# widgets/realtime_monitor_widget.py
"""信号实时监控页（重构）：通过「连接状态」页提供的通道/波特率连接 PCAN，
实时接收并绘制「已选信号列表」中的信号曲线。

布局：
- 左侧：已选信号列表（可删除）— 信号由「信号分组」窗的分发按钮添加
- 右侧：状态 + 实时曲线（复用 PlotWidget 的实时模式）

通道/波特率不再由本页设置，统一由「连接状态」页提供（set_connection）。
"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QPushButton,
    QLabel, QMessageBox, QAbstractItemView, QListWidget, QListWidgetItem,
)
from PyQt5.QtCore import Qt

from widgets.plot_widget import PlotWidget
from widgets.del_key_filter import DelKeyFilter
from workers.can_capture_worker import CanCaptureWorker
from core.can_utils import (DEFAULT_CHANNEL, DEFAULT_BITRATE, DEFAULT_INTERFACE_TYPE)
from core.can_connection import CanConnectionManager
from core.can_data import MessageDef


class RealtimeMonitorWidget(QWidget):
    """信号实时监控页"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._messages: list[MessageDef] = []
        self._dbc_path: str = ""
        self._interface_type = DEFAULT_INTERFACE_TYPE
        self._channel = DEFAULT_CHANNEL
        self._bitrate = DEFAULT_BITRATE
        self._capture_worker: CanCaptureWorker | None = None
        self._monitoring = False
        self._manager: CanConnectionManager | None = None  # 进程内共享连接
        self._sel_signals: set = set()  # {(msg_name, sig_name)}
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        splitter = QSplitter(Qt.Horizontal)

        # ─── 左侧：已选信号列表 ───
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        left_layout.addWidget(QLabel("已选信号（可删除）:"))
        self._sel_list = QListWidget()
        self._sel_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._sel_list.setAlternatingRowColors(True)
        left_layout.addWidget(self._sel_list, stretch=1)

        sel_bar = QHBoxLayout()
        self._remove_btn = QPushButton("移除选中")
        self._remove_btn.clicked.connect(self._remove_selected)
        sel_bar.addWidget(self._remove_btn)
        self._clear_btn = QPushButton("清空")
        self._clear_btn.clicked.connect(self._clear_selected)
        sel_bar.addWidget(self._clear_btn)
        left_layout.addLayout(sel_bar)

        splitter.addWidget(left)

        # ─── 右侧：控制 + 实时曲线 ───
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)
        self._status_label = QLabel("未连接")
        self._status_label.setStyleSheet("color: #9090a0;")
        ctrl.addWidget(self._status_label)
        ctrl.addStretch()
        self._start_btn = QPushButton("开始监控")
        self._start_btn.setProperty("class", "primary")
        self._start_btn.clicked.connect(self._on_start_stop)
        ctrl.addWidget(self._start_btn)
        self._reset_btn = QPushButton("重置曲线")
        self._reset_btn.setToolTip("清空当前曲线（监控中点击则清空画面并继续记录）")
        self._reset_btn.clicked.connect(self._on_reset_plot)
        ctrl.addWidget(self._reset_btn)
        right_layout.addLayout(ctrl)

        self._plot = PlotWidget()
        # Issue 3：实时监控页默认使用独立子图模式
        self._plot.set_subplot_mode(True)
        right_layout.addWidget(self._plot, stretch=1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([300, 900])
        layout.addWidget(splitter)

        # Delete 键移除选中信号（等价于「移除选中」按钮）
        self._del_filter = DelKeyFilter(self._sel_list, self._remove_selected)

    # ────────────────────── 公共接口 ──────────────────────

    def set_messages(self, messages: list[MessageDef]):
        self._messages = messages

    def set_dbc_path(self, dbc_path: str):
        self._dbc_path = dbc_path

    def set_connection(self, interface_type: str, channel: str, bitrate: int):
        """由「连接状态」页注入设备类型/通道/波特率（本页不再自带控件）"""
        self._interface_type = interface_type
        self._channel = channel
        self._bitrate = bitrate

    def set_connection_manager(self, manager: CanConnectionManager):
        """注入进程内共享连接管理器（总线由它统一持有，本页只取用不自建）"""
        self._manager = manager

    def set_value_descriptions(self, descriptions: dict):
        """透传 DBC 值描述给实时曲线，使悬停注释显示枚举含义"""
        self._plot.set_value_descriptions(descriptions)

    def add_selected_signals(self, signals: list):
        """由「信号分组」窗分发按钮添加信号（去重）"""
        added = False
        for msg_name, sig_name in signals:
            if (msg_name, sig_name) not in self._sel_signals:
                self._sel_signals.add((msg_name, sig_name))
                added = True
        if added:
            self._refresh_sel_list()
            # 监控进行中加入信号：同步进采集与曲线（曲线/采集各自去重）
            self._sync_monitor_signals()

    def _refresh_sel_list(self):
        self._sel_list.blockSignals(True)
        self._sel_list.clear()
        for msg_name, sig_name in sorted(self._sel_signals):
            item = QListWidgetItem(f"{sig_name}  ({msg_name})")
            item.setData(Qt.UserRole, (msg_name, sig_name))
            self._sel_list.addItem(item)
        self._sel_list.blockSignals(False)

    def _remove_selected(self):
        for item in self._sel_list.selectedItems():
            self._sel_signals.discard(item.data(Qt.UserRole))
        self._refresh_sel_list()
        # 移除信号后：实时曲线与采集 worker 同步停止该信号（Bug1 修复）
        self._sync_monitor_signals()

    def _clear_selected(self):
        self._sel_signals.clear()
        self._refresh_sel_list()
        # 清空后：曲线与采集同步清空（Bug1 修复）
        self._sync_monitor_signals()

    def _sync_monitor_signals(self):
        """已选信号列表变化后，使实时曲线与采集 worker 与实际选择保持一致：

        - 移除已不在选择中的信号：曲线删除、采集停止；
        - 监控进行中新增的信号：加入采集与曲线。
        """
        sel = self._sel_signals
        # 1) 曲线侧：移除不在选择中的信号（一次重绘，不影响其它曲线）
        if self._plot._realtime:
            self._plot.remove_realtime_signals(sel)
        # 2) 采集 worker 侧：与选择同步（去/加）
        if self._capture_worker is not None:
            self._capture_worker.sync_signals(sel)
            # 3) 监控进行中新增的信号：加入曲线（worker 侧已同步）
            if self._monitoring:
                for (m, s) in sel:
                    if not self._plot.has_realtime_signal(m, s):
                        self._plot.add_realtime_signal(self._frame_id_of(m), m, s)

    # ────────────────────── 开始 / 停止监控 ──────────────────────

    def _on_start_stop(self):
        if self._monitoring:
            self._stop_monitoring()
        else:
            self._start_monitoring()

    def _start_monitoring(self):
        if not self._dbc_path:
            QMessageBox.warning(self, "提示", "请先加载 DBC 文件")
            return
        if not self._messages:
            QMessageBox.warning(self, "提示", "请先加载 DBC 文件（报文定义缺失）")
            return
        if not self._sel_signals:
            QMessageBox.warning(self, "提示", "请先通过「信号分组」窗添加要监控的信号")
            return
        if self._manager is None:
            QMessageBox.critical(self, "监控错误", "连接管理器未初始化")
            return

        # 通过共享管理器建立/复用唯一总线（未连则自动连接，避免“忘了点连接”）
        bus, err = self._manager.ensure_connected(
            self._interface_type, self._channel, self._bitrate
        )
        if bus is None:
            QMessageBox.critical(self, "监控错误", err)
            return

        checked = list(self._sel_signals)  # [(msg_name, sig_name)] 给解码 worker
        meta = [(self._frame_id_of(m), m, s) for (m, s) in self._sel_signals]
        self._plot.start_realtime(meta)
        self._capture_worker = CanCaptureWorker(self._dbc_path, checked)
        self._capture_worker.sample_received.connect(self._on_sample, Qt.QueuedConnection)
        self._capture_worker.status_changed.connect(self._on_status, Qt.QueuedConnection)
        self._capture_worker.error_occurred.connect(self._on_error, Qt.QueuedConnection)
        if not self._capture_worker.start_monitoring():
            self._capture_worker = None
            return
        # 注册为共享总线的收帧监听者（管理器唯一收帧线程会把每帧 fan-out 过来）
        self._manager.add_listener(self._capture_worker.process_message)

        self._monitoring = True
        self._start_btn.setText("■ 停止监控")

    def _stop_monitoring(self):
        if self._capture_worker is not None and self._manager is not None:
            # 注销收帧监听者（无监听者时管理器自动停收帧线程）；共享总线不自关
            self._manager.remove_listener(self._capture_worker.process_message)
            self._capture_worker.stop()
            self._capture_worker = None
        self._plot.stop_realtime()
        self._monitoring = False
        self._start_btn.setText("开始监控")
        self._status_label.setText("已停止")

    def _on_reset_plot(self):
        """Issue 3：重置曲线按钮——清空当前画面，监控中则继续记录。"""
        self._plot.reset_realtime()
        if self._monitoring:
            self._status_label.setText("曲线已重置，继续记录…")
        else:
            self._status_label.setText("曲线已清空")

    # ────────────────────── 信号回调 ──────────────────────

    def _frame_id_of(self, msg_name: str) -> int | None:
        """从已加载的报文定义中反查报文 ID（用于图例展示）"""
        for m in self._messages:
            if m.name == msg_name:
                return m.frame_id
        return None

    def _on_sample(self, frame_id: int, msg_name: str, sig_name: str, t: float, v: float):
        self._plot.push_sample(frame_id, msg_name, sig_name, t, v)

    def _on_status(self, text: str):
        self._status_label.setText(text)

    def _on_error(self, text: str):
        QMessageBox.critical(self, "监控错误", text)
        self._stop_monitoring()

    def closeEvent(self, event):
        self._stop_monitoring()
        super().closeEvent(event)

    def stop(self):
        """供主窗口在退出时强制停止后台监控线程"""
        self._stop_monitoring()
