# tests/test_build_platform.py
"""打包脚本的跨平台参数自检。

PyInstaller 不支持交叉编译，Windows 上无法真实产出 .app，但「参数是否正确
按平台区分」是可以验证的：把平台开关临时改掉再构造命令即可。这一层测试能
挡住「在 mac 上传了 --manifest / --noconsole 导致打包直接失败」这类低级错误。
"""
import importlib.util
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _load_build():
    spec = importlib.util.spec_from_file_location(
        "_cmp_build", os.path.join(_ROOT, "build.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cmd_for(platform: str) -> list:
    """在指定平台下构造 PyInstaller 命令。"""
    mod = _load_build()
    mod.IS_WIN = platform == "win32"
    mod.IS_MAC = platform == "darwin"
    mod.ICON_FILE = "app.ico" if platform == "win32" else "app.icns"
    return mod.build_cmd()


def test_mac_cmd_has_no_windows_only_flags():
    cmd = _cmd_for("darwin")
    joined = " ".join(cmd)
    assert "--osx-bundle-identifier" in cmd, "macOS 必须设置 bundle identifier"
    assert "com.canmsgparser.app" in cmd
    assert "--manifest" not in cmd, "macOS 不支持 --manifest"
    assert "--noconsole" not in cmd, "macOS 不支持 --noconsole（用 --windowed）"
    # 硬件后端只有 Windows 有驱动：macOS 上收集会在分析阶段因缺 dll 中断
    assert "can.interfaces.pcan" not in joined, "macOS 不应收集 PEAK 后端"
    assert "can.interfaces.vector" not in joined, "macOS 不应收集 Vector 后端"
    assert "uptime" not in joined, "macOS 不需要 uptime（PEAK 专用）"
    assert "can.interfaces.virtual" in joined, "虚拟总线是跨平台的，必须保留"
    assert "--windowed" in cmd
    print("    OK: macOS 命令不含 Windows 专属选项")


def test_win_cmd_has_windows_flags():
    cmd = _cmd_for("win32")
    joined = " ".join(cmd)
    assert "--manifest" in cmd, "Windows 需要 app.manifest（PerMonitorV2）"
    assert "--noconsole" in cmd
    assert "--osx-bundle-identifier" not in cmd, "Windows 不支持该选项"
    assert "can.interfaces.pcan" in joined
    assert "uptime" in joined
    print("    OK: Windows 命令含 manifest / noconsole / 硬件后端")


def test_version_single_source():
    """版本号应只有一处来源：version.APP_VERSION。"""
    from version import APP_VERSION
    assert APP_VERSION.count(".") == 2, f"版本号格式异常: {APP_VERSION}"

    # installer.iss 的默认值必须能被 /D 覆盖（#ifndef 包裹）
    iss = open(os.path.join(_ROOT, "installer", "installer.iss"),
               encoding="utf-8").read()
    assert "#ifndef MyAppVersion" in iss, "iss 的版本需允许 /D 覆盖"
    assert APP_VERSION in iss, "iss 兜底版本应与 version.py 一致"

    # 窗口标题不得再硬编码版本
    mw = open(os.path.join(_ROOT, "main_window.py"), encoding="utf-8").read()
    assert "v{APP_VERSION}" in mw or "APP_VERSION" in mw, \
        "主窗口标题应引用 APP_VERSION"
    print(f"    OK: 版本单一来源 {APP_VERSION}（iss / 窗口标题均已对齐）")


def test_installer_version_arg():
    """build_installer 必须把版本号以 /D 形式传给 ISCC。"""
    src = open(os.path.join(_ROOT, "build_installer.py"), encoding="utf-8").read()
    assert "/DMyAppVersion=" in src, "安装包版本需通过 /D 覆盖 iss"
    assert "hdiutil" in src and "ditto" in src, "macOS 需 hdiutil + ditto 出产物"
    print("    OK: 分发脚本按平台出 Setup / dmg / zip")


if __name__ == "__main__":
    test_mac_cmd_has_no_windows_only_flags()
    test_win_cmd_has_windows_flags()
    test_version_single_source()
    test_installer_version_arg()
    print("BUILD PLATFORM TESTS PASSED")
