# widgets/connection_status_widget.py
"""连接状态页：协议数据加载（矩阵xlsx / DBC / 待分析报文）+ CAN 总线连接

- 协议数据加载：三项分别加载 值描述 Excel、DBC、待分析日志(BLF/ASC)，
  点击「加载」仅发出请求信号，由主窗口统一打开文件对话框并执行加载，
  加载完成后回调本页更新路径显示。
- CAN 总线连接：通道 / 波特率下拉 + 连接 / 断开按钮 + 状态显示，
  作为模拟上报 / 实时监控 / 实时报文三页共用的通道波特率唯一来源。
"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QComboBox, QFileDialog,
)
from PyQt5.QtCore import pyqtSignal

from core.can_utils import DEFAULT_CHANNEL, DEFAULT_BITRATE


class ConnectionStatusWidget(QWidget):
    # 协议数据加载请求（点击加载按钮时发射，主窗口负责打开对话框并执行）
    dbc_load_requested = pyqtSignal()
    excel_load_requested = pyqtSignal()
    log_load_requested = pyqtSignal()
    # CAN 连接状态变化: (channel, bitrate, connected)
    connection_changed = pyqtSignal(str, int, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected = False
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        # ─── 协议数据加载 ───
        proto_group = QGroupBox("协议数据加载")
        proto_layout = QVBoxLayout(proto_group)
        proto_layout.setSpacing(10)

        self._excel_label = QLabel("未加载")
        self._dbc_label = QLabel("未加载")
        self._log_label = QLabel("未加载")

        proto_layout.addLayout(
            self._make_loader_row("矩阵 xlsx（值描述）", self._excel_label, self._on_excel)
        )
        proto_layout.addLayout(
            self._make_loader_row("DBC 数据库", self._dbc_label, self._on_dbc)
        )
        proto_layout.addLayout(
            self._make_loader_row("待分析报文（日志）", self._log_label, self._on_log)
        )

        root.addWidget(proto_group)

        # ─── CAN 总线连接 ───
        can_group = QGroupBox("CAN 总线连接")
        can_layout = QVBoxLayout(can_group)
        can_layout.setSpacing(10)

        self._can_status = QLabel("未连接")
        self._can_status.setStyleSheet("color: #ef5350; font-weight: bold;")
        can_layout.addWidget(self._can_status)

        ch_row = QHBoxLayout()
        ch_row.setSpacing(8)
        ch_row.addWidget(QLabel("通道:"))
        self._channel_combo = QComboBox()
        self._channel_combo.addItems(
            ["PCAN_USBBUS1", "PCAN_USBBUS2", "PCAN_USBBUS3", "PCAN_USBBUS4"]
        )
        if DEFAULT_CHANNEL in [
            self._channel_combo.itemText(i)
            for i in range(self._channel_combo.count())
        ]:
            self._channel_combo.setCurrentText(DEFAULT_CHANNEL)
        ch_row.addWidget(self._channel_combo)
        ch_row.addStretch()

        ch_row.addWidget(QLabel("波特率:"))
        self._bitrate_combo = QComboBox()
        self._bitrate_combo.setEditable(True)
        for b in [500000, 250000, 125000, 1000000, 50000]:
            self._bitrate_combo.addItem(str(b))
        self._bitrate_combo.setCurrentText(str(DEFAULT_BITRATE))
        ch_row.addWidget(self._bitrate_combo)
        can_layout.addLayout(ch_row)

        btn_row = QHBoxLayout()
        self._connect_btn = QPushButton("连接 CAN")
        self._connect_btn.setProperty("class", "primary")
        self._connect_btn.clicked.connect(self._on_connect)
        btn_row.addWidget(self._connect_btn)

        self._disconnect_btn = QPushButton("断开 CAN")
        self._disconnect_btn.setEnabled(False)
        self._disconnect_btn.clicked.connect(self._on_disconnect)
        btn_row.addWidget(self._disconnect_btn)
        btn_row.addStretch()
        can_layout.addLayout(btn_row)

        root.addWidget(can_group)
        root.addStretch()

    def _make_loader_row(self, title, label, slot):
        row = QHBoxLayout()
        row.setSpacing(8)
        t = QLabel(title)
        t.setMinimumWidth(170)
        row.addWidget(t)
        row.addWidget(label, stretch=1)
        btn = QPushButton("加载")
        btn.clicked.connect(slot)
        row.addWidget(btn)
        return row

    # ── 协议数据加载回调 ──
    def _on_dbc(self):
        self.dbc_load_requested.emit()

    def _on_excel(self):
        self.excel_load_requested.emit()

    def _on_log(self):
        self.log_load_requested.emit()

    def set_dbc_path(self, path: str):
        self._dbc_label.setText(path if path else "未加载")

    def set_excel_path(self, path: str):
        self._excel_label.setText(path if path else "未加载")

    def set_log_path(self, path: str):
        self._log_label.setText(path if path else "未加载")

    # ── CAN 连接回调 ──
    def _on_connect(self):
        self._connected = True
        self._can_status.setText("已连接")
        self._can_status.setStyleSheet("color: #4fc3f7; font-weight: bold;")
        self._connect_btn.setEnabled(False)
        self._disconnect_btn.setEnabled(True)
        self.connection_changed.emit(self.get_channel(), self.get_bitrate(), True)

    def _on_disconnect(self):
        self._connected = False
        self._can_status.setText("未连接")
        self._can_status.setStyleSheet("color: #ef5350; font-weight: bold;")
        self._connect_btn.setEnabled(True)
        self._disconnect_btn.setEnabled(False)
        self.connection_changed.emit(self.get_channel(), self.get_bitrate(), False)

    def get_channel(self) -> str:
        return self._channel_combo.currentText()

    def get_bitrate(self) -> int:
        try:
            return int(self._bitrate_combo.currentText())
        except ValueError:
            return DEFAULT_BITRATE

    def is_connected(self) -> bool:
        return self._connected
