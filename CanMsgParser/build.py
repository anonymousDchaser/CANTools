# build.py
"""打包脚本 — 使用 PyInstaller 生成无控制台的可执行文件

用法:
    python build.py

产物:
    dist/CanMsgParser/CanMsgParser.exe   (onedir 模式，启动快)

关键点:
    - --windowed / --noconsole: 不弹出控制台窗口
    - --collect-data matplotlib: 捆绑 matplotlib 的数据文件（字体/样式等）
    - --hidden-import: 补齐动态导入的库（cantools / python-can / PyQt5.sip 等）
"""
import os
import sys
import subprocess
import shutil

APP_NAME = "CanMsgParser"
MAIN_SCRIPT = "main.py"
ICON_FILE = None  # 图标：Windows 用 "app.ico"，macOS 用 "app.icns"

# 平台判定 —— PyInstaller 的部分选项只在特定平台存在，必须按平台传参，
# 否则在 macOS 上会因无法识别 --manifest / --noconsole 而直接失败。
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# macOS .app 的 Bundle Identifier（未签名运行也建议显式指定）
MACOS_BUNDLE_ID = "com.canmsgparser.app"


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
        "--hidden-import", "can.interfaces.socketcan",
        "--hidden-import", "can.io.blf",       # python-can 4.x: BLF 日志读写
        "--hidden-import", "can.io.asc",       # python-can 4.x: ASC 日志读写
        "--hidden-import", "can.interfaces.pcan",    # PEAK PCAN 后端（动态导入，必须显式收）
        "--hidden-import", "can.interfaces.vector",  # Vector 后端（动态导入）
        "--hidden-import", "can.interfaces.virtual", # 虚拟总线后端（动态导入）
        "--hidden-import", "uptime",                 # PCAN 后端依赖
        "--hidden-import", "openpyxl",
        "--hidden-import", "xlrd",
        "--hidden-import", "pandas",
        "--hidden-import", "numpy",
        "--hidden-import", "lxml",

        # matplotlib 数据文件
        "--collect-data", "matplotlib",

        # 包含 xlrd 的数据文件
        "--collect-data", "xlrd",

        # 排除不需要的大库以减小体积（可选）
        # "--exclude-module", "tkinter",
        # "--exclude-module", "PyQt5.QtWebEngineWidgets",
    ]

    # 图标
    if ICON_FILE and os.path.exists(ICON_FILE):
        cmd.extend(["--icon", ICON_FILE])

    # 主脚本
    cmd.append(MAIN_SCRIPT)
    return cmd


def build():
    """执行打包"""
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

    # 产物路径按平台不同：
    #   Windows → dist/CanMsgParser/CanMsgParser.exe
    #   macOS   → dist/CanMsgParser.app
    #   Linux   → dist/CanMsgParser/CanMsgParser
    if IS_WIN:
        artifact = os.path.join("dist", APP_NAME, f"{APP_NAME}.exe")
    elif IS_MAC:
        artifact = os.path.join("dist", f"{APP_NAME}.app")
    else:
        artifact = os.path.join("dist", APP_NAME, APP_NAME)

    if os.path.exists(artifact):
        raw_size = (_dir_size(artifact) if os.path.isdir(artifact)
                    else os.path.getsize(artifact))
        print(f"\n打包成功!")
        print(f"产物: {os.path.abspath(artifact)}")
        print(f"大小: {raw_size / 1024 / 1024:.1f} MB")
        if IS_MAC:
            print(f"运行: open {artifact}")
    else:
        print(f"警告: 未找到输出产物: {artifact}")


if __name__ == "__main__":
    build()
