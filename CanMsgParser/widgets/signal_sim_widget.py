# widgets/signal_sim_widget.py
"""信号模拟上报页（重构）：通过「连接状态」页提供的通道/波特率连接 PCAN，
周期性发送「已选信号列表」中的信号报文。

布局（参考 VehicleTMasterProj 批量上行模拟页）：
- 左侧：已选信号列表（可删除）— 信号由「信号分组」窗分发按钮添加
- 右侧：控制（开始/停止）+ 数值表 + 发送日志

数值表列（每信号一行，可单独设置周期）：
信号(报文) | CANID | 换算公式 | 模拟值 | 手动值 | DBC周期(ms) |
实际周期(ms) | 自动递增 | 状态 | 详情 | 操作

通道/波特率不再由本页设置，统一由「连接状态」页提供（set_connection）。
发送按 CAN ID 分组：同一报文内的多个信号聚合为一帧，按该组最小「实际周期」
周期性编码发送；支持每信号「自动递增」与「手动值」覆盖。
"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QPushButton, QLabel,
    QMessageBox, QAbstractItemView, QListWidget, QListWidgetItem,
    QTableWidget, QTableWidgetItem, QDoubleSpinBox, QSpinBox, QCheckBox,
    QLineEdit, QHeaderView, QComboBox,
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor

import can
import re

from core.can_utils import (
    load_dbc, DEFAULT_CHANNEL, DEFAULT_BITRATE, DEFAULT_INTERFACE_TYPE,
)
from core.can_connection import CanConnectionManager
from core.can_data import MessageDef, SignalDef


# 列索引
COL_SIG = 0
COL_CAN_ID = 1
COL_FORMULA = 2
COL_VALUE = 3
COL_MANUAL = 4
COL_DBC_CYCLE = 5
COL_ACTUAL_CYCLE = 6
COL_RAMP = 7
COL_STATUS = 8
COL_DETAIL = 9
COL_ACTION = 10
NUM_COLS = 11


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
        # key -> 行数据
        self._row_data: dict = {}
        # 信号名 -> Excel 矩阵值库(SignalValueInfo)，用于枚举下拉与范围 mock
        self._value_db: dict = {}
        # CAN ID -> 组定时器； CAN ID -> 当前生效的组 key 列表（可被单信号停止移除）
        self._group_timers: dict = {}
        self._group_keys: dict = {}
        # key -> 单信号定时器
        self._single_timers: dict = {}
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

        self._value_table = QTableWidget()
        self._value_table.setColumnCount(NUM_COLS)
        self._value_table.verticalHeader().setDefaultSectionSize(26)
        self._value_table.setHorizontalHeaderLabels([
            "信号(报文)", "CANID", "换算公式", "模拟值", "手动值",
            "DBC周期(ms)", "实际周期(ms)", "自动递增", "状态", "详情", "操作",
        ])
        hdr = self._value_table.horizontalHeader()
        hdr.setSectionResizeMode(COL_SIG, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(COL_CAN_ID, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(COL_FORMULA, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(COL_VALUE, QHeaderView.Interactive)
        hdr.setSectionResizeMode(COL_MANUAL, QHeaderView.Interactive)
        hdr.setSectionResizeMode(COL_DBC_CYCLE, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(COL_ACTUAL_CYCLE, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(COL_RAMP, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(COL_DETAIL, QHeaderView.Stretch)
        hdr.setSectionResizeMode(COL_ACTION, QHeaderView.Fixed)
        self._value_table.setColumnWidth(COL_SIG, 240)
        self._value_table.setColumnWidth(COL_VALUE, 120)
        self._value_table.setColumnWidth(COL_MANUAL, 90)
        self._value_table.setColumnWidth(COL_DETAIL, 160)
        self._value_table.setColumnWidth(COL_ACTION, 64)
        self._value_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._value_table.setAlternatingRowColors(True)
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

            row = self._value_table.rowCount()
            self._value_table.insertRow(row)

            name_item = QTableWidgetItem(f"{sig_name}  ({msg_name})")
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            name_item.setData(Qt.UserRole, key)
            self._value_table.setItem(row, COL_SIG, name_item)

            id_item = QTableWidgetItem(f"0x{frame_id:03X}" if frame_id is not None else "")
            id_item.setFlags(id_item.flags() & ~Qt.ItemIsEditable)
            self._value_table.setItem(row, COL_CAN_ID, id_item)

            formula_item = QTableWidgetItem(self._formula_text(sdef) if sdef else "")
            formula_item.setFlags(formula_item.flags() & ~Qt.ItemIsEditable)
            self._value_table.setItem(row, COL_FORMULA, formula_item)

            # 模拟值：枚举下拉（来自信号矩阵 choices）+ 「手动模拟」项。
            # 选中枚举时按 raw 整数发送；选中「手动模拟」时启用手动值输入框。
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
            self._value_table.setCellWidget(row, COL_VALUE, value_combo)

            manual_edit = QLineEdit()
            manual_edit.setPlaceholderText("手动原始值(支持0x)")
            self._value_table.setCellWidget(row, COL_MANUAL, manual_edit)

            dbc_item = QTableWidgetItem(str(dbc_cycle))
            dbc_item.setFlags(dbc_item.flags() & ~Qt.ItemIsEditable)
            self._value_table.setItem(row, COL_DBC_CYCLE, dbc_item)

            cycle_spin = QSpinBox()
            cycle_spin.setRange(10, 5000)
            cycle_spin.setValue(dbc_cycle)
            cycle_spin.setSuffix(" ms")
            self._value_table.setCellWidget(row, COL_ACTUAL_CYCLE, cycle_spin)

            ramp_chk = QCheckBox()
            ramp_chk.setEnabled(hi > lo)
            self._value_table.setCellWidget(row, COL_RAMP, ramp_chk)

            status_item = QTableWidgetItem("停止")
            status_item.setFlags(status_item.flags() & ~Qt.ItemIsEditable)
            self._value_table.setItem(row, COL_STATUS, status_item)

            detail_item = QTableWidgetItem("")
            detail_item.setFlags(detail_item.flags() & ~Qt.ItemIsEditable)
            self._value_table.setItem(row, COL_DETAIL, detail_item)

            action_btn = QPushButton("模拟")
            action_btn.setFixedHeight(22)
            action_btn.setMaximumWidth(60)
            action_btn.setStyleSheet(
                "QPushButton{padding:1px 4px;font-size:11px;}"
            )
            action_btn.clicked.connect(
                lambda _checked, k=key: self._toggle_single(k)
            )
            self._value_table.setCellWidget(row, COL_ACTION, action_btn)

            self._row_data[key] = {
                "sdef": sdef, "frame_id": frame_id, "unit": unit,
                "value_combo": value_combo, "manual_edit": manual_edit,
                "ramp_chk": ramp_chk, "cycle_spin": cycle_spin,
                "status_item": status_item, "detail_item": detail_item,
                "action_btn": action_btn, "timer": None,
            }
            # 初始化手动值输入框可用状态（默认选枚举时禁用）
            self._on_value_mode_changed(key)
        self._refresh_sel_list()

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
            # 停止该信号的单信号定时器
            timer = self._single_timers.pop(key, None)
            if timer is not None:
                timer.stop()
                timer.deleteLater()
            # 若属于组发送，从组中移除（组空则停组定时器）
            if rd is not None:
                fid = rd["frame_id"]
                gkeys = self._group_keys.get(fid)
                if gkeys and key in gkeys:
                    gkeys.remove(key)
                    if not gkeys:
                        gt = self._group_timers.pop(fid, None)
                        if gt is not None:
                            gt.stop()
                            gt.deleteLater()
                        self._group_keys.pop(fid, None)
            # 从表格移除对应行
            for r in range(self._value_table.rowCount()):
                it0 = self._value_table.item(r, COL_SIG)
                if it0 is not None and it0.data(Qt.UserRole) == key:
                    self._value_table.removeRow(r)
                    break

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
        self._value_table.setRowCount(0)
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

    def _send_frame(self, frame_id: int, keys: list):
        """编码并发送一帧（同一 CAN ID 的信号聚合）。

        关键修复（修复「点 A 却报 B 发送失败」的元凶）：
        cantools 的 msg.encode() 要求提供报文中【全部】信号，否则对缺失的
        信号直接抛 KeyError（异常文本恰好是被缺失的信号名）。本工具常只模拟
        上报一帧中的部分信号，因此必须先以各信号的 offset 作为默认值填满整
        帧，再用已选信号的原始值覆盖，最后才编码发送。这样：
          1) 只模拟部分信号时不再因同帧其它信号而编码失败；
          2) 真正取值非法的信号会被正确指名，不再张冠李戴。
        """
        if self._dbc is None or self._bus is None:
            return
        try:
            msg = self._dbc.get_message_by_frame_id(frame_id)
        except Exception:  # noqa: BLE001
            return
        # 1) 先以各信号 offset 填满整帧默认值（offset 为空则用 0）
        raw_signals = {s.name: (s.offset if s.offset else 0) for s in msg.signals}
        # 2) 用已选信号的原始值覆盖
        for key in keys:
            ok, raw, err = self._resolve_raw(key)
            detail = self._row_data[key]["detail_item"]
            status = self._row_data[key]["status_item"]
            if not ok:
                detail.setText(err)
                detail.setForeground(QColor("#FF4444"))
                status.setText("错误")
                status.setForeground(QColor("#FF4444"))
                continue
            sig_name = key[1]
            raw_signals[sig_name] = raw
            detail.setText("")
            detail.setForeground(QColor("#000000"))
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
                rd["status_item"].setText("错误")
                rd["status_item"].setForeground(QColor("#FF4444"))
                if not rd["detail_item"].text():
                    rd["detail_item"].setText(err_msg[:120])

    def _tick_group(self, frame_id: int):
        if not self._sending or self._bus is None:
            return
        keys = self._group_keys.get(frame_id, [])
        if not keys:
            return
        self._send_frame(frame_id, keys)

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
        """该信号当前是否正在被模拟上报（组发送或单信号发送）。"""
        if key in self._single_timers:
            return True
        rd = self._row_data.get(key)
        if rd is None:
            return False
        fid = rd["frame_id"]
        return (
            self._sending
            and fid is not None
            and fid in self._group_keys
            and key in self._group_keys[fid]
        )

    def _refresh_action_button(self, key):
        """按信号实际发送状态刷新操作列按钮文字（模拟 / 停止）。"""
        rd = self._row_data.get(key)
        if rd is None:
            return
        btn = rd.get("action_btn")
        if isinstance(btn, QPushButton):
            btn.setText("停止" if self._is_sending_key(key) else "模拟")

    def _on_start_stop(self):
        if self._sending or self._single_timers:
            self._stop_all()
        else:
            self._start_all()

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

        # 按 CAN ID 分组
        groups: dict = {}
        for key in self._sel_signals:
            fid = self._row_data[key]["frame_id"]
            if fid is None:
                continue
            groups.setdefault(fid, []).append(key)

        self._sending = True
        for fid, keys in groups.items():
            self._group_keys[fid] = list(keys)
            cycle = max(min(self._row_data[k]["cycle_spin"].value() for k in keys), 10)
            timer = QTimer(self)
            timer.timeout.connect(lambda f=fid: self._tick_group(f))
            timer.start(cycle)
            self._group_timers[fid] = timer
            for k in keys:
                st = self._row_data[k]["status_item"]
                is_manual = self._row_data[k]["value_combo"].currentData() == "__manual__"
                st.setText("手动" if is_manual else "发送中")
                st.setForeground(QColor("#44CC44"))
                self._refresh_action_button(k)

        self._start_btn.setText("停止模拟上报")
        self._status_label.setText(
            f"模拟上报中: {len(self._sel_signals)} 信号 / {len(groups)} CAN ID"
        )

    def _stop_all(self):
        self._sending = False
        for timer in self._group_timers.values():
            timer.stop()
            timer.deleteLater()
        self._group_timers.clear()
        self._group_keys.clear()
        for timer in self._single_timers.values():
            timer.stop()
            timer.deleteLater()
        self._single_timers.clear()
        for key, rd in self._row_data.items():
            st = rd["status_item"]
            if st.text() in ("发送中", "手动"):
                st.setText("停止")
                st.setForeground(QColor("#888888"))
            self._refresh_action_button(key)
        # 共享总线由管理器统一持有，本页停止时只释放本地引用，不 shutdown
        self._bus = None
        self._start_btn.setText("开始模拟上报")
        self._status_label.setText("已停止")

    # ────────────────────── 单信号 模拟/停止 ──────────────────────

    def _toggle_single(self, key: tuple):
        if self._is_sending_key(key):
            self._stop_one(key)
        else:
            self._start_single(key)

    def _start_single(self, key: tuple):
        if not self._dbc_path or self._dbc is None:
            QMessageBox.warning(self, "提示", "请先加载 DBC 文件")
            return
        if not self._ensure_bus():
            return
        rd = self._row_data.get(key)
        if rd is None or rd["frame_id"] is None:
            return
        fid = rd["frame_id"]
        # 若该信号仍在组发送中，先从组里移除，避免与单信号定时器重复发送
        keys = self._group_keys.get(fid)
        if keys and key in keys:
            keys.remove(key)
            if not keys:
                timer = self._group_timers.pop(fid, None)
                if timer is not None:
                    timer.stop()
                    timer.deleteLater()
                self._group_keys.pop(fid, None)
                if not self._group_timers:
                    self._sending = False
                    self._start_btn.setText("开始模拟上报")
                    # 共享总线由管理器统一持有，本页只释放本地引用，不 shutdown
                    self._bus = None
                    self._status_label.setText("已停止")

        cycle = max(rd["cycle_spin"].value(), 10)
        timer = QTimer(self)
        timer.timeout.connect(lambda k=key: self._tick_single(k))
        timer.start(cycle)
        self._single_timers[key] = timer
        is_manual = rd["value_combo"].currentData() == "__manual__"
        rd["status_item"].setText("手动" if is_manual else "发送中")
        rd["status_item"].setForeground(QColor("#44CC44"))
        rd["action_btn"].setText("停止")

    def _tick_single(self, key: tuple):
        rd = self._row_data.get(key)
        if rd is None or self._bus is None:
            return
        self._send_frame(rd["frame_id"], [key])

    def _stop_single(self, key: tuple):
        timer = self._single_timers.pop(key, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        rd = self._row_data.get(key)
        if rd is not None:
            rd["status_item"].setText("停止")
            rd["status_item"].setForeground(QColor("#888888"))
            rd["action_btn"].setText("模拟")

    def _stop_one(self, key: tuple):
        """停止单个信号：若是单信号定时器则停定时器；若属于组发送则从组中移除。"""
        if key in self._single_timers:
            self._stop_single(key)
            return
        rd = self._row_data.get(key)
        if rd is None:
            return
        fid = rd["frame_id"]
        keys = self._group_keys.get(fid, [])
        if key in keys:
            keys.remove(key)
        rd["status_item"].setText("停止")
        rd["status_item"].setForeground(QColor("#888888"))
        rd["action_btn"].setText("模拟")
        if not keys:
            timer = self._group_timers.pop(fid, None)
            if timer is not None:
                timer.stop()
                timer.deleteLater()
            self._group_keys.pop(fid, None)
            if not self._group_timers:
                self._sending = False
                self._start_btn.setText("开始模拟上报")
                # 共享总线由管理器统一持有，本页只释放本地引用，不 shutdown
                self._bus = None
                self._status_label.setText("已停止")

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
