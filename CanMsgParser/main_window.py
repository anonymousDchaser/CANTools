# main_window.py
"""主窗口：Tab 布局（曲线图含信号树子面板 + 报文表格 + 位图查看器）+ 菜单栏 + 状态栏 + 专业暗色主题"""
import json
import os

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTabWidget, QMenuBar, QMenu, QAction, QFileDialog, QDockWidget,
    QStatusBar, QProgressBar, QMessageBox, QLabel, QApplication,
    QPushButton, QListWidget, QListWidgetItem, QAbstractItemView,
)
from PyQt5.QtCore import Qt
import pandas as pd
import numpy as np
from core.dbc_parser import parse_dbc
from core.can_data import MessageDef, DecodedSignal
from core.signal_cache import SignalCache
from widgets.plot_widget import PlotWidget
from widgets.connection_status_widget import ConnectionStatusWidget
from widgets.realtime_message_widget import RealtimeMessageWidget
from widgets.signal_group_panel import SignalGroupPanel
from widgets.message_table import MessageTableWidget
from widgets.bit_layout_view import BitLayoutView
from widgets.realtime_monitor_widget import RealtimeMonitorWidget
from widgets.signal_sim_widget import SignalSimWidget
from workers.load_worker import LoadWorker, DecodeWorker
from utils.export_utils import export_chart_image, export_signal_data
from utils.excel_value_loader import load_value_descriptions

# 配置文件路径，用于记住上次加载的 DBC 文件
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".canmsgparser_config.json")


class MainWindow(QMainWindow):
    """CAN 报文分析工具主窗口"""

    # ─── 全局暗色主题样式表（与 main.py 设计系统一致） ───
    _GLOBAL_QSS = """
        /* ── 主窗口 ── */
        QMainWindow {
            background-color: #1e1e2e;
            color: #e0e0e0;
            font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
        }

        /* ── 菜单栏 ── */
        QMenuBar {
            background-color: #252535;
            color: #e0e0e0;
            border-bottom: 1px solid #3a3a4e;
            padding: 2px 0;
            font-size: 13px;
        }
        QMenuBar::item {
            padding: 6px 14px;
            margin: 2px 1px;
            border-radius: 4px;
        }
        QMenuBar::item:selected {
            background-color: #4fc3f7;
            color: #1e1e2e;
        }

        /* ── 下拉菜单 ── */
        QMenu {
            background-color: #252535;
            color: #e0e0e0;
            border: 1px solid #3a3a4e;
            border-radius: 6px;
            padding: 4px 0;
        }
        QMenu::item {
            padding: 8px 32px 8px 20px;
            border-radius: 3px;
            margin: 1px 4px;
        }
        QMenu::item:selected {
            background-color: #4fc3f7;
            color: #1e1e2e;
        }
        QMenu::separator {
            height: 1px;
            background-color: #3a3a4e;
            margin: 4px 12px;
        }

        /* ── 状态栏 ── */
        QStatusBar {
            background-color: #252535;
            color: #9090a0;
            border-top: 1px solid #3a3a4e;
            font-size: 12px;
            padding: 4px 8px;
        }

        /* ── Tab 页签 ── */
        QTabWidget::pane {
            border: 1px solid #3a3a4e;
            background-color: #1e1e2e;
            border-radius: 0 0 6px 6px;
        }
        QTabBar::tab {
            background-color: #252535;
            color: #9090a0;
            padding: 8px 20px;
            border: 1px solid #3a3a4e;
            border-bottom: none;
            margin-right: 2px;
            border-radius: 6px 6px 0 0;
            font-size: 13px;
        }
        QTabBar::tab:selected {
            background-color: #1e1e2e;
            color: #4fc3f7;
            border-bottom: 2px solid #4fc3f7;
        }
        QTabBar::tab:hover:!selected {
            background-color: #2a2a3e;
            color: #e0e0e0;
        }

        /* ── 分割器手柄 ── */
        QSplitter::handle {
            background-color: #3a3a4e;
        }
        QSplitter::handle:horizontal {
            width: 3px;
        }
        QSplitter::handle:hover {
            background-color: #4fc3f7;
        }

        /* ── 进度条 ── */
        QProgressBar {
            border: 1px solid #3a3a4e;
            border-radius: 4px;
            text-align: center;
            background-color: #252535;
            color: #e0e0e0;
            font-size: 11px;
            min-height: 18px;
        }
        QProgressBar::chunk {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4fc3f7, stop:1 #29b6f6);
            border-radius: 3px;
        }

        /* ── 滚动条（垂直） ── */
        QScrollBar:vertical {
            background: #1e1e2e;
            width: 10px;
            border: none;
        }
        QScrollBar::handle:vertical {
            background: #3a3a4e;
            border-radius: 5px;
            min-height: 20px;
        }
        QScrollBar::handle:vertical:hover {
            background: #4fc3f7;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0;
        }

        /* ── 滚动条（水平） ── */
        QScrollBar:horizontal {
            background: #1e1e2e;
            height: 10px;
            border: none;
        }
        QScrollBar::handle:horizontal {
            background: #3a3a4e;
            border-radius: 5px;
            min-width: 20px;
        }
        QScrollBar::handle:horizontal:hover {
            background: #4fc3f7;
        }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            width: 0;
        }

        /* ── 提示框 ── */
        QToolTip {
            background-color: #252535;
            color: #e0e0e0;
            border: 1px solid #4fc3f7;
            padding: 6px 10px;
            border-radius: 4px;
            font-size: 12px;
        }

        /* ── 输入框通用 ── */
        QLineEdit {
            background-color: #2a2a3e;
            color: #e0e0e0;
            border: 1px solid #3a3a4e;
            border-radius: 4px;
            padding: 6px 10px;
            font-size: 13px;
            selection-background-color: #1e3a5a;
        }
        QLineEdit:focus {
            border-color: #4fc3f7;
        }
        QLineEdit::placeholder {
            color: #666680;
        }

        /* ── 按钮通用 ── */
        QPushButton {
            background-color: #3a3a4e;
            color: #e0e0e0;
            border: 1px solid #4a4a5e;
            border-radius: 4px;
            padding: 6px 14px;
            min-height: 28px;
            font-size: 13px;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: #4a4a5e;
            border-color: #4fc3f7;
        }
        QPushButton:pressed {
            background-color: #2a2a3e;
        }
        QPushButton:disabled {
            background-color: #2a2a3e;
            color: #555560;
            border-color: #3a3a4e;
        }

        /* ── 主要按钮 ── */
        QPushButton[class="primary"] {
            background-color: #4fc3f7;
            color: #1e1e2e;
            border: none;
            font-weight: bold;
        }
        QPushButton[class="primary"]:hover {
            background-color: #29b6f6;
        }
        QPushButton[class="primary"]:pressed {
            background-color: #0288d1;
        }

        /* ── 树形控件 ── */
        QTreeWidget {
            background-color: #1e1e2e;
            alternate-background-color: #252535;
            color: #e0e0e0;
            border: 1px solid #3a3a4e;
            border-radius: 4px;
            outline: none;
            font-size: 13px;
        }
        QTreeWidget::item {
            padding: 4px 6px;
        }
        QTreeWidget::item:selected {
            background-color: #1e3a5a;
            color: #4fc3f7;
        }
        QTreeWidget::item:hover {
            background-color: #2a2a4e;
        }
        QHeaderView::section {
            background-color: #2a2a3e;
            color: #4fc3f7;
            border: none;
            border-right: 1px solid #3a3a4e;
            border-bottom: 2px solid #4fc3f7;
            padding: 6px 8px;
            font-weight: bold;
            font-size: 12px;
        }
        QHeaderView::section:hover {
            background-color: #3a3a4e;
        }

        /* ── 消息框 ── */
        QMessageBox {
            background-color: #1e1e2e;
        }
        QMessageBox QLabel {
            color: #e0e0e0;
        }
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CAN 报文分析工具")
        self.setMinimumSize(1280, 720)
        self.resize(1920, 1280)

        # 居中显示
        screen = QApplication.primaryScreen()
        if screen:
            screen_geo = screen.availableGeometry()
            x = (screen_geo.width() - 1920) // 2 + screen_geo.x()
            y = (screen_geo.height() - 1280) // 2 + screen_geo.y()
            self.move(max(0, x), max(0, y))

        # 应用全局样式
        self.setStyleSheet(self._GLOBAL_QSS)

        # ── 数据状态 ──
        self._messages: list[MessageDef] = []
        self._dbc_path: str = ""
        self._excel_path: str = ""  # Excel 值描述文件路径（用于配置持久化）
        self._group_config_path: str = ""  # 分组配置文件路径（用于配置持久化）
        self._excel_value_descriptions: dict = {}  # Excel 加载的值描述
        self._value_descriptions: dict = {}  # {sig_name: {int_val: "描述", ...}}
        self._frame_index: pd.DataFrame | None = None
        self._raw_data: np.ndarray | None = None
        self._cache = SignalCache(max_entries=100)
        self._decoded_signals: list[DecodedSignal] = []
        self._load_worker: LoadWorker | None = None
        self._decode_workers: list[DecodeWorker] = []
        # 曲线图已选信号与解码批次代次（分发驱动，替代原信号树）
        self._curve_signals: set = set()        # {(msg_name, sig_name)}
        self._curve_decode_gen: int = 0         # 避免过期解码批次误绘
        self._dock_positioned: bool = False     # 分组窗仅首次显示时定位一次

        self._setup_ui()
        self._setup_menu()
        self._setup_statusbar()

        # 启用拖拽支持
        self.setAcceptDrops(True)

        # 自动加载上次的 DBC 文件（启动时失败仅写状态栏，不弹窗）
        config = self._load_config()
        last_dbc = config.get("last_dbc_path", "")
        if last_dbc and os.path.exists(last_dbc):
            try:
                self._auto_load_dbc(last_dbc)
            except Exception as e:
                self._statusbar.showMessage(f"自动加载 DBC 失败: {e}")

        # 自动加载上次的 Excel 值描述文件（启动时失败仅写状态栏，不弹窗）
        last_excel = config.get("last_excel_path", "")
        if last_excel and os.path.exists(last_excel):
            try:
                self._auto_load_excel_descriptions(last_excel)
            except Exception as e:
                self._statusbar.showMessage(f"自动加载值描述 Excel 失败: {e}")

        # 自动加载上次的分组配置文件（启动时失败仅写状态栏，不弹窗）
        last_group = config.get("last_group_config_path", "")
        if last_group and os.path.exists(last_group):
            try:
                self._group_panel.load_config_from_path(last_group)
                self._group_config_path = last_group
                self._statusbar.showMessage(f"已自动加载分组配置: {last_group}")
            except Exception:
                pass

    def _setup_ui(self):
        """构建主界面布局：Tab 页（曲线图 / 报文表格 / 模拟上报信号 /
        实时监控 / 位图查看器）+ 可停靠悬浮的「信号分组」窗
        """
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # ── Tab 页（唯一的顶层内容）──
        self._tabs = QTabWidget()

        # ─── Tab 1: 曲线图 — 左侧信号树 + 已选信号区；右侧图表 ───
        chart_tab = QWidget()
        chart_layout = QHBoxLayout(chart_tab)
        chart_layout.setContentsMargins(4, 4, 4, 4)

        chart_splitter = QSplitter(Qt.Horizontal)

        # 左侧列：已选信号显示区（由「信号分组」窗分发添加，可删除）
        left_col = QWidget()
        left_layout = QVBoxLayout(left_col)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        sel_label = QLabel("已选信号（可删除）:")
        sel_label.setStyleSheet("color: #9090a0; font-weight: 500;")
        left_layout.addWidget(sel_label)

        self._selected_list = QListWidget()
        self._selected_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._selected_list.setAlternatingRowColors(True)
        left_layout.addWidget(self._selected_list, stretch=1)

        sel_btn_bar = QHBoxLayout()
        self._remove_sel_btn = QPushButton("移除选中")
        self._remove_sel_btn.setToolTip("从已选列表中移除（取消信号树中的勾选）")
        self._remove_sel_btn.clicked.connect(self._remove_selected_signals)
        sel_btn_bar.addWidget(self._remove_sel_btn)

        self._clear_sel_btn = QPushButton("清空")
        self._clear_sel_btn.setToolTip("清空所有已选信号")
        self._clear_sel_btn.clicked.connect(self._clear_selected_signals)
        sel_btn_bar.addWidget(self._clear_sel_btn)
        left_layout.addLayout(sel_btn_bar)

        # 绘制按钮：对当前已选信号重新解码并绘图
        self._plot_btn = QPushButton("绘制")
        self._plot_btn.setProperty("class", "primary")
        self._plot_btn.setToolTip("对当前已选信号重新解码并绘制曲线")
        self._plot_btn.clicked.connect(self._decode_and_plot_curve)
        left_layout.addWidget(self._plot_btn)

        chart_splitter.addWidget(left_col)

        # 右侧：图表
        self._plot_widget = PlotWidget()
        chart_splitter.addWidget(self._plot_widget)
        chart_splitter.setStretchFactor(0, 1)
        chart_splitter.setStretchFactor(1, 3)
        chart_splitter.setSizes([320, 900])

        chart_layout.addWidget(chart_splitter)
        # ─── Tab 1: 连接状态 ───
        self._conn_widget = ConnectionStatusWidget()
        self._tabs.addTab(self._conn_widget, "🔌 连接状态")

        self._tabs.addTab(chart_tab, "📈 曲线图")

        # ─── Tab 3: 报文表格 ───
        self._message_table = MessageTableWidget()
        self._tabs.addTab(self._message_table, "📋 报文表格")

        # ─── Tab 4: 模拟上报 ───
        self._sim_widget = SignalSimWidget()
        self._tabs.addTab(self._sim_widget, "📤 模拟上报")

        # ─── Tab 5: 实时监控 ───
        self._monitor_widget = RealtimeMonitorWidget()
        self._tabs.addTab(self._monitor_widget, "📡 实时监控")

        # ─── Tab 6: 实时报文 ───
        self._realtime_msg_widget = RealtimeMessageWidget()
        self._tabs.addTab(self._realtime_msg_widget, "📡 实时报文")

        # ─── Tab 7: 位图查看器 ───
        self._bit_layout = BitLayoutView()
        self._tabs.addTab(self._bit_layout, "🔢 位图查看器")

        # 连接状态页信号接线
        self._conn_widget.dbc_load_requested.connect(self._load_dbc)
        self._conn_widget.excel_load_requested.connect(self._load_excel_descriptions)
        self._conn_widget.log_load_requested.connect(self._load_log)
        self._conn_widget.connection_changed.connect(self._on_connection_changed)
        # 初始把连接页的通道/波特率推送给各硬件页（不触发连接）
        self._on_connection_changed(
            self._conn_widget.get_channel(),
            self._conn_widget.get_bitrate(),
            False,
        )

        main_layout.addWidget(self._tabs)

        # ─── 信号分组停靠窗（可悬浮 / 关闭 / 重新打开）───
        self._setup_group_dock()

    def _setup_group_dock(self):
        """将 SignalGroupPanel 包装为可停靠/悬浮/关闭的分组窗，供三页共享"""
        self._group_panel = SignalGroupPanel()
        self._group_panel.config_saved.connect(self._on_group_config_saved)
        # 分组窗分发信号到曲线图/实时监控/模拟上报
        self._group_panel.dispatch_requested.connect(self._on_dispatch)
        self._group_dock = QDockWidget("信号分组", self)
        self._group_dock.setWidget(self._group_panel)
        self._group_dock.setFeatures(
            QDockWidget.DockWidgetFloatable
            | QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetClosable
        )
        self._group_dock.setAllowedAreas(Qt.AllDockWidgetAreas)
        # 初始停靠在左侧（可在「视图」菜单浮动 / 关闭 / 重新打开）
        self.addDockWidget(Qt.LeftDockWidgetArea, self._group_dock)
        # 首次显示后再尝试浮动到主窗口左侧外部（_position_group_dock）
        self._group_dock.setFloating(True)

    def _setup_menu(self):
        """构建菜单栏"""
        menubar = self.menuBar()

        # ── 文件菜单 ──
        file_menu = menubar.addMenu("文件(&F)")

        load_dbc = QAction("加载 DBC...", self)
        load_dbc.setShortcut("Ctrl+D")
        load_dbc.setStatusTip("加载 DBC 数据库文件")
        load_dbc.triggered.connect(self._load_dbc)
        file_menu.addAction(load_dbc)

        load_log = QAction("加载日志 (BLF/ASC)...", self)
        load_log.setShortcut("Ctrl+L")
        load_log.setStatusTip("加载 BLF 或 ASC 格式的 CAN 日志文件")
        load_log.triggered.connect(self._load_log)
        file_menu.addAction(load_log)

        load_excel = QAction("加载值描述 Excel...", self)
        load_excel.setStatusTip("加载信号值描述 Excel 文件（补充 DBC 值描述）")
        load_excel.triggered.connect(self._load_excel_descriptions)
        file_menu.addAction(load_excel)

        file_menu.addSeparator()

        export_chart = QAction("导出图表图片...", self)
        export_chart.setShortcut("Ctrl+E")
        export_chart.setStatusTip("将当前曲线图导出为 PNG/SVG 图片")
        export_chart.triggered.connect(self._export_chart)
        file_menu.addAction(export_chart)

        export_data = QAction("导出信号数据...", self)
        export_data.setStatusTip("将已解码的信号数据导出为 CSV/Excel")
        export_data.triggered.connect(self._export_signal_data)
        file_menu.addAction(export_data)

        file_menu.addSeparator()

        quit_action = QAction("退出", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.setStatusTip("退出应用程序")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # ── 视图菜单 ──
        view_menu = menubar.addMenu("视图(&V)")
        view_menu.addAction(self._group_dock.toggleViewAction())

        # ── 帮助菜单 ──
        help_menu = menubar.addMenu("帮助(&H)")
        about_action = QAction("关于(&A)", self)
        about_action.setStatusTip("查看软件版本及依赖信息")
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _setup_statusbar(self):
        """构建状态栏，含进度条"""
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)

        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._progress_bar.setMaximumWidth(200)
        self._progress_bar.setTextVisible(True)
        self._statusbar.addPermanentWidget(self._progress_bar)

        self._statusbar.showMessage("就绪 — 请加载 DBC 和日志文件")

    # ═══════════════════════ 文件加载 ═══════════════════════

    def _load_dbc(self):
        """加载 DBC 文件并更新所有依赖组件"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 DBC 文件", "", "DBC Files (*.dbc);;All Files (*)"
        )
        if not path:
            return

        try:
            self._messages = parse_dbc(path)
            self._dbc_path = path

            # 通知各子组件
            self._bit_layout.load_messages(self._messages)
            self._group_panel.set_messages(self._messages)
            # 实时监控 / 模拟上报页同步报文定义与 DBC 路径
            self._monitor_widget.set_messages(self._messages)
            self._monitor_widget.set_dbc_path(path)
            self._sim_widget.set_messages(self._messages)
            self._sim_widget.set_dbc_path(path)
            # 实时报文页 / 连接状态页同步
            self._realtime_msg_widget.set_dbc_path(path)
            self._conn_widget.set_dbc_path(path)
            # Bug 2 修复：通知报文表格更新 DBC 数据库，
            # 否则先加载日志再加载 DBC 时，表格的 _db 仍为 None
            self._message_table.update_dbc(path)

            total_sigs = sum(len(m.signals) for m in self._messages)
            # 提取 DBC 值描述并传递给绘图组件
            self._build_value_descriptions()
            self._statusbar.showMessage(
                f"DBC 加载完成: {len(self._messages)} 个报文, {total_sigs} 个信号"
            )
            # 保存配置以记住本次加载的 DBC 路径
            self._save_config()
        except Exception as e:
            QMessageBox.critical(self, "DBC 加载失败", str(e))

    def _build_value_descriptions(self):
        """构建值描述：DBC 优先，Excel 补充"""
        desc = {}

        # 先从 Excel 加载（作为基础）
        if self._excel_value_descriptions:
            for sig_name, values in self._excel_value_descriptions.items():
                desc[sig_name] = dict(values)

        # DBC 值描述覆盖 Excel（DBC 优先）
        for msg in self._messages:
            for sig in msg.signals:
                if sig.choices:
                    desc[sig.name] = sig.choices

        self._value_descriptions = desc
        self._plot_widget.set_value_descriptions(desc)
        self._monitor_widget.set_value_descriptions(desc)
        # 同步 Excel 值描述到位图查看器
        self._bit_layout.set_value_descriptions(self._excel_value_descriptions)

    def _load_log(self):
        """通过文件对话框选择并异步加载日志文件"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择日志文件", "",
            "CAN Log Files (*.blf *.asc);;BLF Files (*.blf);;ASC Files (*.asc);;All Files (*)"
        )
        if not path:
            return
        self._load_log_file(path)

    def _load_log_file(self, path: str):
        """异步加载指定路径的日志文件（可被拖放复用）"""
        self._conn_widget.set_log_path(path)
        # 清空旧数据和缓存
        self._cache.clear()
        self._decoded_signals.clear()

        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._statusbar.showMessage(f"正在加载 {path} ...")

        self._load_worker = LoadWorker(path)
        self._load_worker.progress.connect(self._on_load_progress)
        self._load_worker.finished.connect(self._on_load_finished)
        self._load_worker.error.connect(self._on_load_error)
        self._load_worker.start()

    def _on_load_progress(self, percent: int):
        """日志加载进度回调"""
        self._progress_bar.setValue(percent)

    def _on_load_finished(self, frame_index, raw_data):
        """日志加载完成回调"""
        self._frame_index = frame_index
        self._raw_data = raw_data
        self._progress_bar.setVisible(False)

        num_frames = len(frame_index)
        t_start = frame_index["timestamp"].iloc[0] if num_frames > 0 else 0
        t_end = frame_index["timestamp"].iloc[-1] if num_frames > 0 else 0

        self._message_table.set_data(
            frame_index, raw_data, self._messages, self._dbc_path
        )

        self._statusbar.showMessage(
            f"日志加载完成: {num_frames} 帧, "
            f"时间范围 {t_start:.3f}s ~ {t_end:.3f}s"
        )

    def _on_load_error(self, error_msg: str):
        """日志加载失败回调"""
        self._progress_bar.setVisible(False)
        QMessageBox.critical(self, "日志加载失败", error_msg)

    # ═══════════════════════ 信号勾选与绘图 ═══════════════════════

    def _on_dispatch(self, target: str, signals: list):
        """信号分组窗分发按钮回调：把信号送到对应目标页（曲线图/实时监控/模拟上报）"""
        if target == "curve":
            self._add_curve_signals(signals)
        elif target == "monitor":
            self._monitor_widget.add_selected_signals(signals)
        elif target == "sim":
            self._sim_widget.add_selected_signals(signals)

    def _add_curve_signals(self, signals: list):
        """把分发的信号加入曲线图已选集合（去重）；加入即绘图"""
        added = False
        for msg_name, sig_name in signals:
            if (msg_name, sig_name) not in self._curve_signals:
                self._curve_signals.add((msg_name, sig_name))
                added = True
        if added:
            self._refresh_curve_list()
            # 加入即绘图
            self._decode_and_plot_curve()

    def _refresh_curve_list(self):
        """刷新曲线图「已选信号」列表（按 (msg_name, sig_name) 去重）"""
        self._selected_list.blockSignals(True)
        self._selected_list.clear()
        for msg_name, sig_name in sorted(self._curve_signals):
            item = QListWidgetItem(f"{sig_name}  ({msg_name})")
            item.setData(Qt.UserRole, (msg_name, sig_name))
            self._selected_list.addItem(item)
        self._selected_list.blockSignals(False)

    def _remove_selected_signals(self):
        """从已选列表中移除选中项"""
        for item in self._selected_list.selectedItems():
            self._curve_signals.discard(item.data(Qt.UserRole))
        self._refresh_curve_list()

    def _clear_selected_signals(self):
        """清空所有已选信号"""
        self._curve_signals.clear()
        self._refresh_curve_list()

    def _decode_and_plot_curve(self):
        """对曲线图已选信号解码并绘图（加入即绘图 / 点击「绘制」）"""
        signals = sorted(self._curve_signals)
        if not signals:
            return
        if not self._dbc_path:
            QMessageBox.warning(self, "提示", "请先加载 DBC 文件")
            return
        if self._frame_index is None:
            QMessageBox.warning(self, "提示", "请先加载日志文件")
            return
        self._curve_decode_gen += 1
        gen = self._curve_decode_gen
        self._decoded_signals.clear()
        self._statusbar.showMessage(f"正在解码 {len(signals)} 个信号...")
        for msg_name, sig_name in signals:
            worker = DecodeWorker(
                self._dbc_path, msg_name, sig_name,
                self._frame_index, self._raw_data, self._cache,
            )
            worker.finished.connect(
                lambda ds, g=gen: self._on_curve_decode_finished(ds, g)
            )
            worker.error.connect(
                lambda e: QMessageBox.warning(self, "解码错误", e)
            )
            worker.start()
            self._decode_workers.append(worker)

    def _on_curve_decode_finished(self, decoded_signal: DecodedSignal, gen: int):
        """曲线图信号解码完成回调（带批次代次，避免过期批次误绘）"""
        if gen != self._curve_decode_gen:
            return
        self._decoded_signals.append(decoded_signal)
        if len(self._decoded_signals) >= len(self._curve_signals):
            self._plot_widget.plot_signals(self._decoded_signals)
            self._tabs.setCurrentIndex(1)
            self._statusbar.showMessage(
                f"绘图完成: {len(self._decoded_signals)} 个信号"
            )

    def _on_connection_changed(self, channel: str, bitrate: int, connected: bool):
        """连接状态页通道/波特率/连接状态变化：推送给各硬件页"""
        self._monitor_widget.set_connection(channel, bitrate)
        self._sim_widget.set_connection(channel, bitrate)
        self._realtime_msg_widget.set_connection(channel, bitrate)
        if connected:
            self._realtime_msg_widget.start_capture(channel, bitrate)
        else:
            self._realtime_msg_widget.stop_capture()

    def _position_group_dock(self):
        """将分组窗浮动到主窗口左侧外部（空间不足则停靠回左侧）"""
        dock = self._group_dock
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        w = dock.width() if dock.width() > 0 else 340
        h = max(420, int(avail.height() * 0.72))
        x = self.x() - w - 12
        y = self.y() + 30
        if x < avail.x():
            # 左侧无足够空间，停靠回左侧
            dock.setFloating(False)
            self.addDockWidget(Qt.LeftDockWidgetArea, dock)
            return
        dock.setFloating(True)
        dock.resize(w, h)
        dock.move(x, y)

    def showEvent(self, event):
        super().showEvent(event)
        # 仅首次显示后将分组窗尝试浮动到左侧外部
        if not self._dock_positioned:
            self._dock_positioned = True
            self._position_group_dock()

    def _on_group_config_saved(self, path: str):
        """分组配置保存回调：记住路径以便下次启动时自动加载"""
        self._group_config_path = path
        self._save_config()

    # ═══════════════════════ 导出 ═══════════════════════

    def _export_chart(self):
        """导出当前曲线图为图片"""
        path, _ = QFileDialog.getSaveFileName(
            self, "导出图表", "", "PNG (*.png);;SVG (*.svg)"
        )
        if path:
            export_chart_image(self._plot_widget.get_figure(), path)
            self._statusbar.showMessage(f"图表已导出: {path}")

    def _export_signal_data(self):
        """导出已解码的信号数据"""
        if not self._decoded_signals:
            QMessageBox.information(self, "提示", "请先解码信号")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "导出信号数据", "", "CSV (*.csv);;Excel (*.xlsx)"
        )
        if path:
            export_signal_data(self._decoded_signals, path)
            self._statusbar.showMessage(f"数据已导出: {path}")

    # ═══════════════════════ 配置持久化 ═══════════════════════

    def _load_config(self) -> dict:
        """加载配置文件"""
        try:
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_config(self):
        """保存配置（记录上次加载的 DBC、Excel 和分组配置路径）"""
        config = self._load_config()
        if self._dbc_path:
            config["last_dbc_path"] = self._dbc_path
        if self._excel_path:
            config["last_excel_path"] = self._excel_path
        if self._group_config_path:
            config["last_group_config_path"] = self._group_config_path
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _auto_load_dbc(self, path: str):
        """自动加载 DBC 文件（启动时或拖放时）。

        失败时抛出异常，由调用方决定展示方式（状态栏或弹窗）。
        """
        self._messages = parse_dbc(path)
        self._dbc_path = path
        self._bit_layout.load_messages(self._messages)
        self._group_panel.set_messages(self._messages)
        # 实时监控 / 模拟上报页同步报文定义与 DBC 路径
        self._monitor_widget.set_messages(self._messages)
        self._monitor_widget.set_dbc_path(path)
        self._sim_widget.set_messages(self._messages)
        self._sim_widget.set_dbc_path(path)
        # 实时报文页 / 连接状态页同步
        self._realtime_msg_widget.set_dbc_path(path)
        self._conn_widget.set_dbc_path(path)
        # Bug 2 修复：通知报文表格更新 DBC 数据库
        self._message_table.update_dbc(path)
        self._build_value_descriptions()
        self._statusbar.showMessage(f"已自动加载 DBC: {path}")

    def _load_excel_descriptions(self):
        """通过文件对话框加载值描述 Excel 文件"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择值描述 Excel 文件", "",
            "Excel Files (*.xlsx *.xls);;All Files (*)"
        )
        if not path:
            return
        try:
            self._do_load_excel_descriptions(path)
        except Exception as e:
            QMessageBox.critical(self, "加载失败", str(e))

    def _auto_load_excel_descriptions(self, path: str):
        """自动加载 Excel 值描述文件（启动时或拖放时）"""
        self._do_load_excel_descriptions(path, auto=True)

    def _do_load_excel_descriptions(self, path: str, auto: bool = False):
        """实际执行 Excel 值描述加载的逻辑。

        Args:
            path: Excel 文件路径
            auto: True 表示自动加载（启动时），False 表示用户手动操作。
                  当 auto=True 且失败时仍会抛出异常，由调用方处理展示。
        """
        self._excel_value_descriptions = load_value_descriptions(path)
        self._excel_path = path
        self._conn_widget.set_excel_path(path)
        self._build_value_descriptions()
        count = sum(len(v) for v in self._excel_value_descriptions.values())
        prefix = "已自动加载" if auto else "加载完成"
        self._statusbar.showMessage(
            f"值描述{prefix}: {len(self._excel_value_descriptions)} 个信号, {count} 个描述"
        )
        # 保存配置以记住本次加载的 Excel 路径
        self._save_config()

    # ═══════════════════════ 关于对话框 ═══════════════════════

    def _show_about(self):
        """显示关于对话框"""
        about_text = """
        <h2>CAN 报文分析工具</h2>
        <p><b>版本:</b> 1.0.0</p>
        <p><b>作者:</b> laizhenxin</p>
        <p><b>邮箱:</b> lzxDchaser@126.com</p>
        <hr>
        <p>一款类似 Vector CANoe 的车载 CAN 报文分析桌面工具。</p>
        <p><b>主要功能:</b></p>
        <ul>
            <li>DBC 文件解析与信号浏览</li>
            <li>BLF/ASC 日志文件加载</li>
            <li>多信号时间曲线绘制（支持缩放、平移、悬停高亮）</li>
            <li>信号分组管理与配置保存/加载</li>
            <li>原始报文查看与解码</li>
            <li>DBC 位图可视化（Intel/Motorola）</li>
            <li>图表/数据导出</li>
        </ul>
        <p><b>Python 依赖包:</b></p>
        <ul>
            <li>PyQt5 — GUI 框架</li>
            <li>matplotlib — 图表绘制</li>
            <li>cantools — DBC 解析</li>
            <li>python-can — CAN 日志读取</li>
            <li>pandas — 数据处理</li>
            <li>numpy — 数值计算</li>
            <li>openpyxl — Excel .xlsx 文件读写</li>
            <li>xlrd — Excel .xls/.xlsx 文件读取</li>
            <li>lxml — HTML 表格解析（用于伪装为 .xls 的 HTML 文件）</li>
        </ul>
        """
        QMessageBox.about(self, "关于 CAN 报文分析工具", about_text)

    # ═══════════════════════ 拖拽支持 ═══════════════════════

    def closeEvent(self, event):
        """退出前停止后台监控/模拟上报线程，避免线程悬挂"""
        try:
            self._monitor_widget.stop()
        except Exception:
            pass
        try:
            self._sim_widget.stop()
        except Exception:
            pass
        super().closeEvent(event)

    def dragEnterEvent(self, event):
        """接受 .dbc / .blf / .asc / .xlsx / .xls 文件的拖拽"""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                path = url.toLocalFile().lower()
                if path.endswith(('.dbc', '.blf', '.asc', '.xlsx', '.xls')):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        """处理文件拖放：根据扩展名加载 DBC、日志或 Excel 值描述。

        任何加载失败都会弹出 QMessageBox 错误提示，避免静默失败。
        """
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            ext = os.path.splitext(path)[1].lower()
            try:
                if ext == '.dbc':
                    self._auto_load_dbc(path)
                    self._save_config()
                elif ext in ('.blf', '.asc'):
                    self._load_log_file(path)
                elif ext in ('.xlsx', '.xls'):
                    self._do_load_excel_descriptions(path)
            except Exception as e:
                QMessageBox.critical(
                    self, f"加载失败: {os.path.basename(path)}", str(e)
                )
