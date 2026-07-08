# utils/dependency_check.py
"""启动前依赖库检测"""
import importlib
import subprocess
import sys
from PyQt5.QtWidgets import QMessageBox, QApplication


# 必需的依赖库: (import名, pip包名)
REQUIRED_PACKAGES = [
    ("PyQt5", "PyQt5"),
    ("matplotlib", "matplotlib"),
    ("cantools", "cantools"),
    ("can", "python-can"),
    ("pandas", "pandas"),
    ("numpy", "numpy"),
    ("openpyxl", "openpyxl"),
    ("xlrd", "xlrd==1.2.0"),  # 固定 1.2.0 支持 .xls
    ("lxml", "lxml"),
]


def check_dependencies() -> list[tuple[str, str]]:
    """检查所有必需依赖库是否已安装。

    Returns:
        缺失的库列表 [(import名, pip安装名), ...]
    """
    missing = []
    for import_name, pip_name in REQUIRED_PACKAGES:
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing.append((import_name, pip_name))
    return missing


def install_package(pip_name: str) -> bool:
    """使用 pip 安装指定包。

    Returns:
        True 表示安装成功
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", pip_name],
            capture_output=True,
            text=True,
            timeout=300,  # 5分钟超时
        )
        return result.returncode == 0
    except Exception:
        return False


def ensure_dependencies(app: QApplication) -> bool:
    """确保所有依赖库已安装。如果缺失，弹窗询问是否安装。

    Args:
        app: QApplication 实例（用于显示弹窗）

    Returns:
        True 表示所有依赖已就绪，可以继续启动
        False 表示依赖缺失且用户拒绝安装，应退出
    """
    missing = check_dependencies()

    if not missing:
        return True

    # 构建缺失库提示
    missing_names = [f"  • {imp} (pip install {pip})" for imp, pip in missing]
    message = (
        f"检测到以下 {len(missing)} 个必需依赖库缺失:\n\n"
        + "\n".join(missing_names)
        + "\n\n是否立即自动安装？（将调用 pip install）"
    )

    reply = QMessageBox.question(
        None,
        "依赖库缺失",
        message,
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.Yes,
    )

    if reply != QMessageBox.Yes:
        return False

    # 逐个安装
    success = []
    failed = []
    for imp_name, pip_name in missing:
        # 显示安装进度
        QMessageBox.information(
            None,
            "正在安装",
            f"正在安装 {pip_name} ...\n请稍候，这可能需要几分钟。",
        )

        if install_package(pip_name):
            # 验证安装
            try:
                importlib.import_module(imp_name)
                success.append(pip_name)
            except ImportError:
                failed.append(pip_name)
        else:
            failed.append(pip_name)

    if failed:
        QMessageBox.critical(
            None,
            "安装失败",
            f"以下库安装失败:\n  • " + "\n  • ".join(failed)
            + "\n\n请手动安装: pip install " + " ".join(failed),
        )
        return False

    QMessageBox.information(
        None,
        "安装完成",
        f"成功安装 {len(success)} 个依赖库。\n应用将重新启动。",
    )
    return True
