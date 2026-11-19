# -*- coding: utf-8 -*-
"""编译分发产物：Windows 安装包 / macOS 磁盘映像，并自动补一个便携 zip。

用法::

    python build_installer.py            # 用现有 dist/ 打包（安装包 + 便携 zip）
    python build_installer.py --build    # 先跑 build.py 再打包（一条龙）
    python build_installer.py --check    # 只检查环境与版本，不编译
    python build_installer.py --no-zip   # 只出安装包，跳过便携 zip

产物::

    Windows : installer/output/CanMsgParser_Setup_<版本>.exe
              installer/output/CanMsgParser-<版本>-win64-portable.zip
    macOS   : installer/output/CanMsgParser-<版本>-macos-<架构>.dmg
              installer/output/CanMsgParser-<版本>-macos-<架构>-portable.zip

两点设计说明:

- **便携 zip 由本脚本产出**。它是「免安装直接跑」的分发形态，与安装包同一批
  发布；手工补打容易漏掉版本号，所以并进同一条流水线。
- **不能在 Windows 上产出 macOS 产物**。PyInstaller 早已不支持交叉编译，
  macOS 的 ``.app`` / ``.dmg`` 必须在 macOS 上生成（本机无 Mac 时走
  ``.github/workflows/build-macos.yml`` 的 GitHub Actions runner）。

版本号从 ``version.py`` 的 ``APP_VERSION`` 读取，避免安装包、窗口标题、
dmg 文件名三处各说各话。
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "CanMsgParser"
OUT_DIR = os.path.join(HERE, "installer", "output")
ISS = os.path.join(HERE, "installer", "installer.iss")
TRASH = os.path.join(HERE, ".build_trash")

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

if HERE not in sys.path:
    sys.path.insert(0, HERE)
from version import APP_VERSION  # noqa: E402

# 源产物：Windows/onedir 是目录，macOS 是 .app 包（本质也是目录）
DIST_DIR = os.path.join(HERE, "dist", APP_NAME)
DIST_EXE = os.path.join(DIST_DIR, f"{APP_NAME}.exe")
DIST_APP = os.path.join(HERE, "dist", f"{APP_NAME}.app")

# ISCC 的常见安装位置；也支持用环境变量 INNO_SETUP_ISCC 指定
ISCC_CANDIDATES = [
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
    os.path.expandvars(r"%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"),
    os.path.expandvars(r"%ProgramFiles%\Inno Setup 6\ISCC.exe"),
]


def arch_tag() -> str:
    """产物架构标记：macOS 用 arm64/x86_64，Windows 固定 win64。"""
    if IS_MAC:
        m = platform.machine().lower()
        return "arm64" if m in ("arm64", "aarch64") else "x86_64"
    return "win64"


def find_iscc() -> str | None:
    env = os.environ.get("INNO_SETUP_ISCC", "").strip()
    if env and os.path.exists(env):
        return env
    for cand in ISCC_CANDIDATES:
        if os.path.exists(cand):
            return cand
    return None


def _move_old(paths: list[str]) -> None:
    """把旧产物改名移入 .build_trash（不删除：本机安全策略会拦批量删除）。"""
    os.makedirs(TRASH, exist_ok=True)
    for path in paths:
        if not os.path.exists(path):
            continue
        base = os.path.basename(os.path.normpath(path))
        dst = os.path.join(
            TRASH, f"{base}.{time.strftime('%H%M%S')}_{os.getpid()}"
        )
        try:
            shutil.move(path, dst)
            print(f"已移走旧产物: {path} -> {dst}")
        except Exception as e:  # noqa: BLE001
            print(f"警告: 无法移走 {path}（{e}），继续尝试覆盖")


def make_setup_windows() -> str | None:
    """用 Inno Setup 生成安装包；返回产物路径（失败返回 None）。"""
    if not os.path.isdir(DIST_DIR):
        print(f"    未找到 dist 目录: {DIST_DIR}，请先执行 python build.py")
        return None
    iscc = find_iscc()
    if not iscc:
        print("    未找到 Inno Setup（ISCC.exe）。安装 Inno Setup 6，或设置环境变量 "
              "INNO_SETUP_ISCC 指向 ISCC.exe")
        return None
    if not os.path.exists(ISS):
        print(f"    未找到安装脚本: {ISS}")
        return None
    # 版本号通过 /D 覆盖 iss 里的 #define，避免两处版本各说各话
    cmd = [iscc, f"/DMyAppVersion={APP_VERSION}", ISS]
    print("    " + " ".join(cmd))
    rc = subprocess.run(cmd).returncode
    if rc != 0:
        print("    ISCC 失败，安装包未生成")
        return None
    out = os.path.join(OUT_DIR, f"{APP_NAME}_Setup_{APP_VERSION}.exe")
    return out if os.path.exists(out) else None



def _sign_identity() -> str:
    """分发签名身份：CI 由 MAC_SIGN_IDENTITY 注入，本地未设置时为空。"""
    return os.environ.get("MAC_SIGN_IDENTITY", "").strip()


def _codesign_cmd(app_path) -> list:
    """组装 codesign 命令。

    设了 MAC_SIGN_IDENTITY（Developer ID Application）就走真签名：
    --options runtime 启用强化运行时、--timestamp 带可信时间戳，这两项是
    Apple 公证的前置条件；否则退回 ad-hoc（"-"），保证本机双击能打开。
    """
    identity = _sign_identity() or "-"
    cmd = ["codesign", "--force", "--deep", "--sign", identity]
    if identity != "-":
        cmd += ["--options", "runtime", "--timestamp"]
    cmd.append(app_path)
    return cmd
def make_dmg_mac() -> str | None:
    """用 hdiutil 生成 dmg（含 /Applications 快捷方式，拖拽安装）。"""
    if not os.path.isdir(DIST_APP):
        print(f"    未找到 .app: {DIST_APP}，请先执行 python build.py")
        return None
    hdiutil = shutil.which("hdiutil")
    if not hdiutil:
        print("    未找到 hdiutil（非 macOS 环境？），跳过 dmg")
        return None

    tag = arch_tag()
    out = os.path.join(OUT_DIR, f"{APP_NAME}-{APP_VERSION}-macos-{tag}.dmg")
    if os.path.exists(out):
        os.remove(out)

    # 暂存目录：.app + 指向 /Applications 的软链接，用户打开 dmg 后直接拖入
    stage = os.path.join(HERE, "installer", "_dmg_stage")
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    os.makedirs(stage)
    subprocess.run(["cp", "-R", DIST_APP, stage], check=True)
    os.symlink("/Applications", os.path.join(stage, "Applications"))

    # 签名：未签名的 .app 在新版 macOS 上会被报「已损坏」。有 Developer ID
    # 证书时用真签名（公证前置条件），否则 ad-hoc 兜底保证本机可打开。
    subprocess.run(
        _codesign_cmd(os.path.join(stage, f"{APP_NAME}.app")),
        check=False,
    )

    rc = subprocess.run([
        hdiutil, "create", "-volname", APP_NAME,
        "-srcfolder", stage, "-ov", "-format", "UDZO", out,
    ]).returncode
    shutil.rmtree(stage, ignore_errors=True)
    if rc != 0:
        print("    hdiutil 失败，dmg 未生成")
        return None
    return out


def make_portable_zip() -> str | None:
    """便携 zip：Windows 用 zipfile 打包目录；macOS 用 ditto 保留符号链接与权限。"""
    tag = arch_tag()
    out = os.path.join(
        OUT_DIR, f"{APP_NAME}-{APP_VERSION}-{tag}-portable.zip"
    )
    if IS_MAC:
        src = DIST_APP
        if not os.path.isdir(src):
            print(f"    未找到 .app: {src}，跳过便携 zip")
            return None
        if os.path.exists(out):
            os.remove(out)
        # ditto 保留资源分支/符号链接/可执行位；zipfile 会破坏 .app 结构
        rc = subprocess.run(
            ["ditto", "-c", "-k", "--keepParent", src, out]
        ).returncode
        return out if rc == 0 else None

    src = DIST_DIR
    if not os.path.isdir(src):
        print(f"    未找到 dist 目录: {src}，跳过便携 zip")
        return None
    if os.path.exists(out):
        os.remove(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(src):
            for name in files:
                fp = os.path.join(root, name)
                zf.write(fp, os.path.relpath(fp, os.path.dirname(src)))
    return out


def _report(paths: list[str | None]) -> None:
    print("")
    for p in paths:
        if not p:
            continue
        if os.path.exists(p):
            print(f"  {os.path.basename(p):52s} {os.path.getsize(p) / 1048576:7.1f} MB")
        else:
            print(f"  警告: 未找到产物 {p}")


def main() -> int:
    if "--build" in sys.argv:
        rc = subprocess.run(
            [sys.executable, os.path.join(HERE, "build.py")]
        ).returncode
        if rc != 0:
            print("build.py 失败，中止")
            return rc

    print(f"打包分发产物: {APP_NAME} {APP_VERSION}（平台: {sys.platform}）")

    if "--check" in sys.argv:
        print(f"  版本        : {APP_VERSION}")
        print(f"  架构标记    : {arch_tag()}")
        print(f"  源产物      : {DIST_APP if IS_MAC else DIST_DIR}")
        print(f"  存在        : {os.path.isdir(DIST_APP if IS_MAC else DIST_DIR)}")
        print(f"  Inno Setup  : {find_iscc() if IS_WIN else '不适用（macOS 用 hdiutil）'}")
        print(f"  输出目录    : {OUT_DIR}")
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)
    _move_old([
        os.path.join(OUT_DIR, f"{APP_NAME}_Setup_{APP_VERSION}.exe"),
        os.path.join(OUT_DIR, f"{APP_NAME}-{APP_VERSION}-{arch_tag()}-portable.zip"),
        os.path.join(OUT_DIR, f"{APP_NAME}-{APP_VERSION}-macos-{arch_tag()}.dmg"),
    ])

    installer = make_setup_windows() if IS_WIN else make_dmg_mac()
    zips = None
    if "--no-zip" not in sys.argv:
        zips = make_portable_zip()

    print("")
    if installer:
        print("分发产物已生成:")
    else:
        print("安装包未生成（见上方原因），其余产物:")
    _report([installer, zips])

    if IS_WIN and not installer:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
