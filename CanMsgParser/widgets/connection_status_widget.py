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
    QPushButton, QComboBox, QFileDialog, QMessageBox,
)
from PyQt5.QtCore import pyqtSignal

from core.can_utils import (
    DEFAULT_CHANNEL, DEFAULT_BITRATE, DEFAULT_INTERFACE_TYPE,
    DEVICE_TYPES, interface_available,
)
from core.can_connection import CanConnectionManager


class ConnectionStatusWidget(QWidget):
    # 协议数据加载请求（点击加载按钮时发射，主窗口负责打开对话框并执行）
    dbc_load_requested = pyqtSignal()
    excel_load_requested = pyqtSignal()
    log_load_requested = pyqtSignal()
    # CAN 连接状态变化: (interface_type, channel, bitrate, connected)
    connection_changed = pyqtSignal(str, str, int, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected = False
        self._manager: CanConnectionManager | None = None
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

        # 设备类型选择（同星 / PEAK / Vector / 虚拟）
        dev_row = QHBoxLayout()
        dev_row.setSpacing(8)
        dev_row.addWidget(QLabel("设备类型:"))
        self._device_combo = QComboBox()
        # 保存 (key, label) 顺序，key 用于连接，label 用于显示
        self._device_keys = ["peak", "tosun", "vector", "virtual"]
        for key in self._device_keys:
            info = DEVICE_TYPES[key]
            avail = interface_available(key)
            suffix = "" if avail else "（后端缺失）"
            self._device_combo.addItem(info["label"] + suffix, key)
        # 默认选中 DEFAULT_INTERFACE_TYPE
        default_idx = self._device_keys.index(DEFAULT_INTERFACE_TYPE) \
            if DEFAULT_INTERFACE_TYPE in self._device_keys else 0
        self._device_combo.setCurrentIndex(default_idx)
        self._device_combo.currentIndexChanged.connect(self._on_device_changed)
        dev_row.addWidget(self._device_combo)
        dev_row.addStretch()
        can_layout.addLayout(dev_row)

        ch_row = QHBoxLayout()
        ch_row.setSpacing(8)
        ch_row.addWidget(QLabel("通道:"))
        self._channel_combo = QComboBox()
        self._channel_combo.setEditable(True)
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

        # 依据默认设备类型填充通道下拉
        self._refresh_channels()

        # 驱动/依赖提示行
        self._needs_label = QLabel("")
        self._needs_label.setWordWrap(True)
        self._needs_label.setStyleSheet("color: #90a4ae; font-size: 12px;")
        can_layout.addWidget(self._needs_label)
        self._update_needs_hint()

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

    # ── 设备类型/通道联动 ──
    def _on_device_changed(self, _idx: int):
        self._refresh_channels()
        self._update_needs_hint()

    def _refresh_channels(self):
        """依据当前设备类型刷新可选通道列表。"""
        key = self.get_interface_type()
        info = DEVICE_TYPES.get(key, {})
        channels = info.get("channels", [])
        self._channel_combo.blockSignals(True)
        self._channel_combo.clear()
        self._channel_combo.addItems(channels)
        if channels:
            # PEAK 默认选中 DEFAULT_CHANNEL，其余选第一个
            if key == "peak" and DEFAULT_CHANNEL in channels:
                self._channel_combo.setCurrentText(DEFAULT_CHANNEL)
            else:
                self._channel_combo.setCurrentIndex(0)
        self._channel_combo.blockSignals(False)

    def _update_needs_hint(self):
        key = self.get_interface_type()
        info = DEVICE_TYPES.get(key, {})
        self._needs_label.setText("说明: " + info.get("needs", ""))

    # ── CAN 连接回调 ──
    def set_connection_manager(self, manager: CanConnectionManager):
        """注入进程内共享连接管理器；其状态变化用于同步本页 UI。"""
        self._manager = manager
        manager.state_changed.connect(self._on_manager_state)

    def _on_connect(self):
        if self._manager is None:
            QMessageBox.critical(self, "CAN 连接失败", "连接管理器未初始化")
            return
        interface_type = self.get_interface_type()
        channel = self.get_channel()
        bitrate = self.get_bitrate()

        # 后端可用性校验：缺驱动/依赖时明确拦截，绝不假「已连接」
        if not interface_available(interface_type):
            info = DEVICE_TYPES.get(interface_type, {})
            pycan = info.get("pycan", "")
            QMessageBox.critical(
                self, "CAN 连接失败",
                f"{info.get('label', interface_type)} 连接失败：\n"
                f"python-can 缺少 '{pycan}' 后端"
                f"（无法导入 can.interfaces.{pycan}）。\n"
                f"{info.get('needs', '')}"
            )
            self._set_disconnected_ui()
            return
        if not channel:
            QMessageBox.warning(self, "提示", "请先选择或输入 CAN 通道")
            return

        # 先把配置推送给各功能页（同步其 interface_type/channel/bitrate），
        # 再真正建立共享总线。否则 manager.connect 触发 state_changed 时，
        # 各页仍用旧配置自动连接会误报「配置不一致」。
        self.connection_changed.emit(interface_type, channel, bitrate, True)

        # 真正建立共享总线（进程内唯一一条）。UI 由 manager.state_changed
        # 统一驱动，故此处成功只推送配置给各功能页即可。
        bus, err = self._manager.connect(interface_type, channel, bitrate)
        if bus is None:
            QMessageBox.critical(self, "CAN 连接失败", err)
            self._set_disconnected_ui()
            return

    def _on_disconnect(self):
        # 先通知主窗口停止各功能页的收/发，再断开共享总线
        self.connection_changed.emit(
            self.get_interface_type(), self.get_channel(), self.get_bitrate(), False
        )
        if self._manager is not None:
            self._manager.disconnect()

    def _on_manager_state(self, connected: bool, info: str):
        """由 CanConnectionManager.state_changed 驱动本页 UI。

        无论是用户手动点「连接 CAN」，还是功能页自动连接成功，UI 都由此统一
        更新，保证状态一致。
        """
        if connected:
            self._set_connected_ui(info)
        else:
            self._set_disconnected_ui()

    def _set_connected_ui(self, info: str):
        self._connected = True
        self._can_status.setText(f"已连接（{info}）")
        self._can_status.setStyleSheet("color: #4fc3f7; font-weight: bold;")
        self._connect_btn.setEnabled(False)
        self._disconnect_btn.setEnabled(True)
        self._device_combo.setEnabled(False)
        self._channel_combo.setEnabled(False)
        self._bitrate_combo.setEnabled(False)

    def _set_disconnected_ui(self):
        self._connected = False
        self._can_status.setText("未连接")
        self._can_status.setStyleSheet("color: #ef5350; font-weight: bold;")
        self._connect_btn.setEnabled(True)
        self._disconnect_btn.setEnabled(False)
        self._device_combo.setEnabled(True)
        self._channel_combo.setEnabled(True)
        self._bitrate_combo.setEnabled(True)

    def get_interface_type(self) -> str:
        return self._device_combo.currentData() or DEFAULT_INTERFACE_TYPE

    def get_channel(self) -> str:
        return self._channel_combo.currentText()

    def get_bitrate(self) -> int:
        try:
            return int(self._bitrate_combo.currentText())
        except ValueError:
            return DEFAULT_BITRATE

    def is_connected(self) -> bool:
        return self._connected
