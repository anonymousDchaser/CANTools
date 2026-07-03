# main.py
"""CAN 报文分析工具 — 应用入口"""
import sys
import time
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
from main_window import MainWindow
from widgets.splash_screen import SplashScreen


def main():
    # 高 DPI 支持
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("CanMsgParser")

    # 全局暗色主题样式（补充 MainWindow 自身 QSS 未覆盖的应用级控件）
    app.setStyleSheet("""
        /* ── 全局基础 ── */
        QMainWindow { background-color: #1e1e2e; color: #e0e0e0; font-family: "Microsoft YaHei", "Segoe UI", sans-serif; }
        QWidget { font-family: "Microsoft YaHei", "Segoe UI", sans-serif; }

        /* ── 菜单栏 ── */
        QMenuBar { background-color: #252535; color: #e0e0e0; border-bottom: 1px solid #3a3a4e; padding: 2px 0; font-size: 13px; }
        QMenuBar::item { padding: 6px 14px; margin: 2px 1px; border-radius: 4px; }
        QMenuBar::item:selected { background-color: #4fc3f7; color: #1e1e2e; }

        /* ── 下拉菜单 ── */
        QMenu { background-color: #252535; color: #e0e0e0; border: 1px solid #3a3a4e; border-radius: 6px; padding: 4px 0; }
        QMenu::item { padding: 8px 32px 8px 20px; border-radius: 3px; margin: 1px 4px; }
        QMenu::item:selected { background-color: #4fc3f7; color: #1e1e2e; }
        QMenu::separator { height: 1px; background-color: #3a3a4e; margin: 4px 12px; }

        /* ── 状态栏 ── */
        QStatusBar { background-color: #252535; color: #9090a0; border-top: 1px solid #3a3a4e; font-size: 12px; padding: 4px 8px; }

        /* ── Tab 页签 ── */
        QTabWidget::pane { border: 1px solid #3a3a4e; background-color: #1e1e2e; border-radius: 0 0 6px 6px; }
        QTabBar::tab { background-color: #252535; color: #9090a0; padding: 8px 20px; border: 1px solid #3a3a4e; border-bottom: none; margin-right: 2px; border-radius: 6px 6px 0 0; font-size: 13px; }
        QTabBar::tab:selected { background-color: #1e1e2e; color: #4fc3f7; border-bottom: 2px solid #4fc3f7; }
        QTabBar::tab:hover:!selected { background-color: #2a2a3e; color: #e0e0e0; }

        /* ── 分割器手柄 ── */
        QSplitter::handle { background-color: #3a3a4e; }
        QSplitter::handle:horizontal { width: 3px; }
        QSplitter::handle:hover { background-color: #4fc3f7; }

        /* ── 进度条 ── */
        QProgressBar { border: 1px solid #3a3a4e; border-radius: 4px; text-align: center; background-color: #252535; color: #e0e0e0; font-size: 11px; min-height: 18px; }
        QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4fc3f7, stop:1 #29b6f6); border-radius: 3px; }

        /* ── 滚动条（垂直） ── */
        QScrollBar:vertical { background: #1e1e2e; width: 10px; border: none; }
        QScrollBar::handle:vertical { background: #3a3a4e; border-radius: 5px; min-height: 20px; }
        QScrollBar::handle:vertical:hover { background: #4fc3f7; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

        /* ── 滚动条（水平） ── */
        QScrollBar:horizontal { background: #1e1e2e; height: 10px; border: none; }
        QScrollBar::handle:horizontal { background: #3a3a4e; border-radius: 5px; min-width: 20px; }
        QScrollBar::handle:horizontal:hover { background: #4fc3f7; }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

        /* ── 提示框 ── */
        QToolTip { background-color: #252535; color: #e0e0e0; border: 1px solid #4fc3f7; padding: 6px 10px; border-radius: 4px; font-size: 12px; }

        /* ── 输入框通用 ── */
        QLineEdit { background-color: #2a2a3e; color: #e0e0e0; border: 1px solid #3a3a4e; border-radius: 4px; padding: 6px 10px; font-size: 13px; selection-background-color: #1e3a5a; }
        QLineEdit:focus { border-color: #4fc3f7; }
        QLineEdit::placeholder { color: #666680; }

        /* ── 下拉框通用 ── */
        QComboBox { background-color: #2a2a3e; color: #e0e0e0; border: 1px solid #3a3a4e; border-radius: 4px; padding: 6px 12px; font-size: 13px; }
        QComboBox:hover { border-color: #4fc3f7; }
        QComboBox::drop-down { border: none; width: 24px; }
        QComboBox QAbstractItemView { background-color: #252535; color: #e0e0e0; selection-background-color: #1e3a5a; selection-color: #4fc3f7; border: 1px solid #3a3a4e; outline: none; }

        /* ── 按钮通用 ── */
        QPushButton { background-color: #3a3a4e; color: #e0e0e0; border: 1px solid #4a4a5e; border-radius: 4px; padding: 6px 14px; min-height: 28px; font-size: 13px; font-weight: 500; }
        QPushButton:hover { background-color: #4a4a5e; border-color: #4fc3f7; }
        QPushButton:pressed { background-color: #2a2a3e; }
        QPushButton:disabled { color: #555560; background-color: #2a2a3e; border-color: #3a3a4e; }

        /* ── 主要按钮 ── */
        QPushButton[class="primary"] { background-color: #4fc3f7; color: #1e1e2e; border: none; font-weight: bold; }
        QPushButton[class="primary"]:hover { background-color: #29b6f6; }
        QPushButton[class="primary"]:pressed { background-color: #0288d1; }

        /* ── 树形控件 ── */
        QTreeWidget { background-color: #1e1e2e; alternate-background-color: #252535; color: #e0e0e0; border: 1px solid #3a3a4e; border-radius: 4px; outline: none; font-size: 13px; }
        QTreeWidget::item { padding: 4px 6px; }
        QTreeWidget::item:selected { background-color: #1e3a5a; color: #4fc3f7; }
        QTreeWidget::item:hover { background-color: #2a2a4e; }
        QHeaderView::section { background-color: #2a2a3e; color: #4fc3f7; border: none; border-right: 1px solid #3a3a4e; border-bottom: 2px solid #4fc3f7; padding: 6px 8px; font-weight: bold; font-size: 12px; }
        QHeaderView::section:hover { background-color: #3a3a4e; }

        /* ── 列表控件 ── */
        QListWidget { background-color: #1e1e2e; alternate-background-color: #252535; color: #e0e0e0; border: 1px solid #3a3a4e; border-radius: 4px; outline: none; padding: 4px; }
        QListWidget::item { padding: 5px 8px; }
        QListWidget::item:selected { background-color: #1e3a5a; color: #4fc3f7; }
        QListWidget::item:hover { background-color: #2a2a4e; }

        /* ── 复选框 ── */
        QCheckBox { color: #e0e0e0; spacing: 8px; }
        QCheckBox::indicator { width: 16px; height: 16px; border: 2px solid #3a3a4e; border-radius: 3px; background-color: #2a2a3e; }
        QCheckBox::indicator:checked { background-color: #4fc3f7; border-color: #4fc3f7; }
        QCheckBox::indicator:hover { border-color: #4fc3f7; }

        /* ── 标签颜色层级 ── */
        QLabel { color: #e0e0e0; }

        /* ── 消息框 ── */
        QMessageBox { background-color: #1e1e2e; }
        QMessageBox QLabel { color: #e0e0e0; }

        /* ── 图形视图 ── */
        QGraphicsView { background-color: #1e1e2e; border: 1px solid #3a3a4e; border-radius: 4px; }

        /* ── 工具栏 ── */
        QToolBar { background-color: #1e1e2e; border: none; spacing: 4px; padding: 2px; }
        QToolButton { background-color: #3a3a4e; color: #e0e0e0; border: 1px solid #4a4a5e; border-radius: 3px; padding: 4px 8px; }
        QToolButton:hover { background-color: #4a4a5e; border-color: #4fc3f7; }
    """)

    # ---- 启动画面 ----
    splash = SplashScreen()
    splash.show()
    app.processEvents()

    # 分步加载，同时更新进度
    load_steps = [
        (10, "正在加载核心模块..."),
        (25, "正在初始化 DBC 解析器..."),
        (40, "正在加载日志解析器..."),
        (55, "正在初始化绘图引擎..."),
        (70, "正在加载 UI 组件..."),
        (85, "正在恢复配置..."),
    ]

    main_window = None
    for progress, status in load_steps:
        splash.update_progress(progress, status)
        time.sleep(0.15)          # 短暂延迟让用户看到进度动画
        if progress == 85:
            main_window = MainWindow()

    # 确保主窗口已创建
    if main_window is None:
        main_window = MainWindow()

    splash.update_progress(100, "启动完成")
    time.sleep(0.2)

    main_window.show()
    splash.finish(main_window)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
