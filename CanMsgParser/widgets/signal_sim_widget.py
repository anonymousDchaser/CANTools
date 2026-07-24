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
    QLineEdit, QHeaderView,
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor

import can

from core.can_utils import (
    connect_bus, load_dbc, DEFAULT_CHANNEL, DEFAULT_BITRATE,
)
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
        self._channel = DEFAULT_CHANNEL
        self._bitrate = DEFAULT_BITRATE
        self._bus = None
        self._sending = False
        self._sel_signals: set = set()  # {(msg_name, sig_name)}
        # key -> 行数据
        self._row_data: dict = {}
        # CAN ID -> 组定时器； key -> 单信号定时器
        self._group_timers: dict = {}
        self._single_timers: dict = {}
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
        self._value_table.setColumnWidth(COL_ACTION, 70)
        self._value_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._value_table.setAlternatingRowColors(True)
        right_layout.addWidget(self._value_table, stretch=2)

        right_layout.addWidget(QLabel("发送日志:"))
        self._log_list = QTableWidget()
        self._log_list.setColumnCount(2)
        self._log_list.setHorizontalHeaderLabels(["ID", "Data"])
        self._log_list.setColumnWidth(0, 100)
        self._log_list.setColumnWidth(1, 300)
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

    def set_connection(self, channel: str, bitrate: int):
        """由「连接状态」页注入通道与波特率（本页不再自带控件）"""
        self._channel = channel
        self._bitrate = bitrate

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

            spin = QDoubleSpinBox()
            spin.setRange(-1e9, 1e9)
            spin.setDecimals(3)
            step = max(abs((hi - lo) / 50.0), 0.001) if hi > lo else 1.0
            spin.setSingleStep(step)
            init = sdef.offset if (sdef and sdef.offset) else 0.0
            spin.setValue(init)
            self._value_table.setCellWidget(row, COL_VALUE, spin)

            manual_edit = QLineEdit()
            manual_edit.setPlaceholderText("手动原始值")
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
            action_btn.clicked.connect(
                lambda _checked, k=key: self._toggle_single(k)
            )
            self._value_table.setCellWidget(row, COL_ACTION, action_btn)

            self._row_data[key] = {
                "sdef": sdef, "frame_id": frame_id, "unit": unit,
                "spin": spin, "manual_edit": manual_edit,
                "ramp_chk": ramp_chk, "cycle_spin": cycle_spin,
                "status_item": status_item, "detail_item": detail_item,
                "action_btn": action_btn, "timer": None,
            }
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
            if rd is not None and rd["timer"] is not None:
                rd["timer"].stop()
                rd["timer"].deleteLater()
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

    def _resolve_raw(self, key) -> tuple[bool, int, str]:
        """解析某行的发送原始值。返回 (ok, raw, error_msg)。"""
        rd = self._row_data[key]
        manual = rd["manual_edit"].text().strip()
        if manual:
            try:
                raw = int(manual, 16) if manual.lower().startswith("0x") else int(manual)
                return True, raw, ""
            except ValueError:
                return False, 0, f"非法手动值: {manual}"
        sdef = rd["sdef"]
        physical = rd["spin"].value()
        if sdef is not None and sdef.scale not in (0, 0.0):
            raw = int(round((physical - sdef.offset) / sdef.scale))
        else:
            raw = int(round(physical))
        return True, raw, ""

    def _ensure_bus(self) -> bool:
        if self._bus is not None:
            return True
        bus, err = connect_bus(self._channel, self._bitrate)
        if bus is None:
            self._status_label.setText(f"连接失败: {err}")
            QMessageBox.warning(self, "连接失败", err)
            return False
        self._bus = bus
        return True

    def _send_frame(self, frame_id: int, keys: list):
        """编码并发送一帧（同一 CAN ID 的信号聚合）。"""
        if self._dbc is None or self._bus is None:
            return
        try:
            msg = self._dbc.get_message_by_frame_id(frame_id)
        except Exception:  # noqa: BLE001
            return
        raw_signals: dict = {}
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
        if not raw_signals:
            return
        try:
            data = msg.encode(raw_signals, scaling=False, strict=False)
            self._bus.send(can.Message(
                arbitration_id=frame_id,
                data=data,
                is_extended_id=frame_id > 0x7FF,
            ))
            self._on_frame_sent(frame_id, " ".join(f"{b:02X}" for b in data))
        except Exception as e:  # noqa: BLE001
            self._status_label.setText(f"发送失败: {e}")

    def _tick_group(self, frame_id: int, keys: list):
        if not self._sending or self._bus is None:
            return
        self._advance_ramp(keys)
        self._send_frame(frame_id, keys)

    def _advance_ramp(self, keys: list):
        for key in keys:
            rd = self._row_data[key]
            if not rd["ramp_chk"].isEnabled() or not rd["ramp_chk"].isChecked():
                continue
            sdef = rd["sdef"]
            if sdef is None:
                continue
            lo, hi = sdef.min_val, sdef.max_val
            if hi <= lo:
                continue
            step = max(abs((hi - lo) / 50.0), 1e-3)
            nxt = rd["spin"].value() + step
            if nxt > hi:
                nxt = lo
            rd["spin"].setValue(nxt)

    # ────────────────────── 开始 / 停止（全部）──────────────────────

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

        # 按 CAN ID 分组
        groups: dict = {}
        for key in self._sel_signals:
            fid = self._row_data[key]["frame_id"]
            if fid is None:
                continue
            groups.setdefault(fid, []).append(key)

        for fid, keys in groups.items():
            cycle = max(min(self._row_data[k]["cycle_spin"].value() for k in keys), 10)
            timer = QTimer(self)
            timer.timeout.connect(lambda f=fid, ks=keys: self._tick_group(f, ks))
            timer.start(cycle)
            self._group_timers[fid] = timer
            for k in keys:
                st = self._row_data[k]["status_item"]
                manual = self._row_data[k]["manual_edit"].text().strip()
                st.setText("手动" if manual else "发送中")
                st.setForeground(QColor("#44CC44"))

        self._sending = True
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
        for key, rd in self._row_data.items():
            if rd["timer"] is not None:
                rd["timer"].stop()
                rd["timer"].deleteLater()
                rd["timer"] = None
            st = rd["status_item"]
            if st.text() in ("发送中", "手动"):
                st.setText("停止")
                st.setForeground(QColor("#888888"))
            btn = rd["action_btn"]
            if isinstance(btn, QPushButton):
                btn.setText("模拟")
        if self._bus is not None:
            try:
                self._bus.shutdown()
            except Exception:  # noqa: BLE001
                pass
            self._bus = None
        self._start_btn.setText("开始模拟上报")
        self._status_label.setText("已停止")

    # ────────────────────── 单信号 模拟/停止 ──────────────────────

    def _toggle_single(self, key: tuple):
        if key in self._single_timers:
            self._stop_single(key)
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
        cycle = max(rd["cycle_spin"].value(), 10)
        timer = QTimer(self)
        timer.timeout.connect(lambda k=key: self._tick_single(k))
        timer.start(cycle)
        self._single_timers[key] = timer
        manual = rd["manual_edit"].text().strip()
        rd["status_item"].setText("手动" if manual else "发送中")
        rd["status_item"].setForeground(QColor("#44CC44"))
        rd["action_btn"].setText("停止")

    def _tick_single(self, key: tuple):
        rd = self._row_data.get(key)
        if rd is None or self._bus is None:
            return
        self._advance_ramp([key])
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

    # ────────────────────── 回调 ──────────────────────

    def _on_status(self, text: str):
        self._status_label.setText(text)

    def _on_error(self, text: str):
        QMessageBox.critical(self, "模拟上报错误", text)
        self._stop_all()

    def _on_frame_sent(self, frame_id: int, data_hex: str):
        row = self._log_list.rowCount()
        self._log_list.insertRow(row)
        self._log_list.setItem(row, 0, QTableWidgetItem(f"0x{frame_id:03X}"))
        self._log_list.setItem(row, 1, QTableWidgetItem(data_hex))
        if self._log_list.rowCount() > 200:
            self._log_list.removeRow(0)

    def closeEvent(self, event):
        self._stop_all()
        super().closeEvent(event)

    def stop(self):
        """供主窗口在退出时强制停止后台模拟上报线程"""
        self._stop_all()
