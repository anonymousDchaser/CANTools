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

from workers.can_raw_capture_worker import CanRawCaptureWorker
from core.can_utils import load_dbc, decode_frame
from core.can_utils import DEFAULT_CHANNEL, DEFAULT_BITRATE


class RealtimeMessageWidget(QWidget):
    """实时报文监控页（同 ID 单行 + 双击就地展开 + 录制 BLF）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dbc_path = ""
        self._db = None
        self._channel = DEFAULT_CHANNEL
        self._bitrate = DEFAULT_BITRATE
        self._capture_worker = None
        self._capturing = False
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
        self._tree.setHeaderLabels(
            ["报文 ID", "名称", "DLC", "数据 (Hex)", "计数", "最近时间(s)"]
        )
        self._tree.setColumnWidth(0, 110)
        self._tree.setColumnWidth(1, 180)
        self._tree.setColumnWidth(2, 60)
        self._tree.setColumnWidth(3, 300)
        self._tree.setColumnWidth(4, 70)
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

    def set_connection(self, channel: str, bitrate: int):
        self._channel = channel
        self._bitrate = bitrate

    def start_capture(self, channel: str | None = None, bitrate: int | None = None):
        if channel is not None:
            self._channel = channel
        if bitrate is not None:
            self._bitrate = bitrate
        if self._capturing:
            return
        self._capture_worker = CanRawCaptureWorker(self._channel, self._bitrate)
        self._capture_worker.frame_received.connect(self._on_frame)
        self._capture_worker.status_changed.connect(self._on_status)
        self._capture_worker.error_occurred.connect(self._on_error)
        self._capture_worker.start()
        self._capturing = True

    def stop_capture(self):
        if self._capture_worker is not None:
            self._capture_worker.stop()
            self._capture_worker = None
        self._capturing = False
        self._status_label.setText("已停止")

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
        base = self._rec_path.text().strip()
        if not base:
            base = self._process_dir()
        if os.path.isfile(base):
            base = os.path.dirname(base)
        os.makedirs(base, exist_ok=True)
        name = f"CANLOG_{datetime.now():%Y%m%d_%H%M%S}.blf"
        full = os.path.join(base, name)
        self._rec_path.setText(full)
        if self._capture_worker is not None:
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
            if decoded:
                self._prev_values[can_id] = self._cur_values.get(can_id, {})
                self._cur_values[can_id] = decoded
                if expanded:
                    self._refresh_children(can_id, decoded,
                                           self._prev_values.get(can_id, {}))

    def _ensure_children(self, can_id: int):
        """为某报文构建/刷新解码信号子项（仅在展开或有 DBC 时调用）"""
        if can_id in self._child_items:
            return
        data = self._last_data.get(can_id, b"")
        if self._db is None:
            note = QTreeWidgetItem(self._rows[can_id])
            note.setText(0, "（未加载 DBC，无法解码）")
            self._child_items[can_id] = {}
            return
        decoded = decode_frame(self._db, can_id, data)
        children = {}
        if decoded:
            prev = self._cur_values.get(can_id, {})
            for sig_name, val in decoded.items():
                child = QTreeWidgetItem(self._rows[can_id])
                child.setText(0, sig_name)
                child.setText(1, str(val))
                unit = self._unit_of(can_id, sig_name)
                child.setText(2, unit)
                child.setText(3, str(prev.get(sig_name, "")))
                children[sig_name] = child
        else:
            note = QTreeWidgetItem(self._rows[can_id])
            note.setText(0, "（无可解码信号）")
        self._child_items[can_id] = children

    def _refresh_children(self, can_id: int, decoded: dict, prev: dict):
        children = self._child_items.get(can_id)
        if children is None:
            self._ensure_children(can_id)
            children = self._child_items.get(can_id, {})
        for sig_name, child in children.items():
            child.setText(1, str(decoded.get(sig_name, "")))
            child.setText(3, str(prev.get(sig_name, "")))

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
                self._refresh_children(can_id, decoded,
                                       self._prev_values.get(can_id, {}))
                return

    def _on_clear(self):
        self._tree.clear()
        self._rows.clear()
        self._child_items.clear()
        self._cur_values.clear()
        self._prev_values.clear()
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
