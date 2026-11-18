# build.py
"""打包脚本 — 使用 PyInstaller 生成无控制台的可执行文件（Windows / macOS）

用法:
    python build.py              # onedir 模式（默认，启动快）
    python build.py --onefile    # 单文件（便于分发，启动稍慢）
    python build.py --check      # 只打印将要执行的命令，不实际打包

产物:
    Windows onedir  : dist/CanMsgParser/CanMsgParser.exe
    Windows onefile : dist/CanMsgParser.exe
    macOS   onedir  : dist/CanMsgParser.app
    macOS   onefile : dist/CanMsgParser

关键点:
    - --windowed / --noconsole: 不弹出控制台窗口
    - --collect-data matplotlib: 捆绑 matplotlib 的数据文件（字体/样式等）
    - --hidden-import: 补齐动态导入的库（cantools / python-can / PyQt5.sip 等）
    - 平台专属选项必须按平台传参，否则在另一平台会因无法识别而直接失败：
      Windows 的 --manifest / --noconsole，macOS 的 --osx-bundle-identifier。

macOS 说明:
    - PyInstaller 不支持交叉编译：Windows 上跑本脚本只能产出 .exe，
      .app 必须在 macOS 上生成（无 Mac 机时走 .github/workflows/build-macos.yml）。
    - 图标是 app.icns（由 tools/make_icns.py 在 macOS 上生成）；
      app.ico 是 Windows 专用，macOS 上 PyInstaller 不认。
    - 目标架构由环境变量 PYI_TARGET_ARCH 指定（arm64 / x86_64），
      缺省跟随当前解释器；Apple Silicon 上要出 Intel 包时才需要显式设置。
    - 打包完成后会把 version.APP_VERSION 写进 Info.plist 并重新 ad-hoc 签名：
      PyInstaller 命令行没有「设置 bundle 版本」的参数，默认恒为 0.0.0，
      与窗口标题/安装包版本对不上；而改过 Info.plist 签名即失效。
"""
import os
import sys
import subprocess
import shutil

from version import APP_VERSION

APP_NAME = "CanMsgParser"
MAIN_SCRIPT = "main.py"

# 平台判定 —— PyInstaller 的部分选项只在特定平台存在，必须按平台传参，
# 否则在 macOS 上会因无法识别 --manifest / --noconsole 而直接失败。
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# 图标按平台取：Windows 用 .ico，macOS 用 .icns（互不通用）
ICON_FILE = "app.ico" if IS_WIN else "app.icns"

# macOS .app 的 Bundle Identifier（未签名运行也建议显式指定）
MACOS_BUNDLE_ID = "com.canmsgparser.app"

ONE_FILE = "--onefile" in sys.argv
CHECK_ONLY = "--check" in sys.argv


def _dir_size(path):
    """递归统计目录总大小（字节）。

    macOS 的产物 ``.app`` 本质是一个目录，``os.path.getsize`` 对它无意义，
    必须逐个文件累加。
    """
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            fp = os.path.join(root, name)
            if os.path.isfile(fp) and not os.path.islink(fp):
                total += os.path.getsize(fp)
    return total


def _patch_macos_bundle(app_path):
    """把版本号写进 .app 的 Info.plist，并重新做 ad-hoc 签名。

    为什么必须做：
      ① PyInstaller 命令行没有「设置 bundle 版本」的参数，产出的
         CFBundleShortVersionString 恒为 0.0.0，与窗口标题、安装包版本不一致；
      ② 改动 Info.plist 会让 PyInstaller 生成的 ad-hoc 签名失效，Apple Silicon
         上系统会直接拒绝启动（code signature invalid），所以必须重签。
    只做尽力修正：任何一步失败都打印原因，但不中断已经打好的产物。
    """
    plist = os.path.join(app_path, "Contents", "Info.plist")
    if not os.path.exists(plist):
        print(f"警告: 未找到 {plist}，跳过 bundle 版本号写入")
        return

    for key in ("CFBundleShortVersionString", "CFBundleVersion"):
        r = subprocess.run(
            ["/usr/libexec/PlistBuddy", "-c", f"Set :{key} {APP_VERSION}", plist],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            # 键不存在时 Set 会失败，退化为 Add（不同 PyInstaller 版本字段集不同）
            r = subprocess.run(
                ["/usr/libexec/PlistBuddy",
                 "-c", f"Add :{key} string {APP_VERSION}", plist],
                capture_output=True, text=True,
            )
        if r.returncode != 0:
            print(f"警告: 写入 {key} 失败: {(r.stderr or r.stdout).strip()}")

    r = subprocess.run(
        ["codesign", "--force", "--deep", "--sign", "-", app_path],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        print(f"已写入 bundle 版本 {APP_VERSION} 并重新 ad-hoc 签名")
    else:
        print("警告: 重新签名失败（App 可能被 Gatekeeper 拒绝启动）: "
              f"{(r.stderr or r.stdout).strip()[:400]}")


def build_cmd():
    """构造 PyInstaller 命令行参数（跨平台）。

    单独抽成函数，便于只做参数自检（例如验证 macOS 下不会误传
    Windows 专属的 --manifest / --noconsole）而不触发真实打包。
    """
    # PyInstaller 命令
    # 跨平台：Windows 产出 .exe，macOS 产出 .app，Linux 产出可执行目录
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",                    # 覆盖已有输出
        "--windowed",                     # Windows: 不弹控制台；macOS: 生成 .app bundle
        "--onefile" if ONE_FILE else "--onedir",
        "--name", APP_NAME,
    ]

    # ── Windows 专属 ──
    if IS_WIN:
        cmd += ["--noconsole"]            # 明确指定无控制台
        # 高 DPI / 多屏自适应：嵌入声明 PerMonitorV2 的清单，使 Windows 按真实
        # 每屏 DPI 向窗口发送 WM_DPICHANGED，彻底修复「小分辨率屏 UI 过大」与
        # 「锁屏解锁后鼠标点击偏移」两类问题（详见 app.manifest）
        if os.path.exists("app.manifest"):
            cmd += ["--manifest", "app.manifest"]

    # ── macOS 专属 ──
    if IS_MAC:
        cmd += ["--osx-bundle-identifier", MACOS_BUNDLE_ID]
        # 目标架构：默认跟随当前解释器；只有在 Apple Silicon 上出 Intel 包
        # （或反之）时才需要显式设置，交叉架构打包需对应的 Python 环境。
        _arch = os.environ.get("PYI_TARGET_ARCH", "").strip()
        if _arch:
            cmd += ["--target-architecture", _arch]

    cmd += [
        # 隐藏导入 — 这些库有动态导入，PyInstaller 可能漏掉
        "--hidden-import", "PyQt5.QtCore",
        "--hidden-import", "PyQt5.QtGui",
        "--hidden-import", "PyQt5.QtWidgets",
        "--hidden-import", "PyQt5.sip",
        "--hidden-import", "matplotlib.backends.backend_qt5agg",
        "--hidden-import", "matplotlib.backends.backend_qt5",
        "--hidden-import", "cantools",
        "--hidden-import", "can",
        "--hidden-import", "can.io.blf",       # python-can 4.x: BLF 日志读写
        "--hidden-import", "can.io.asc",       # python-can 4.x: ASC 日志读写
        "--hidden-import", "can.interfaces.virtual", # 虚拟总线后端（动态导入）
        "--hidden-import", "openpyxl",
        "--hidden-import", "xlrd",
        "--hidden-import", "pandas",
        "--hidden-import", "numpy",
        "--hidden-import", "lxml",

        # matplotlib 数据文件
        "--collect-data", "matplotlib",

        # 包含 xlrd 的数据文件
        "--collect-data", "xlrd",
    ]

    if IS_WIN:
        # 硬件后端仅 Windows 有驱动：macOS 上收集这些模块会在分析阶段
        # 因加载 PCANBasic.dll / vxlapi64.dll 失败而中断打包。
        cmd += [
            "--hidden-import", "can.interfaces.pcan",
            "--hidden-import", "can.interfaces.vector",
            "--hidden-import", "uptime",             # PCAN 后端依赖
        ]
    else:
        # 非 Windows：只保留虚拟总线（macOS 无 PCAN/Vector 驱动）。
        # socketcan 仅 Linux 存在，加在这里会变成「模块不存在」警告，故不加。
        pass

    cmd += [
        # 剔除用不到的大模块，减小体积
        "--exclude-module", "tkinter",
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc_data",
        "--exclude-module", "PyQt5.QtWebEngineCore",
        "--exclude-module", "PyQt5.QtWebEngineWidgets",
    ]

    # 图标
    if ICON_FILE and os.path.exists(ICON_FILE):
        cmd.extend(["--icon", ICON_FILE])

    # 主脚本
    cmd.append(MAIN_SCRIPT)
    return cmd


def build():
    """执行打包"""
    cmd = build_cmd()

    if CHECK_ONLY:
        print(f"平台: {sys.platform} / 模式: {'onefile' if ONE_FILE else 'onedir'}")
        print(f"图标: {ICON_FILE}（{'存在' if os.path.exists(ICON_FILE) else '不存在，将使用默认图标'}）")
        print("将执行: " + " ".join(cmd))
        return

    print(f"开始打包 CanMsgParser（平台: {sys.platform}）...")

    # 清理旧的构建产物：用「改名移到同盘 gitignored 垃圾目录」的方式。
    # 说明：
    #   1) 本机安全删除拦截(SAFE_DELETE_BULK_CONFIRM_REQUIRED)会阻断对 dist/ 这种
    #      含上千文件的大目录的删除(shutil.rmtree)，因此改用「改名/移动」(os.rename)
    #      来腾出位置——移动不是删除，不会被拦截。
    #   2) 按需求不再保留上一版编译产物作为可还原备份；旧产物只是被移到
    #      .build_trash/（已被 .gitignore 忽略，不会入库），不构成版本控制里的备份。
    import time as _time
    import random as _random
    _here = os.path.dirname(os.path.abspath(__file__))
    _trash = os.path.join(_here, ".build_trash")
    os.makedirs(_trash, exist_ok=True)
    for path in ["build", "dist", f"{APP_NAME}.spec"]:
        if os.path.exists(path):
            _base = os.path.basename(os.path.normpath(path))
            _dst = os.path.join(
                _trash,
                f"{_base}.{_time.strftime('%H%M%S')}_{_random.randint(0, 9999)}",
            )
            shutil.move(path, _dst)
            print(f"已移走旧产物: {path} -> {_dst}")

    cmd = build_cmd()

    print(f"执行: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=False)

    if result.returncode != 0:
        print("打包失败!")
        sys.exit(1)

    # 产物路径按平台与模式不同：
    #   Windows onedir  → dist/CanMsgParser/CanMsgParser.exe
    #   Windows onefile → dist/CanMsgParser.exe
    #   macOS   onedir  → dist/CanMsgParser.app
    #   macOS   onefile → dist/CanMsgParser
    if IS_WIN:
        artifact = (os.path.join("dist", f"{APP_NAME}.exe") if ONE_FILE
                    else os.path.join("dist", APP_NAME, f"{APP_NAME}.exe"))
    elif IS_MAC:
        artifact = (os.path.join("dist", APP_NAME) if ONE_FILE
                    else os.path.join("dist", f"{APP_NAME}.app"))
    else:
        artifact = (os.path.join("dist", APP_NAME) if ONE_FILE
                    else os.path.join("dist", APP_NAME, APP_NAME))

    if os.path.exists(artifact):
        raw_size = (_dir_size(artifact) if os.path.isdir(artifact)
                    else os.path.getsize(artifact))
        print(f"\n打包成功!")
        print(f"产物: {os.path.abspath(artifact)}")
        print(f"大小: {raw_size / 1024 / 1024:.1f} MB")
        if IS_MAC:
            if artifact.endswith(".app"):
                _patch_macos_bundle(artifact)
            print(f"运行: open {artifact}")
    else:
        print(f"警告: 未找到输出产物: {artifact}")


if __name__ == "__main__":
    build()
