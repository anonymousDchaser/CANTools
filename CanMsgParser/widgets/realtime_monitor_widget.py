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
from workers.can_capture_worker import CanCaptureWorker
from core.can_utils import DEFAULT_CHANNEL, DEFAULT_BITRATE
from core.can_data import MessageDef


class RealtimeMonitorWidget(QWidget):
    """信号实时监控页"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._messages: list[MessageDef] = []
        self._dbc_path: str = ""
        self._channel = DEFAULT_CHANNEL
        self._bitrate = DEFAULT_BITRATE
        self._capture_worker: CanCaptureWorker | None = None
        self._monitoring = False
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
        right_layout.addLayout(ctrl)

        self._plot = PlotWidget()
        right_layout.addWidget(self._plot, stretch=1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([300, 900])
        layout.addWidget(splitter)

    # ────────────────────── 公共接口 ──────────────────────

    def set_messages(self, messages: list[MessageDef]):
        self._messages = messages

    def set_dbc_path(self, dbc_path: str):
        self._dbc_path = dbc_path

    def set_connection(self, channel: str, bitrate: int):
        """由「连接状态」页注入通道与波特率（本页不再自带控件）"""
        self._channel = channel
        self._bitrate = bitrate

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

    def _clear_selected(self):
        self._sel_signals.clear()
        self._refresh_sel_list()

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
        if not self._sel_signals:
            QMessageBox.warning(self, "提示", "请先通过「信号分组」窗添加要监控的信号")
            return

        checked = list(self._sel_signals)
        self._plot.start_realtime(checked)
        self._capture_worker = CanCaptureWorker(
            self._dbc_path, checked, self._channel, self._bitrate
        )
        self._capture_worker.sample_received.connect(self._on_sample)
        self._capture_worker.status_changed.connect(self._on_status)
        self._capture_worker.error_occurred.connect(self._on_error)
        self._capture_worker.start()

        self._monitoring = True
        self._start_btn.setText("■ 停止监控")

    def _stop_monitoring(self):
        if self._capture_worker is not None:
            self._capture_worker.stop()
            self._capture_worker = None
        self._plot.stop_realtime()
        self._monitoring = False
        self._start_btn.setText("开始监控")
        self._status_label.setText("已停止")

    # ────────────────────── 信号回调 ──────────────────────

    def _on_sample(self, msg_name: str, sig_name: str, t: float, v: float):
        self._plot.push_sample(msg_name, sig_name, t, v)

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
