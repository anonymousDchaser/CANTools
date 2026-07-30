# widgets/signal_sim_widget.py
"""信号模拟上报页（重构）：通过「连接状态」页提供的通道/波特率连接 PCAN，
周期性发送「已选信号列表」中的信号报文。

核心设计——按报文 ID 分组（修复「同帧信号互相覆盖/交替上报」）：
- 添加信号后，表格以「报文组（顶层）+ 组内信号（子层）」的树形展示；
  同一 CAN ID 的多个信号归入同一个报文组，不再平铺为独立行。
- 每个报文组有【独立的发送周期】（组行上的 SpinBox 控制），组内所有信号
  按该周期填入【同一帧】一次性编码发送，从根本上杜绝逐信号各发一帧导致
  的取值来回切换（例如 0x3E3 的 A/B 两个信号会一起出现在同一帧里）。
- 组级「发送/停止」按钮控制单个报文组；顶部「开始/停止模拟上报」一键控制全部。
- 信号行不再提供「单信号模拟」按钮（那正是覆盖问题的根源），操作列改为
  「移除该信号」；从组中移除最后一个信号时该报文组自动消失。

通道/波特率不再由本页设置，统一由「连接状态」页提供（set_connection）。
"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QPushButton, QLabel,
    QMessageBox, QAbstractItemView, QListWidget, QListWidgetItem,
    QTableWidget, QTableWidgetItem, QSpinBox, QCheckBox,
    QLineEdit, QHeaderView, QComboBox, QTreeWidget, QTreeWidgetItem,
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor

import can
import re

from core.can_utils import (
    load_dbc, DEFAULT_CHANNEL, DEFAULT_BITRATE, DEFAULT_INTERFACE_TYPE,
)
from core.can_connection import CanConnectionManager
from widgets.del_key_filter import DelKeyFilter
from core.can_data import MessageDef, SignalDef


# 列索引（分组树：顶层=报文组，子层=信号）
COL_SIG = 0        # 信号名 / 报文组标题
COL_FORMULA = 1    # 换算公式 / 组信号数概览
COL_VALUE = 2      # 模拟值下拉 / 组周期 SpinBox
COL_MANUAL = 3     # 手动值 / —
COL_RAMP = 4       # 自动递增 CheckBox / —
COL_STATUS = 5     # 状态 / 组状态
COL_DETAIL = 6     # 详情 / —
COL_ACTION = 7     # 操作（信号：移除 / 组：发送·停止）
NUM_COLS = 8


class SignalSimWidget(QWidget):
    """信号模拟上报页"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._messages: list[MessageDef] = []
        self._dbc = None
        self._dbc_path: str = ""
        self._interface_type = DEFAULT_INTERFACE_TYPE
        self._channel = DEFAULT_CHANNEL
        self._bitrate = DEFAULT_BITRATE
        self._manager: CanConnectionManager | None = None  # 进程内共享连接
        self._sending = False
        self._sel_signals: set = set()  # {(msg_name, sig_name)}
        # key -> 信号行数据（详见 _add_signal_row）
        self._row_data: dict = {}
        # 信号名 -> Excel 矩阵值库(SignalValueInfo)，用于枚举下拉与范围 mock
        self._value_db: dict = {}
        # frame_id -> 报文组信息：
        # {
        #   "msg_name": str,
        #   "keys": list[(msg_name, sig_name)],
        #   "cycle": int,            # 组周期(ms)
        #   "timer": QTimer|None,
        #   "sending": bool,
        #   "item": QTreeWidgetItem,       # 组行
        #   "cycle_spin": QSpinBox,        # 组周期控件
        #   "status_item": QTreeWidgetItem,# 组状态单元
        #   "send_btn": QPushButton,       # 组发送/停止按钮
        # }
        self._groups: dict = {}
        # 发送日志：frame_id -> 行号 / frame_id -> 已发送计数
        self._log_rows: dict = {}
        self._log_counts: dict = {}
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

        # Delete 键移除选中的已选信号（等价于「移除选中」按钮）
        self._del_filter = DelKeyFilter(self._sel_list, self._remove_selected)

        # ─── 右侧：控制 + 数值表 + 日志 ───
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
        self._start_btn = QPushButton("开始模拟上报")
        self._start_btn.setProperty("class", "primary")
        self._start_btn.clicked.connect(self._on_start_stop)
        ctrl.addWidget(self._start_btn)
        right_layout.addLayout(ctrl)

        self._value_table = QTreeWidget()
        self._value_table.setColumnCount(NUM_COLS)
        self._value_table.setRootIsDecorated(True)
        self._value_table.setHeaderLabels([
            "信号 / 报文组", "换算公式 / 信号数", "模拟值 / 组周期",
            "手动值", "自动递增", "状态 / 组状态", "详情", "操作",
        ])
        hdr = self._value_table.header()
        hdr.setSectionResizeMode(COL_SIG, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(COL_FORMULA, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(COL_VALUE, QHeaderView.Interactive)
        hdr.setSectionResizeMode(COL_MANUAL, QHeaderView.Interactive)
        hdr.setSectionResizeMode(COL_RAMP, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(COL_DETAIL, QHeaderView.Stretch)
        hdr.setSectionResizeMode(COL_ACTION, QHeaderView.Fixed)
        self._value_table.setColumnWidth(COL_SIG, 240)
        self._value_table.setColumnWidth(COL_VALUE, 175)
        self._value_table.setColumnWidth(COL_MANUAL, 90)
        self._value_table.setColumnWidth(COL_DETAIL, 160)
        self._value_table.setColumnWidth(COL_ACTION, 90)
        self._value_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._value_table.setAlternatingRowColors(True)
        # Task #4：增大行高，避免「模拟值」下拉文本显示不全
        self._value_table.setStyleSheet(
            "QTreeWidget::item { min-height: 24px; padding: 5px 6px; }"
        )
        right_layout.addWidget(self._value_table, stretch=2)

        right_layout.addWidget(QLabel("发送日志:"))
        self._log_list = QTableWidget()
        self._log_list.setColumnCount(3)
        self._log_list.setHorizontalHeaderLabels(["ID", "Data", "发送计数"])
        self._log_list.setColumnWidth(0, 100)
        self._log_list.setColumnWidth(1, 300)
        self._log_list.setColumnWidth(2, 80)
        self._log_list.setAlternatingRowColors(True)
        right_layout.addWidget(self._log_list, stretch=1)

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
        self._dbc = None
        if dbc_path:
            db, _err = load_dbc(dbc_path)
            self._dbc = db

    def set_connection(self, interface_type: str, channel: str, bitrate: int):
        """由「连接状态」页注入设备类型/通道/波特率（本页不再自带控件）"""
        self._interface_type = interface_type
        self._channel = channel
        self._bitrate = bitrate

    def set_connection_manager(self, manager: CanConnectionManager):
        """注入进程内共享连接管理器（总线由它统一持有，本页只取用不自建）"""
        self._manager = manager

    def set_value_db(self, db: dict):
        """注入 Excel 矩阵值库（dict[信号名, SignalValueInfo]）。

        供「连接状态」页在加载矩阵 xls 后调用，刷新已添加信号的模拟值下拉，
        使枚举下拉与「范围边界 mock」立即可见；未加载 xls 时传入空字典即可。
        """
        self._value_db = db or {}
        self._refresh_value_combos()

    def _refresh_value_combos(self):
        """xls 加载后，用最新值库重建每一行的模拟值下拉（尽量保留原选择）。"""
        for key, rd in self._row_data.items():
            combo = rd["value_combo"]
            prev = combo.currentData()
            choices = self._choices_of(key)
            combo.blockSignals(True)
            combo.clear()
            if choices:
                for raw_val, desc in choices.items():
                    combo.addItem(f"{raw_val} - {desc}", raw_val)
            combo.addItem("手动模拟", "__manual__")
            idx = combo.findData(prev) if prev is not None else -1
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)
            self._on_value_mode_changed(key)

    def add_selected_signals(self, signals: list):
        """由「信号分组」窗分发按钮添加信号（去重，保留已输入数值）"""
        new = [(m, s) for m, s in signals if (m, s) not in self._sel_signals]
        if not new:
            return
        for key in new:
            self._sel_signals.add(key)
        # 延迟追加新行（保留已有行的用户输入值）
        QTimer.singleShot(0, lambda: self._add_table_rows(new))

    # ────────────────────── 查找辅助 ──────────────────────

    def _find_sig_def(self, msg_name: str, sig_name: str) -> SignalDef | None:
        for m in self._messages:
            if m.name == msg_name:
                for s in m.signals:
                    if s.name == sig_name:
                        return s
        return None

    def _frame_id_of(self, msg_name: str) -> int | None:
        for m in self._messages:
            if m.name == msg_name:
                return m.frame_id
        return None

    def _dbc_cycle_of(self, frame_id: int) -> int:
        if self._dbc is not None:
            try:
                ct = self._dbc.get_message_by_frame_id(frame_id).cycle_time
                if ct and ct > 0:
                    return int(ct)
            except Exception:  # noqa: BLE001
                pass
        return 100

    # ────────────────────── 表格行构建 ──────────────────────

    def _formula_text(self, sdef: SignalDef) -> str:
        scale = sdef.scale
        offset = sdef.offset
        if scale == 1 and offset == 0:
            return "x"
        if scale == 1:
            return f"x+{offset}" if offset > 0 else f"x{offset}"
        if offset == 0:
            return f"x*{scale}"
        return f"x*{scale}+{offset}" if offset > 0 else f"x*{scale}{offset}"

    def _add_table_rows(self, keys: list):
        for key in keys:
            if key in self._row_data:
                continue
            msg_name, sig_name = key
            sdef = self._find_sig_def(msg_name, sig_name)
            frame_id = self._frame_id_of(msg_name)
            lo = sdef.min_val if sdef else 0.0
            hi = sdef.max_val if sdef else 100.0
            unit = sdef.unit if sdef else ""
            dbc_cycle = self._dbc_cycle_of(frame_id) if frame_id is not None else 100

            # ── 报文组：首次出现该 frame_id 时创建组行 ──
            grp = self._groups.get(frame_id)
            if grp is None:
                grp_item = QTreeWidgetItem(self._value_table)
                grp_item.setText(COL_SIG, f"📦 报文组 0x{frame_id:03X}  {msg_name}")
                grp_item.setData(COL_SIG, Qt.UserRole, frame_id)
                # 组周期（组内所有信号共享）
                cycle_spin = QSpinBox()
                cycle_spin.setRange(10, 5000)
                cycle_spin.setValue(dbc_cycle)
                cycle_spin.setSuffix(" ms")
                cycle_spin.valueChanged.connect(
                    lambda _v, f=frame_id: self._on_group_cycle_changed(f)
                )
                self._value_table.setItemWidget(grp_item, COL_VALUE, cycle_spin)
                grp_status = QTreeWidgetItem(grp_item)
                grp_status.setText(COL_STATUS, "停止")
                grp_btn = QPushButton("发送")
                grp_btn.setFixedHeight(22)
                grp_btn.setStyleSheet(
                    "QPushButton{padding:1px 4px;font-size:11px;}"
                )
                grp_btn.clicked.connect(
                    lambda _checked, f=frame_id: self._toggle_group(f)
                )
                self._value_table.setItemWidget(grp_item, COL_ACTION, grp_btn)
                grp = {
                    "msg_name": msg_name, "keys": [], "cycle": dbc_cycle,
                    "timer": None, "sending": False, "item": grp_item,
                    "cycle_spin": cycle_spin, "status_item": grp_status,
                    "send_btn": grp_btn,
                }
                self._groups[frame_id] = grp
            grp["keys"].append(key)

            # ── 信号子行 ──
            row = QTreeWidgetItem(grp["item"])
            row.setText(COL_SIG, f"{sig_name}  ({msg_name})")
            row.setData(COL_SIG, Qt.UserRole, key)

            row.setText(COL_FORMULA, self._formula_text(sdef) if sdef else "")
            row.setText(COL_DETAIL, "")
            row.setText(COL_STATUS, "停止")

            # 模拟值：枚举下拉（来自信号矩阵 choices）+ 「手动模拟」项。
            choices = self._choices_of(key)
            value_combo = QComboBox()
            if choices:
                for raw_val, desc in choices.items():
                    value_combo.addItem(f"{raw_val} - {desc}", raw_val)
            value_combo.addItem("手动模拟", "__manual__")
            value_combo.setCurrentIndex(0)
            value_combo.currentIndexChanged.connect(
                lambda _i, k=key: self._on_value_mode_changed(k)
            )
            self._value_table.setItemWidget(row, COL_VALUE, value_combo)

            manual_edit = QLineEdit()
            manual_edit.setPlaceholderText("手动原始值(支持0x)")
            self._value_table.setItemWidget(row, COL_MANUAL, manual_edit)

            ramp_chk = QCheckBox()
            ramp_chk.setEnabled(hi > lo)
            self._value_table.setItemWidget(row, COL_RAMP, ramp_chk)

            remove_btn = QPushButton("移除")
            remove_btn.setFixedHeight(22)
            remove_btn.setMaximumWidth(60)
            remove_btn.setStyleSheet(
                "QPushButton{padding:1px 4px;font-size:11px;}"
            )
            remove_btn.clicked.connect(
                lambda _checked, k=key: self._remove_one_signal(k)
            )
            self._value_table.setItemWidget(row, COL_ACTION, remove_btn)

            self._row_data[key] = {
                "sdef": sdef, "frame_id": frame_id, "unit": unit,
                "value_combo": value_combo, "manual_edit": manual_edit,
                "ramp_chk": ramp_chk, "status_item": row,
                "detail_item": row, "grp_item": grp["item"],
            }
            # 初始化手动值输入框可用状态（默认选枚举时禁用）
            self._on_value_mode_changed(key)
        self._refresh_group_summaries()
        self._refresh_sel_list()
        # 新增信号不改变发送状态（未开始发送），但确保按钮与实际状态一致
        self._refresh_send_button()

    def _refresh_group_summaries(self):
        """刷新各报文组的「信号数」概览列，便于一眼看清组内信号。"""
        for fid, grp in self._groups.items():
            n = len(grp["keys"])
            grp["item"].setText(COL_FORMULA, f"{n} 个信号")

    def _on_group_cycle_changed(self, frame_id: int):
        """组周期 SpinBox 变化时同步到组的周期值；运行中修改即时生效
        （组 timer 每次 timeout 复用最新 grp[\"cycle\"] 作为启动间隔）。"""
        grp = self._groups.get(frame_id)
        if grp is not None:
            grp["cycle"] = grp["cycle_spin"].value()

    def _refresh_sel_list(self):
        self._sel_list.blockSignals(True)
        self._sel_list.clear()
        for msg_name, sig_name in sorted(self._sel_signals):
            item = QListWidgetItem(f"{sig_name}  ({msg_name})")
            item.setData(Qt.UserRole, (msg_name, sig_name))
            self._sel_list.addItem(item)
        self._sel_list.blockSignals(False)

    def _remove_rows(self, keys: set):
        for key in keys:
            rd = self._row_data.pop(key, None)
            if rd is None:
                continue
            fid = rd["frame_id"]
            grp = self._groups.get(fid)
            if grp is not None:
                if key in grp["keys"]:
                    grp["keys"].remove(key)
                # 若组正在发送且已无信号，停止并销毁组
                if not grp["keys"]:
                    if grp["timer"] is not None:
                        grp["timer"].stop()
                        grp["timer"].deleteLater()
                    grp["sending"] = False
                    self._groups.pop(fid, None)
                    idx = self._value_table.indexOfTopLevelItem(grp["item"])
                    if idx >= 0:
                        self._value_table.takeTopLevelItem(idx)
            # 从树中移除信号子行
            grp_item = rd.get("grp_item")
            if grp_item is not None:
                for ci in range(grp_item.childCount()):
                    child = grp_item.child(ci)
                    if child.data(COL_SIG, Qt.UserRole) == key:
                        grp_item.takeChild(ci)
                        break
        self._refresh_group_summaries()
        # 移除信号可能删掉正在发送的报文组，需按实际状态刷新顶部按钮
        self._refresh_send_button()

    def _remove_one_signal(self, key: tuple):
        """信号行「移除」按钮：从所在报文组移除该信号。"""
        self._sel_signals.discard(key)
        self._remove_rows({key})
        self._refresh_sel_list()

    def _remove_selected(self):
        keys = {item.data(Qt.UserRole) for item in self._sel_list.selectedItems()}
        if not keys:
            return
        self._sel_signals -= keys
        self._remove_rows(keys)
        self._refresh_sel_list()

    def _clear_selected(self):
        self._stop_all()
        self._sel_signals.clear()
        self._row_data.clear()
        self._groups.clear()
        self._value_table.clear()
        self._refresh_sel_list()

    # ────────────────────── 值解析与发送 ──────────────────────

    def _on_value_mode_changed(self, key: tuple):
        """下拉框在「枚举 / 手动模拟」间切换时，启用/禁用手动值输入框。"""
        rd = self._row_data.get(key)
        if rd is None:
            return
        is_manual = rd["value_combo"].currentData() == "__manual__"
        rd["manual_edit"].setEnabled(is_manual)

    def _choices_of(self, key: tuple) -> dict:
        """解析该信号的模拟值下拉项（raw 值 -> 描述），按优先级合并来源：

        1) DBC（cantools）枚举优先；
        2) Excel 矩阵枚举（SignalValueInfo.raw_choices）；
        3) Excel 矩阵数值范围 -> 边界测试 mock 原始值（最小/最大/中点等）；
        4) DBC SignalDef.choices 兜底。

        若以上均无，返回空字典，调用方将只显示「手动模拟」。
        """
        # 1) DBC（cantools）枚举优先
        if self._dbc is not None:
            msg_name, sig_name = key
            try:
                msg = self._dbc.get_message_by_name(msg_name)
                sig = msg.get_signal_by_name(sig_name)
                if sig.choices:
                    return dict(sig.choices)
            except Exception:  # noqa: BLE001
                pass
        # 2) Excel 矩阵枚举
        info = self._value_db.get(key[1])
        if info is not None and info.raw_choices:
            return dict(info.raw_choices)
        # 3) Excel 矩阵数值范围 -> 边界测试 mock
        if info is not None:
            mock = info.mock_choices()
            if mock:
                return mock
        # 4) DBC SignalDef.choices 兜底
        sdef = self._find_sig_def(key[0], key[1])
        return dict(sdef.choices) if sdef and sdef.choices else {}

    def _resolve_raw(self, key) -> tuple[bool, int, str]:
        """解析某行的发送原始值。返回 (ok, raw, error_msg)。

        - 下拉选中枚举：raw 即该枚举整数（直接 encode(scaling=False) 发送）；
        - 下拉选中「手动模拟」：解析手动值输入框中的整数（支持 0x 十六进制）。
        """
        rd = self._row_data[key]
        mode = rd["value_combo"].currentData()
        if mode == "__manual__":
            manual = rd["manual_edit"].text().strip()
            if not manual:
                return False, 0, "请填写手动值"
            try:
                raw = int(manual, 16) if manual.lower().startswith("0x") else int(manual)
                return True, raw, ""
            except ValueError:
                return False, 0, f"非法手动值: {manual}"
        # 枚举模式：raw 即下拉选中的整数
        try:
            raw = int(mode)
            return True, raw, ""
        except (TypeError, ValueError):
            return False, 0, "未选择有效枚举值"

    def _ensure_bus(self) -> bool:
        """通过共享管理器建立/复用唯一总线（未连则自动连接）。"""
        if self._manager is None:
            QMessageBox.warning(self, "连接失败", "连接管理器未初始化")
            return False
        bus, err = self._manager.ensure_connected(
            self._interface_type, self._channel, self._bitrate
        )
        if bus is None:
            self._status_label.setText(f"连接失败: {err}")
            QMessageBox.warning(self, "连接失败", err)
            return False
        self._bus = bus
        return True

    @staticmethod
    def _signal_range(sig) -> tuple[int, int]:
        """返回该信号原始值的可表示范围 [lo, hi]（根据位宽与符号位）。"""
        length = getattr(sig, "length", 64) or 64
        if getattr(sig, "is_signed", False):
            return -(2 ** (length - 1)), 2 ** (length - 1) - 1
        return 0, 2 ** length - 1

    def _send_frame(self, frame_id: int, keys: list):
        """编码并发送一帧（同一 CAN ID 的信号聚合）。

        关键修复（修复「点 A 却报 B 发送失败」的元凶）：
        cantools 的 msg.encode() 要求提供报文中【全部】信号，否则对缺失的
        信号直接抛 KeyError（异常文本恰好是被缺失的信号名）。本工具常只模拟
        上报一帧中的部分信号，因此必须先以各信号的可表示范围安全默认值填满
        整帧，再用已选信号的原始值覆盖，最后才编码发送。

        范围安全（修复 #5「can't convert negative int to unsigned」）：
        - 默认填充值钳制进该信号 [lo, hi]，避免无符号信号被负 offset 默认填满；
        - 已选信号原始值若超出 [lo, hi]（如无符号信号取到负值），在此给出友好
          报错并钳制，保证整帧仍能编码发送、不再把异常抛给 cantools 崩溃。
        """
        if self._dbc is None or self._bus is None:
            return
        try:
            msg = self._dbc.get_message_by_frame_id(frame_id)
        except Exception:  # noqa: BLE001
            return
        # 1) 先以各信号的可表示范围安全默认值填满整帧（负 offset 钳制为下限）
        raw_signals = {}
        for s in msg.signals:
            lo, hi = self._signal_range(s)
            dflt = int(s.offset) if s.offset else 0
            if dflt < lo:
                dflt = lo
            elif dflt > hi:
                dflt = hi
            raw_signals[s.name] = dflt
        # 2) 用已选信号的原始值覆盖（越界/负值做友好报错并钳制，保证整帧可编码）
        for key in keys:
            ok, raw, err = self._resolve_raw(key)
            detail = self._row_data[key]["detail_item"]
            status = self._row_data[key]["status_item"]
            sig_name = key[1]
            if not ok:
                detail.setText(COL_DETAIL, err)
                detail.setForeground(COL_DETAIL, QColor("#FF4444"))
                status.setText(COL_STATUS, "错误")
                status.setForeground(COL_STATUS, QColor("#FF4444"))
                continue
            try:
                sig = msg.get_signal_by_name(sig_name)
                lo, hi = self._signal_range(sig)
            except Exception:  # noqa: BLE001
                lo, hi = 0, 2 ** 64 - 1
            if not (lo <= raw <= hi):
                detail.setText(COL_DETAIL, f"值 {raw} 超出可表示范围[{lo},{hi}]")
                detail.setForeground(COL_DETAIL, QColor("#FF4444"))
                status.setText(COL_STATUS, "错误")
                status.setForeground(COL_STATUS, QColor("#FF4444"))
                raw = max(lo, min(raw, hi))
            else:
                detail.setText(COL_DETAIL, "")
                detail.setForeground(COL_DETAIL, QColor("#000000"))
            raw_signals[sig_name] = raw
        try:
            data = msg.encode(raw_signals, scaling=False, strict=False)
            frame = can.Message(
                arbitration_id=frame_id,
                data=data,
                is_extended_id=frame_id > 0x7FF,
            )
            self._bus.send(frame)
            # 把本进程发出的帧主动 fan-out 给所有监听者（监控页/报文页）。
            # 硬件（如 PCAN）默认不回环自身发出的帧，收帧线程收不到，故在此
            # 发送侧主动投递，保证自己模拟的信号也能在监控/报文页实时显示。
            if self._manager is not None:
                self._manager.dispatch(frame)
            self._on_frame_sent(frame_id, " ".join(f"{b:02X}" for b in data))
        except Exception as e:  # noqa: BLE001
            self._status_label.setText(f"发送失败: {e}")
            # 把错误精确定位到具体信号行，避免误导用户
            self._mark_encode_error(str(e), keys, msg, raw_signals)

    def _mark_encode_error(self, err_msg: str, keys: list,
                           msg=None, raw_signals: dict | None = None):
        """从编码异常中定位失败信号，把对应行标红并补充详情。

        定位策略：
        1) 异常文本中若含信号名（如 "Signal 'ADU_XXX' ..." 或 KeyError 的
           "'ADU_XXX'"），按名匹配已选信号；
        2) 若异常未带信号名（如 "Unsigned integer value 2 out of range."），
           则按「原始值是否超出信号位宽可表示范围」反推真正非法的信号；
        3) 都找不到时，保守地把所有已选行标红。
        """
        names = re.findall(r"['\"]([^'\"]+)['\"]", err_msg)
        target = set()
        for n in names:
            for key in keys:
                if key[1] == n:
                    target.add(key)
        if not target and msg is not None and raw_signals is not None:
            for key in keys:
                sig_name = key[1]
                try:
                    sig = msg.get_signal_by_name(sig_name)
                except Exception:  # noqa: BLE001
                    continue
                raw = raw_signals.get(sig_name)
                if raw is None:
                    continue
                length = sig.length
                if getattr(sig, "is_signed", False):
                    lo, hi = -(2 ** (length - 1)), 2 ** (length - 1) - 1
                else:
                    lo, hi = 0, 2 ** length - 1
                if not (lo <= raw <= hi):
                    target.add(key)
        if not target:
            target = set(keys)
        for key in target:
            rd = self._row_data.get(key)
            if rd is not None:
                rd["status_item"].setText(COL_STATUS, "错误")
                rd["status_item"].setForeground(COL_STATUS, QColor("#FF4444"))
                if not rd["detail_item"].text(COL_DETAIL):
                    rd["detail_item"].setText(COL_DETAIL, err_msg[:120])

    def _tick_group(self, frame_id: int):
        if self._bus is None:
            return
        grp = self._groups.get(frame_id)
        if grp is None or not grp["sending"]:
            return
        self._send_frame(frame_id, grp["keys"])

    def _advance_ramp(self, keys: list):
        """手动模拟模式下，对手动原始值做 +1 递增（循环到 0）。

        枚举下拉模式下递增无意义（离散枚举），直接跳过。
        """
        for key in keys:
            rd = self._row_data.get(key)
            if rd is None:
                continue
            if not rd["ramp_chk"].isEnabled() or not rd["ramp_chk"].isChecked():
                continue
            if rd["value_combo"].currentData() != "__manual__":
                continue
            raw_text = rd["manual_edit"].text().strip()
            try:
                cur = int(raw_text, 16) if raw_text.lower().startswith("0x") else int(raw_text)
            except ValueError:
                continue
            nxt = cur + 1
            if nxt > 0xFFFF:
                nxt = 0
            rd["manual_edit"].setText(str(nxt))

    # ────────────────────── 开始 / 停止（全部）──────────────────────

    def _is_sending_key(self, key) -> bool:
        """该信号当前是否正在被模拟上报（其所属报文组正在发送）。"""
        rd = self._row_data.get(key)
        if rd is None:
            return False
        grp = self._groups.get(rd["frame_id"])
        return grp is not None and grp["sending"]

    def _on_start_stop(self):
        # 全局发送中，或任一报文组正在发送，都视为「正在模拟」
        sending_any = self._sending or any(
            g["sending"] for g in self._groups.values()
        )
        if sending_any:
            self._stop_all()
        else:
            self._start_all()

    def _refresh_send_button(self):
        """按实际发送状态刷新顶部「开始/停止模拟上报」按钮（Task #2）。

        状态完全由「是否有报文组正在发送」推导，避免 _sending 与实际发送
        状态脱节——旧实现在某些路径下会让按钮显示「停止」但实际并未发送。

        注意：本方法不负责清空 self._bus。self._bus 指向「连接状态」页注入的
        共享总线（由管理器统一持有），空闲时保留引用无害，下次开始时会由
        _ensure_bus 重新取用；若在此清空，会误伤「添加信号（尚未发送）」等
        仅刷新按钮的调用路径（见回归测试 test_signal_sim_group）。
        """
        sending_any = any(g["sending"] for g in self._groups.values())
        self._sending = sending_any
        self._start_btn.setText("停止模拟上报" if sending_any else "开始模拟上报")

    def _start_all(self):
        if not self._dbc_path or self._dbc is None:
            QMessageBox.warning(self, "提示", "请先加载 DBC 文件")
            return
        if not self._sel_signals:
            QMessageBox.warning(self, "提示", "请先通过「信号分组」窗添加要上报的信号")
            return
        if not self._ensure_bus():
            return

        # 新会话：清空发送日志与计数
        self._log_list.setRowCount(0)
        self._log_rows.clear()
        self._log_counts.clear()

        for fid in list(self._groups.keys()):
            self._start_group(fid)
        self._refresh_send_button()
        self._status_label.setText(
            f"模拟上报中: {len(self._sel_signals)} 信号 / {len(self._groups)} 报文组"
        )

    def _stop_all(self):
        for fid in list(self._groups.keys()):
            self._stop_group(fid)
        self._refresh_send_button()
        self._status_label.setText("已停止")

    # ────────────────────── 报文组 发送/停止 ──────────────────────

    def _toggle_group(self, frame_id: int):
        """报文组「发送/停止」按钮：仅控制该报文组（整帧聚合发送组内信号）。"""
        grp = self._groups.get(frame_id)
        if grp is None:
            return
        if grp["sending"]:
            self._stop_group(frame_id)
        else:
            self._start_group(frame_id)

    def _start_group(self, frame_id: int):
        """启动单个报文组的周期发送：组内所有信号填入同一帧一次性编码发送。"""
        if not self._ensure_bus():
            return
        grp = self._groups.get(frame_id)
        if grp is None or grp["sending"]:
            return
        grp["sending"] = True
        if grp["timer"] is None:
            timer = QTimer(self)
            timer.timeout.connect(lambda f=frame_id: self._tick_group(f))
            grp["timer"] = timer
        grp["timer"].start(grp["cycle"])
        grp["status_item"].setText(COL_STATUS, "发送中")
        grp["status_item"].setForeground(COL_STATUS, QColor("#44CC44"))
        grp["send_btn"].setText("停止")
        for k in grp["keys"]:
            rd = self._row_data.get(k)
            if rd is not None:
                rd["status_item"].setText(COL_STATUS, "发送中")
                rd["status_item"].setForeground(COL_STATUS, QColor("#44CC44"))
        # 同步全局按钮状态：只要任一报文组在发送，顶部即显示「停止模拟上报」
        self._refresh_send_button()
        self._status_label.setText(
            f"模拟上报中: {len(self._sel_signals)} 信号 / {len(self._groups)} 报文组"
        )

    def _stop_group(self, frame_id: int):
        """停止单个报文组的周期发送（释放本地总线引用由全局 _stop_all 负责）。"""
        grp = self._groups.get(frame_id)
        if grp is None:
            return
        grp["sending"] = False
        if grp["timer"] is not None:
            grp["timer"].stop()
        grp["status_item"].setText(COL_STATUS, "停止")
        grp["status_item"].setForeground(COL_STATUS, QColor("#888888"))
        grp["send_btn"].setText("发送")
        for k in grp["keys"]:
            rd = self._row_data.get(k)
            if rd is not None:
                rd["status_item"].setText(COL_STATUS, "停止")
                rd["status_item"].setForeground(COL_STATUS, QColor("#888888"))
        # 若没有任何报文组在发送，复位全局按钮状态（_refresh_send_button 会在
        # 「无发送」时释放本地总线引用并复位按钮文案）
        self._refresh_send_button()
        if not any(g["sending"] for g in self._groups.values()):
            self._status_label.setText("已停止")

    # ────────────────────── 说明 ──────────────────────
    # 信号行的「移除」按钮见 _remove_one_signal；报文组行的「发送/停止」
    # 按钮见 _toggle_group / _start_group / _stop_group。同一报文 ID 的
    # 信号始终聚合在同一帧发送，不再提供单信号独立发送（那会导致同帧其它
    # 信号被默认值覆盖、取值来回切换）。

    # ────────────────────── 回调 ──────────────────────

    def _on_status(self, text: str):
        self._status_label.setText(text)

    def _on_error(self, text: str):
        QMessageBox.critical(self, "模拟上报错误", text)
        self._stop_all()

    def _on_frame_sent(self, frame_id: int, data_hex: str):
        """记录一次成功发送。同一报文（frame_id）在日志中只占一行：
        命中则刷新 Data 并将「发送计数」+1，便于观察周期外发情况。"""
        row = self._log_rows.get(frame_id)
        if row is not None and 0 <= row < self._log_list.rowCount():
            self._log_list.setItem(row, 1, QTableWidgetItem(data_hex))
            cnt = self._log_counts.get(frame_id, 0) + 1
            self._log_counts[frame_id] = cnt
            self._log_list.setItem(row, 2, QTableWidgetItem(str(cnt)))
        else:
            row = self._log_list.rowCount()
            self._log_list.insertRow(row)
            self._log_list.setItem(row, 0, QTableWidgetItem(f"0x{frame_id:03X}"))
            self._log_list.setItem(row, 1, QTableWidgetItem(data_hex))
            self._log_list.setItem(row, 2, QTableWidgetItem("1"))
            self._log_rows[frame_id] = row
            self._log_counts[frame_id] = 1

    def closeEvent(self, event):
        self._stop_all()
        super().closeEvent(event)

    def stop(self):
        """供主窗口在退出时强制停止后台模拟上报线程"""
        self._stop_all()
