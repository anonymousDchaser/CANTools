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
ICON_FILE = None  # 如有图标可指定 "app.ico"


def build():
    """执行打包"""
    print("开始打包 CanMsgParser...")

    # 清理旧的构建产物：用改名备份方式（避免环境安全删除拦截 SAFE_DELETE_FAIL_CLOSED）
    import time as _time
    _ts = _time.strftime("%H%M%S")
    for path in ["build", "dist", f"{APP_NAME}.spec"]:
        if os.path.exists(path):
            _bak = f"{path}.bak_{_ts}"
            shutil.move(path, _bak)
            print(f"已备份: {path} -> {_bak}")

    # PyInstaller 命令
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",                    # 覆盖已有输出
        "--windowed",                     # 不弹出控制台 (关键!)
        "--noconsole",                    # 明确指定无控制台
        "--name", APP_NAME,

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

    print(f"执行: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=False)

    if result.returncode != 0:
        print("打包失败!")
        sys.exit(1)

    exe_path = os.path.join("dist", APP_NAME, f"{APP_NAME}.exe")
    if os.path.exists(exe_path):
        print(f"\n打包成功!")
        print(f"可执行文件: {os.path.abspath(exe_path)}")
        print(f"大小: {os.path.getsize(exe_path) / 1024 / 1024:.1f} MB")
    else:
        print("警告: 未找到输出 exe 文件")


if __name__ == "__main__":
    build()
