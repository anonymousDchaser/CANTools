# widgets/realtime_message_widget.py
"""实时报文页：监听总线上全部报文，按报文 ID 单行展示原始数据，
双击报文行就地展开显示其解码信号（含「上一次的值」）；支持清除、录制 BLF。

与「报文表格」页的区别：
- 本页面向实时总线，同 ID 只保留一行（原地更新），不按帧展开；
- 双击报文行后在其下方就地展开子项显示各信号（名称/当前值/单位/上一次的值），
  比弹窗更直观，无需额外对话框。
"""
import os
from datetime import datetime

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QPushButton, QLineEdit, QLabel, QFileDialog, QAbstractItemView,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor

from workers.can_raw_capture_worker import CanRawCaptureWorker
from core.can_utils import load_dbc, decode_frame
from core.can_utils import (DEFAULT_CHANNEL, DEFAULT_BITRATE, DEFAULT_INTERFACE_TYPE)
from core.can_connection import CanConnectionManager


class RealtimeMessageWidget(QWidget):
    """实时报文监控页（同 ID 单行 + 双击就地展开 + 录制 BLF）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dbc_path = ""
        self._db = None
        self._interface_type = DEFAULT_INTERFACE_TYPE
        self._channel = DEFAULT_CHANNEL
        self._bitrate = DEFAULT_BITRATE
        self._capture_worker = None
        self._capturing = False
        self._user_paused = False  # 用户手动暂停监听标记（重连时不自动恢复）
        self._manager: CanConnectionManager | None = None  # 进程内共享连接
        self._recording = False
        # frame_id -> 顶层 QTreeWidgetItem
        self._rows: dict[int, QTreeWidgetItem] = {}
        # frame_id -> {sig_name: 子项 QTreeWidgetItem}
        self._child_items: dict[int, dict] = {}
        # 当前/上一次解码值: frame_id -> {sig_name: value}
        self._cur_values: dict[int, dict] = {}
        self._prev_values: dict[int, dict] = {}
        # frame_id -> 最近原始数据（用于展开时解码）
        self._last_data: dict[int, bytes] = {}
        # frame_id -> 报文名（来自 DBC）
        self._msg_names: dict[int, str] = {}
        # frame_id -> 当前/上一次原始（未缩放）解码值，用于十六进制列展示
        self._cur_raw: dict[int, dict] = {}
        self._prev_raw: dict[int, dict] = {}
        # {sig_name: {int_val: "描述"}}，来自 DBC+Excel 合并（主窗口派发）
        self._value_descriptions: dict = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # ─── 工具栏：清除 / 录制 / 路径 ───
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self._clear_btn = QPushButton("清除")
        self._clear_btn.clicked.connect(self._on_clear)
        bar.addWidget(self._clear_btn)

        self._pause_btn = QPushButton("▶ 开始监听")
        self._pause_btn.setProperty("class", "primary")
        self._pause_btn.setToolTip("连接 CAN 设备后自动监听；点击可暂停/恢复监听")
        self._pause_btn.clicked.connect(self._on_toggle_listen)
        bar.addWidget(self._pause_btn)

        self._rec_btn = QPushButton("开始录制")
        self._rec_btn.setProperty("class", "primary")
        self._rec_btn.clicked.connect(self._on_start_record)
        bar.addWidget(self._rec_btn)

        self._stop_rec_btn = QPushButton("停止录制")
        self._stop_rec_btn.setEnabled(False)
        self._stop_rec_btn.clicked.connect(self._on_stop_record)
        bar.addWidget(self._stop_rec_btn)

        bar.addWidget(QLabel("录制路径:"))
        self._rec_path = QLineEdit()
        self._rec_path.setPlaceholderText(
            "默认：进程同级目录/CANLOG_年月日_时分秒.blf"
        )
        bar.addWidget(self._rec_path, stretch=1)

        self._browse_btn = QPushButton("浏览")
        self._browse_btn.clicked.connect(self._on_browse)
        bar.addWidget(self._browse_btn)

        bar.addStretch()
        self._status_label = QLabel("未连接")
        self._status_label.setStyleSheet("color: #9090a0;")
        bar.addWidget(self._status_label)

        layout.addLayout(bar)

        # ─── 树：同 ID 单行（双击就地展开信号）───
        self._tree = QTreeWidget()
        self._tree.setColumnCount(6)
        self._tree.setHeaderLabels([
            "报文ID / 信号名",
            "名称 / 十六进制值",
            "DLC / 十进制值",
            "数据(Hex) / 单位",
            "计数 / 信号描述",
            "最近时间(s) / 上次值",
        ])
        self._tree.setColumnWidth(0, 110)
        self._tree.setColumnWidth(1, 160)
        self._tree.setColumnWidth(2, 160)
        self._tree.setColumnWidth(3, 110)
        self._tree.setColumnWidth(4, 230)
        self._tree.setColumnWidth(5, 110)
        self._tree.setAlternatingRowColors(True)
        self._tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        # 双击报文行就地展开/折叠（QTreeWidget 默认行为），展开时补齐子项
        self._tree.itemExpanded.connect(self._on_item_expanded)
        layout.addWidget(self._tree, stretch=1)

    # ─────────────── 公共接口 ───────────────

    def set_dbc_path(self, dbc_path: str):
        self._dbc_path = dbc_path
        self._db = None
        self._msg_names.clear()
        self._child_items.clear()
        if dbc_path:
            db, err = load_dbc(dbc_path)
            if db is not None:
                self._db = db
                for m in db.messages:
                    self._msg_names[m.frame_id] = m.name

    def set_connection(self, interface_type: str, channel: str, bitrate: int):
        self._interface_type = interface_type
        self._channel = channel
        self._bitrate = bitrate

    def set_connection_manager(self, manager: CanConnectionManager):
        """注入进程内共享连接管理器（总线由它统一持有，本页只取用不自建）"""
        self._manager = manager
        # 连接状态变化时自动开始/停止监听：连接上 CAN 即显示报文，断开即停止
        self._manager.state_changed.connect(self._on_conn_state_changed)

    def _on_conn_state_changed(self, connected: bool, info: str):
        """连接建立后自动开始监听并显示报文；断开后自动停止监听。

        自动连接采用管理器当前已生效的配置（而非本页可能尚未同步的配置），
        避免「连接状态」页先建总线、本页配置滞后导致的「配置不一致」误报。
        若用户此前手动暂停了监听（_user_paused），重连时不再自动恢复，
        尊重用户「暂停监听」的意图。
        """
        if connected:
            if not self._capturing and not self._user_paused:
                cfg = self._manager.get_config()
                if cfg:
                    self.start_capture(
                        cfg["interface_type"], cfg["channel"], cfg["bitrate"]
                    )
                else:
                    self.start_capture()
            elif self._user_paused:
                self._status_label.setText("已连接（已暂停监听）")
        else:
            if self._capturing:
                self.stop_capture()
            # 注意：此处【不清空】 _user_paused，使「用户手动暂停」的意图在设备
            # 短暂掉线重连后仍然生效（重连不自动恢复监听）；点击「开始监听」
            # 按钮才会清除该标记并恢复监听。
            self._status_label.setText("已断开")

    def start_capture(self, interface_type: str | None = None,
                      channel: str | None = None, bitrate: int | None = None):
        if interface_type is not None:
            self._interface_type = interface_type
        if channel is not None:
            self._channel = channel
        if bitrate is not None:
            self._bitrate = bitrate
        if self._capturing:
            return
        if self._manager is None:
            QMessageBox.critical(self, "实时报文错误", "连接管理器未初始化")
            return
        # 通过共享管理器建立/复用唯一总线（未连则自动连接，避免“忘了点连接”）
        bus, err = self._manager.ensure_connected(
            self._interface_type, self._channel, self._bitrate
        )
        if bus is None:
            QMessageBox.critical(self, "实时报文错误", err)
            self._status_label.setText("连接失败")
            return
        self._capture_worker = CanRawCaptureWorker(
            self._interface_type, self._channel, self._bitrate
        )
        self._capture_worker.frame_received.connect(self._on_frame, Qt.QueuedConnection)
        self._capture_worker.status_changed.connect(self._on_status, Qt.QueuedConnection)
        self._capture_worker.error_occurred.connect(self._on_error, Qt.QueuedConnection)
        self._capture_worker.start_monitoring()
        # 注册为共享总线的收帧监听者
        self._manager.add_listener(self._capture_worker.process_message)
        self._capturing = True
        self._update_pause_button()

    def stop_capture(self):
        if self._capture_worker is not None and self._manager is not None:
            # 注销收帧监听者（无监听者时管理器自动停收帧线程）；共享总线不自关
            self._manager.remove_listener(self._capture_worker.process_message)
            self._capture_worker.stop()
            self._capture_worker = None
        self._capturing = False
        self._status_label.setText("已停止")
        self._update_pause_button()

    def _on_toggle_listen(self):
        """暂停/恢复监听的按钮回调。

        暂停只移除本页收帧监听者并停收帧线程，不关闭共享总线（其他页仍可用），
        符合「可暂停监听」诉求；恢复时复用共享连接重新开始收帧。
        """
        if self._capturing:
            # 暂停监听（共享总线不关闭）
            self._user_paused = True
            self.stop_capture()
            self._status_label.setText("已暂停监听")
        else:
            # 恢复监听（若设备未连接则先自动连接）
            self._user_paused = False
            cfg = self._manager.get_config() if self._manager else None
            if cfg:
                self.start_capture(cfg["interface_type"], cfg["channel"],
                                    cfg["bitrate"])
            else:
                self.start_capture()
            self._status_label.setText("监听中" if self._capturing else "连接失败")

    def _update_pause_button(self):
        """根据当前监听状态刷新暂停/开始按钮的文案与样式。"""
        if getattr(self, "_pause_btn", None) is None:
            return
        if self._capturing:
            self._pause_btn.setText("⏸ 暂停监听")
            self._pause_btn.setProperty("class", "")
        else:
            self._pause_btn.setText("▶ 开始监听")
            self._pause_btn.setProperty("class", "primary")
        # 触发样式刷新（class 属性变化需 unpolish/polish 才生效）
        self._pause_btn.style().unpolish(self._pause_btn)
        self._pause_btn.style().polish(self._pause_btn)

    # ─────────────── 录制控制 ───────────────

    def _process_dir(self) -> str:
        # 进程同级目录（开发态为 CanMsgParser 目录）
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _on_browse(self):
        d = QFileDialog.getExistingDirectory(self, "选择录制文件保存目录")
        if d:
            self._rec_path.setText(d)

    def _on_start_record(self):
        if not self._capturing:
            # 未监听则先自动开始监听（使用当前通道/波特率）
            self.start_capture()
        if self._capture_worker is None:
            return
        base = self._rec_path.text().strip()
        if not base:
            base = self._process_dir()
        if os.path.isfile(base):
            base = os.path.dirname(base)
        os.makedirs(base, exist_ok=True)
        name = f"CANLOG_{datetime.now():%Y%m%d_%H%M%S}.blf"
        full = os.path.join(base, name)
        self._rec_path.setText(full)
        self._capture_worker.start_recording(full)
        self._recording = True
        self._rec_btn.setEnabled(False)
        self._stop_rec_btn.setEnabled(True)
        self._status_label.setText(f"录制中: {os.path.basename(full)}")

    def _on_stop_record(self):
        if self._capture_worker is not None:
            self._capture_worker.stop_recording()
        self._recording = False
        self._rec_btn.setEnabled(True)
        self._stop_rec_btn.setEnabled(False)

    # ─────────────── 帧处理 ───────────────

    def _on_frame(self, rel_time, can_id, dlc, data, is_ext, is_fd):
        hex_str = " ".join(f"{b:02X}" for b in data)
        name = self._msg_names.get(can_id, "")
        expanded = False

        top = self._rows.get(can_id)
        if top is None:
            top = QTreeWidgetItem(self._tree)
            self._rows[can_id] = top
            top.setText(0, f"0x{can_id:03X}")
            top.setText(1, name)
            top.setText(2, str(dlc))
            top.setText(3, hex_str)
            top.setText(4, "1")
            top.setText(5, f"{rel_time:.3f}")
            # 添加占位子项，使展开箭头可见，用户才能双击展开解码信号
            placeholder = QTreeWidgetItem(top)
            placeholder.setText(1, "（双击展开解码信号）")
            placeholder.setForeground(1, QColor("#777777"))
            self._child_items[can_id] = {}
        else:
            if top.text(1) != name:
                top.setText(1, name)
            top.setText(2, str(dlc))
            top.setText(3, hex_str)
            cnt = int(top.text(4)) + 1
            top.setText(4, str(cnt))
            top.setText(5, f"{rel_time:.3f}")
            expanded = top.isExpanded()

        # 记录原始数据并在有 DBC 时维护「上一次的值」
        self._last_data[can_id] = data
        if self._db is not None:
            decoded = decode_frame(self._db, can_id, data)
            raw_decoded = self._decode_raw(self._db, can_id, data)
            if decoded:
                self._prev_values[can_id] = self._cur_values.get(can_id, {})
                self._prev_raw[can_id] = self._cur_raw.get(can_id, {})
                self._cur_values[can_id] = decoded
                self._cur_raw[can_id] = raw_decoded
                if expanded:
                    self._refresh_children(
                        can_id, decoded, raw_decoded,
                        self._prev_values.get(can_id, {}),
                        self._prev_raw.get(can_id, {}),
                    )

    def _ensure_children(self, can_id: int):
        """为某报文构建/刷新解码信号子项（仅在展开时调用）。

        与「报文表格」页一致的展示：子项列 = 信号名 / 十六进制值(左) /
        十进制值(右，含枚举名) / 单位 / 信号描述 / 上次值。
        """
        top = self._rows.get(can_id)
        if top is None:
            return
        # 已有真实解码子项（非空 dict）则不重建，避免重复叠加
        existing = self._child_items.get(can_id)
        if existing:
            return
        # 清除占位 / 旧子项
        while top.childCount() > 0:
            top.removeChild(top.child(0))

        data = self._last_data.get(can_id, b"")
        if self._db is None:
            note = QTreeWidgetItem(top)
            note.setText(1, "（未加载 DBC，无法解码）")
            note.setForeground(1, QColor("#777777"))
            self._child_items[can_id] = {}
            return
        decoded = decode_frame(self._db, can_id, data)
        raw_decoded = self._decode_raw(self._db, can_id, data)
        children = {}
        if decoded:
            prev = self._cur_values.get(can_id, {})
            for sig_name, val in decoded.items():
                child = QTreeWidgetItem(top)
                child.setText(0, sig_name)
                child.setText(1, self._raw_to_hex(raw_decoded.get(sig_name)))  # 十六进制值(左)
                child.setText(2, self._val_to_text(val))                      # 十进制值(右)
                child.setText(3, self._unit_of(can_id, sig_name))
                child.setText(4, self._desc_of(can_id, sig_name,
                                               raw_decoded.get(sig_name)))
                child.setText(5, self._val_to_text(prev.get(sig_name)))
                # 与报文表格页一致的着色，便于阅读
                child.setForeground(0, QColor("#4fc3f7"))  # 信号名
                child.setForeground(1, QColor("#ffd54f"))  # 十六进制
                child.setForeground(2, QColor("#66bb6a"))  # 十进制
                child.setForeground(4, QColor("#ba9ffb"))  # 描述
                children[sig_name] = child
        else:
            note = QTreeWidgetItem(top)
            note.setText(1, "（无可解码信号）")
            note.setForeground(1, QColor("#777777"))
        self._child_items[can_id] = children

    def _refresh_children(self, can_id: int, decoded: dict, raw_decoded: dict,
                          prev: dict, prev_raw: dict):
        children = self._child_items.get(can_id)
        if children is None:
            self._ensure_children(can_id)
            children = self._child_items.get(can_id, {})
        for sig_name, child in children.items():
            child.setText(1, self._raw_to_hex(raw_decoded.get(sig_name)))  # 十六进制值(左)
            child.setText(2, self._val_to_text(decoded.get(sig_name)))      # 十进制值(右)
            child.setText(4, self._desc_of(can_id, sig_name,
                                           raw_decoded.get(sig_name)))
            child.setText(5, self._val_to_text(prev.get(sig_name)))

    @staticmethod
    def _val_to_text(val) -> str:
        # cantools 新版本对枚举信号返回 NamedSignalValue，统一转成可读文本
        if val is None:
            return ""
        if hasattr(val, "name") and hasattr(val, "value"):
            return f"{val.value} ({val.name})"
        return str(val)

    @staticmethod
    def _raw_to_hex(val) -> str:
        """把信号的原始（未缩放）整数值格式化为十六进制字符串。"""
        if val is None:
            return ""
        try:
            return f"0x{int(val):X}"
        except (ValueError, TypeError):
            return ""

    def _decode_raw(self, db, can_id: int, data: bytes) -> dict:
        """解码出每个信号的原始（未缩放、未映射枚举名）整数值，用于十六进制列。"""
        try:
            msg = db.get_message_by_frame_id(can_id)
            return dict(msg.decode(data, decode_choices=False, scaling=False))
        except Exception:  # noqa: BLE001
            return {}

    def _desc_of(self, can_id: int, sig_name: str, raw_val) -> str:
        """返回某信号当前原始值对应的描述（DBC choices 优先，Excel 补充）。

        描述来源：主窗口已将 DBC choices 与 Excel 值描述合并进
        self._value_descriptions（DBC 优先覆盖 Excel）。

        cantools 42 中 signal.choices 的值是 NamedSignalValue（str 子类），
        而 PyQt5 的 setText 拒绝 str 子类，因此所有返回值都用 str() 归一化。
        """
        if raw_val is None:
            return ""
        try:
            key = int(raw_val)
        except (ValueError, TypeError):
            return ""
        # 主窗口派发的描述字典（DBC choices 优先 + Excel 补充）
        desc = self._value_descriptions.get(sig_name, {}).get(key)
        if desc:
            return str(desc)
        # 现场兜底：直接查 DBC signal.choices（同样可能是 NamedSignalValue）
        if self._db is not None:
            try:
                msg = self._db.get_message_by_frame_id(can_id)
                for s in msg.signals:
                    if s.name == sig_name and s.choices:
                        choice = s.choices.get(key)
                        if choice is not None:
                            return str(choice)
            except Exception:  # noqa: BLE001
                pass
        return ""

    def set_value_descriptions(self, descriptions: dict):
        """接收 DBC+Excel 合并的值描述 {sig_name: {int_val: 描述}}，
        用于报文树「信号描述」列。"""
        self._value_descriptions = descriptions or {}

    def _unit_of(self, can_id: int, sig_name: str) -> str:
        try:
            m = self._db.get_message_by_frame_id(can_id)
            for s in m.signals:
                if s.name == sig_name:
                    return s.unit or ""
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _on_item_expanded(self, item: QTreeWidgetItem):
        # 通过顶层项反查 frame_id
        for can_id, top in self._rows.items():
            if top is item:
                self._ensure_children(can_id)
                decoded = self._cur_values.get(can_id, {})
                raw_decoded = self._cur_raw.get(can_id, {})
                self._refresh_children(can_id, decoded, raw_decoded,
                                       self._prev_values.get(can_id, {}),
                                       self._prev_raw.get(can_id, {}))
                return

    def _on_clear(self):
        self._tree.clear()
        self._rows.clear()
        self._child_items.clear()
        self._cur_values.clear()
        self._prev_values.clear()
        self._cur_raw.clear()
        self._prev_raw.clear()
        self._last_data.clear()

    # ─────────────── 状态/错误 ───────────────

    def _on_status(self, text):
        self._status_label.setText(text)

    def _on_error(self, text):
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.critical(self, "实时报文错误", text)
        self.stop_capture()
        self._rec_btn.setEnabled(True)
        self._stop_rec_btn.setEnabled(False)

    def closeEvent(self, event):
        self.stop_capture()
        if self._capture_worker is not None:
            self._capture_worker.stop_recording()
        super().closeEvent(event)

    def stop(self):
        self.stop_capture()
