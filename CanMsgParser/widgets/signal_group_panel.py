# widgets/signal_group_panel.py
"""信号分组管理面板（"信号分组"视图）：创建/加载/保存分组，管理分组内信号勾选

功能特性：
- 多分组管理（新建、删除、切换）
- JSON 配置文件保存/加载
- DBC 匹配检查（未匹配信号置灰）
- 跨分组搜索（信号名 / 报文名 / 帧 ID / 备注）
- 与"信号检索"视图联动：由信号检索视图的"加入分组"按钮将勾选信号加入当前分组
- 专业深色主题样式（与信号检索视图共用 widgets/theme.DARK_PANEL_QSS）
"""
import json
from dataclasses import dataclass, field
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton,
    QListWidget, QListWidgetItem, QInputDialog, QMessageBox,
    QFileDialog, QLabel, QAbstractItemView, QTreeWidget, QTreeWidgetItem,
    QLineEdit, QCheckBox,
)
from PyQt5.QtCore import pyqtSignal, Qt, QTimer
from PyQt5.QtGui import QColor, QFont
from core.can_data import MessageDef
from widgets.theme import DARK_PANEL_QSS
from widgets.del_key_filter import DelKeyFilter


@dataclass
class SignalRef:
    """分组中的信号引用"""
    msg_name: str
    sig_name: str
    frame_id: str  # hex string like "0x1A0"
    remark: str = ""  # 用户备注（描述信号功能），随配置持久化


@dataclass
class SignalGroup:
    """信号分组"""
    name: str
    signals: list = field(default_factory=list)  # list[SignalRef]


class SignalGroupPanel(QWidget):
    """信号分组管理面板（独立停靠视图"信号分组"）"""

    # 组内勾选状态变化时发射（参数为当前分组已勾选的信号列表）
    checked_changed = pyqtSignal(list)
    # 用户保存分组配置时发射文件路径（用于主窗口记住路径）
    config_saved = pyqtSignal(str)
    # 分发信号到曲线图/实时监控/模拟上报页：(target, [(msg_name, sig_name), ...])
    dispatch_requested = pyqtSignal(str, list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._groups: list[SignalGroup] = []
        self._current_group_idx: int = -1
        self._messages: list[MessageDef] = []  # 当前 DBC 报文定义
        self._search_text: str = ""  # 跨分组搜索关键字（已转小写）
        # 自动保存：已知配置文件路径 + 脏标记（备注/增删/改组触发）
        self._config_path: str = ""
        self._dirty: bool = False
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(2000)
        self._autosave_timer.timeout.connect(self._autosave)
        self._autosave_timer.start()

        self.setStyleSheet(DARK_PANEL_QSS)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # ─── 分组选择栏 ───
        group_bar = QHBoxLayout()
        group_bar.setSpacing(8)

        lbl = QLabel("分组:")
        lbl.setStyleSheet("font-weight: bold;")
        group_bar.addWidget(lbl)

        self._group_combo = QComboBox()
        self._group_combo.setMinimumWidth(180)
        self._group_combo.currentIndexChanged.connect(self._on_group_changed)
        group_bar.addWidget(self._group_combo)

        self._new_btn = QPushButton("+ 新建")
        self._new_btn.setToolTip("创建新的信号分组")
        self._new_btn.clicked.connect(self._create_group)
        group_bar.addWidget(self._new_btn)

        self._save_btn = QPushButton("💾 保存配置")
        self._save_btn.setToolTip("将分组配置保存到 JSON 文件")
        self._save_btn.clicked.connect(self._save_config)
        group_bar.addWidget(self._save_btn)

        self._load_btn = QPushButton("📂 加载配置")
        self._load_btn.setToolTip("从 JSON 文件加载分组配置")
        self._load_btn.clicked.connect(self._load_config)
        group_bar.addWidget(self._load_btn)

        self._delete_btn = QPushButton("🗑 删除分组")
        self._delete_btn.setToolTip("删除当前选中的分组")
        self._delete_btn.setStyleSheet("""
            QPushButton { border-color: #ef5350; color: #ef5350; }
            QPushButton:hover { background-color: #ef5350; color: #1e1e2e; }
        """)
        self._delete_btn.clicked.connect(self._delete_group)
        group_bar.addWidget(self._delete_btn)

        group_bar.addStretch()
        layout.addLayout(group_bar)

        # ─── 组内信号搜索框（跨分组搜索：信号名 / 报文名 / 帧 ID / 备注）───
        self._sig_search = QLineEdit()
        self._sig_search.setPlaceholderText("🔍 搜索信号名 / 报文名 / 备注（跨分组）…")
        self._sig_search.textChanged.connect(self._on_group_search)
        layout.addWidget(self._sig_search)

        # ─── 单一「全选」勾选框（紧贴搜索框下方，与信号检索视图一致）───
        sel_bar = QHBoxLayout()
        sel_bar.setSpacing(8)
        self._check_all_chk = QCheckBox("全选")
        self._check_all_chk.setToolTip(
            "勾选：选中当前可见（含跨分组搜索结果）的所有信号；\n"
            "取消：取消全部可见信号；\n"
            "存在未勾选信号时，本框自动取消勾选"
        )
        self._check_all_chk.stateChanged.connect(
            lambda _s: self._on_check_all(self._check_all_chk.isChecked())
        )
        sel_bar.addWidget(self._check_all_chk)
        sel_bar.addStretch()
        layout.addLayout(sel_bar)

        # ─── 信号列表（跨分组搜索时按所属分组归类；备注列可编辑）───
        self._sig_list = QTreeWidget()
        self._sig_list.setColumnCount(2)
        self._sig_list.setHeaderLabels(["信号", "备注（描述功能）"])
        self._sig_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._sig_list.setAlternatingRowColors(True)
        self._sig_list.setColumnWidth(0, 300)
        self._sig_list.setColumnWidth(1, 220)
        self._sig_list.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._sig_list.itemChanged.connect(self._on_sig_checked)
        layout.addWidget(self._sig_list, stretch=3)

        # ─── 分组内信号分发按钮（作用于分组中已勾选的信号）───
        group_dispatch_bar = QHBoxLayout()
        group_dispatch_bar.setSpacing(6)
        for _target, _label in (
            ("curve", "添加到曲线图"),
            ("monitor", "添加到实时监控"),
            ("sim", "添加到模拟上报"),
        ):
            _btn = QPushButton(_label)
            _btn.clicked.connect(
                lambda _checked=False, t=_target: self._on_group_dispatch(t)
            )
            group_dispatch_bar.addWidget(_btn)
        layout.addLayout(group_dispatch_bar)

        # Delete 键移除选中信号（保留既有 DEL 快捷键移除能力）
        self._del_filter = DelKeyFilter(self._sig_list, self._remove_selected)

    # ────────────────────── 公共接口 ──────────────────────

    def set_messages(self, messages: list[MessageDef]):
        """更新当前 DBC 报文定义，用于匹配检查。"""
        self._messages = messages
        self._refresh_signal_list()

    def add_signals(self, signals):
        """批量添加信号到当前分组。

        兼容两种调用约定：
        - 来自"信号检索"视图的联动：[(msg_name, sig_name), ...]（2 元组），
          frame_id 自动从已加载 DBC（set_messages）查得；
        - 历史/单测直接调用：[(msg_name, sig_name, frame_id_hex), ...]（3 元组），
          以传入的 frame_id 为准。

        Args:
            signals: [(msg_name, sig_name)] 或 [(msg_name, sig_name, frame_id_hex)]
        """
        # 若还没有任何分组，自动创建一个默认分组，避免无目标可添加
        if self._current_group_idx < 0:
            self._groups.append(SignalGroup(name="默认分组"))
            self._refresh_combo()
            self._group_combo.setCurrentIndex(0)

        group = self._groups[self._current_group_idx]
        existing = {(s.msg_name, s.sig_name) for s in group.signals}

        added = False
        for sig in signals:
            if len(sig) >= 3:
                msg_name, sig_name, frame_id = sig[0], sig[1], sig[2]
            else:
                msg_name, sig_name = sig[0], sig[1]
                # 2 元组（来自信号检索视图联动）：从已加载 DBC 反查帧 ID
                frame_id = self._lookup_frame_id(msg_name, sig_name)
            if (msg_name, sig_name) not in existing:
                group.signals.append(SignalRef(msg_name, sig_name, frame_id))
                added = True

        if added:
            self._dirty = True
        self._refresh_signal_list()
        # 更新 combo 显示
        idx = self._current_group_idx
        self._group_combo.setItemText(idx, f"{group.name} ({len(group.signals)} 信号)")

    def _lookup_frame_id(self, msg_name: str, sig_name: str) -> str:
        """从已加载 DBC（self._messages）反查信号的帧 ID（hex 字符串）。

        用于"信号检索"视图以 (msg_name, sig_name) 2 元组传入时的联动；
        DBC 未加载或查不到时返回空串，避免崩溃。
        """
        for msg in self._messages:
            if getattr(msg, "name", None) != msg_name:
                continue
            fid = getattr(msg, "frame_id", None)
            if isinstance(fid, int):
                return f"0x{fid:03X}"
            return str(fid) if fid else ""
        return ""

    def get_current_group_name(self) -> str:
        """返回当前选中分组名称，无分组时返回空字符串"""
        if 0 <= self._current_group_idx < len(self._groups):
            return self._groups[self._current_group_idx].name
        return ""

    def get_checked_signals(self) -> list[tuple[str, str]]:
        """返回当前列表中已勾选的 (msg_name, sig_name) 列表（含跨分组子项）"""
        result = []
        for i in range(self._sig_list.topLevelItemCount()):
            top = self._sig_list.topLevelItem(i)
            sig_ref = top.data(0, Qt.UserRole)
            if sig_ref is not None and (top.flags() & Qt.ItemIsUserCheckable) \
                    and top.checkState(0) == Qt.Checked:
                result.append((sig_ref.msg_name, sig_ref.sig_name))
            for j in range(top.childCount()):
                child = top.child(j)
                cs = child.data(0, Qt.UserRole)
                if cs is not None and (child.flags() & Qt.ItemIsUserCheckable) \
                        and child.checkState(0) == Qt.Checked:
                    result.append((cs.msg_name, cs.sig_name))
        return result

    # ────────────────────── 分发 / 添加 ──────────────────────

    def _on_group_dispatch(self, target: str):
        """把分组中已勾选的信号分发到指定目标页"""
        checked = self.get_checked_signals()
        if not checked:
            QMessageBox.information(self, "提示", "请先在分组中勾选要发送的信号")
            return
        self.dispatch_requested.emit(target, checked)

    # ────────────────────── 分组管理 ──────────────────────

    def _create_group(self):
        name, ok = QInputDialog.getText(self, "新建分组", "分组名称:")
        if ok and name.strip():
            self._groups.append(SignalGroup(name=name.strip()))
            self._dirty = True
            self._refresh_combo()
            self._group_combo.setCurrentIndex(len(self._groups) - 1)

    def _delete_group(self):
        if self._current_group_idx < 0:
            return
        name = self._groups[self._current_group_idx].name
        reply = QMessageBox.question(
            self, "确认删除", f"确定删除分组 '{name}' 吗？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._groups.pop(self._current_group_idx)
            self._dirty = True
            self._refresh_combo()

    def _on_group_changed(self, idx: int):
        # 跨分组搜索态下切换分组无意义：_refresh_signal_list 会显示【全部】分组的
        # 命中项、不随当前分组变化，表现为"切了分组但下方列表没切"。
        # 故切换分组时清空搜索，保证"切分组 → 列表切到该组"始终成立。
        if self._search_text:
            self._search_text = ""
            self._sig_search.blockSignals(True)
            self._sig_search.clear()
            self._sig_search.blockSignals(False)
        self._current_group_idx = idx
        self._refresh_signal_list()

    def _refresh_combo(self):
        self._group_combo.blockSignals(True)
        self._group_combo.clear()
        for g in self._groups:
            self._group_combo.addItem(f"{g.name} ({len(g.signals)} 信号)")
        self._group_combo.blockSignals(False)
        if self._groups:
            self._group_combo.setCurrentIndex(0)
            self._current_group_idx = 0
        else:
            self._current_group_idx = -1
        self._refresh_signal_list()

    def _refresh_signal_list(self):
        """刷新信号列表。

        - 无搜索：显示当前分组信号（顶层项）。
        - 跨分组搜索：遍历所有分组，命中信号按所属分组以「分组标题 + 子项」呈现。
        两种方式均不改动底层分组数据；每次重建会重置勾选状态
        （单次搜索仅保留当前勾选，满足"重新搜索即恢复不勾选"）。
        """
        self._sig_list.blockSignals(True)
        self._sig_list.clear()

        # 构建 DBC 查找索引
        dbc_lookup = {}
        for msg in self._messages:
            for sig in msg.signals:
                dbc_lookup[(msg.name, sig.name)] = True
        self._dbc_lookup = dbc_lookup

        text = self._search_text
        if text:
            # 跨分组搜索：遍历所有分组，命中信号按所属分组归类展示
            for g in self._groups:
                matches = [s for s in g.signals if self._match_sig(s, text)]
                if not matches:
                    continue
                header = QTreeWidgetItem(self._sig_list)
                header.setText(0, f"{g.name}  ({len(matches)} 信号)")
                header.setFlags(
                    header.flags() & ~Qt.ItemIsSelectable & ~Qt.ItemIsUserCheckable
                )
                header.setForeground(0, QColor("#9090a0"))
                hf = header.font(0)
                hf.setBold(True)
                header.setFont(0, hf)
                for sig_ref in matches:
                    self._add_signal_item(header, sig_ref)
                self._sig_list.expandAll()
        else:
            if self._current_group_idx < 0:
                self._sig_list.blockSignals(False)
                self._update_check_all_state()
                return
            g = self._groups[self._current_group_idx]
            for sig_ref in g.signals:
                self._add_signal_item(None, sig_ref)

        self._sig_list.blockSignals(False)
        # 重新搜索 -> 勾选框归位（单次搜索仅保留当前勾选）
        self._update_check_all_state()

    def _match_sig(self, sig_ref: SignalRef, text: str) -> bool:
        """按信号名 / 报文名 / 帧 ID / 备注内容匹配（不区分大小写）。"""
        if not text:
            return True
        hay = " ".join([
            sig_ref.sig_name, sig_ref.msg_name, sig_ref.frame_id, sig_ref.remark or ""
        ]).lower()
        return text in hay

    def _add_signal_item(self, parent, sig_ref: SignalRef):
        """构建单个信号项（可置于列表顶层或某分组标题下），含可编辑备注列。"""
        item = QTreeWidgetItem(parent) if parent is not None \
            else QTreeWidgetItem(self._sig_list)
        item.setText(0, f"{sig_ref.sig_name}  ({sig_ref.msg_name} · {sig_ref.frame_id})")
        item.setData(0, Qt.UserRole, sig_ref)

        matched = (sig_ref.msg_name, sig_ref.sig_name) in self._dbc_lookup
        if matched:
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            item.setCheckState(0, Qt.Unchecked)
        else:
            # 置灰不可勾选
            item.setFlags(
                item.flags() & ~Qt.ItemIsUserCheckable & ~Qt.ItemIsEnabled
            )
            item.setToolTip(0, "当前 DBC 中未找到此信号")
            item.setForeground(0, QColor("#555560"))

        # 备注列：可编辑 QLineEdit（增大行高避免文字显示不全）
        le = QLineEdit(sig_ref.remark or "")
        le.setPlaceholderText("描述信号功能")
        le.setMinimumHeight(26)
        le.setEnabled(matched)
        le.editingFinished.connect(
            lambda _checked=False, it=item, w=le: self._on_remark_edited(it, w)
        )
        self._sig_list.setItemWidget(item, 1, le)
        return item

    # ────────────────────── 信号操作 ──────────────────────

    def _on_sig_checked(self, item, column):
        """组内信号勾选变化：通知外部页面联动刷新，并刷新全选框状态"""
        self.checked_changed.emit(self.get_checked_signals())
        self._update_check_all_state()

    def _on_group_search(self, text: str):
        """跨分组搜索：按信号名 / 报文名 / 备注过滤（不改动底层数据）。

        每次重新搜索重建列表 -> 已勾选状态归零（单次搜索仅保留当前勾选）。
        """
        self._search_text = (text or "").strip().lower()
        self._refresh_signal_list()

    def _on_remark_edited(self, item, line_edit):
        """备注编辑完成——写回 SignalRef 并标记脏（触发自动保存）。"""
        sig_ref = item.data(0, Qt.UserRole)
        if sig_ref is not None:
            sig_ref.remark = line_edit.text().strip()
            self._dirty = True

    def _remove_selected(self):
        """移除选中的信号（跨分组安全：定位其所属分组后删除）。"""
        selected = self._sig_list.selectedItems()
        if not selected:
            return

        changed = False
        for item in selected:
            sig_ref = item.data(0, Qt.UserRole)
            if sig_ref is None:
                continue
            for g in self._groups:
                before = len(g.signals)
                g.signals = [
                    s for s in g.signals
                    if not (s.msg_name == sig_ref.msg_name
                            and s.sig_name == sig_ref.sig_name)
                ]
                if len(g.signals) != before:
                    changed = True

        if changed:
            self._dirty = True
            self._refresh_signal_list()
            for idx, g in enumerate(self._groups):
                self._group_combo.setItemText(
                    idx, f"{g.name} ({len(g.signals)} 信号)"
                )

    def _on_check_all(self, checked: bool):
        """单一「全选」勾选框：勾选=勾选当前所有可见信号，取消=取消所有可见信号。"""
        self._set_visible_checked(checked)

    def _update_check_all_state(self):
        """刷新「全选」勾选框：仅当所有可见信号项均被勾选时才勾选全选框。"""
        total = 0
        checked = 0
        for i in range(self._sig_list.topLevelItemCount()):
            top = self._sig_list.topLevelItem(i)
            candidates = []
            if top.flags() & Qt.ItemIsUserCheckable:
                candidates.append(top)
            for j in range(top.childCount()):
                candidates.append(top.child(j))
            for it in candidates:
                if (it.flags() & Qt.ItemIsUserCheckable) and not it.isHidden():
                    total += 1
                    if it.checkState(0) == Qt.Checked:
                        checked += 1
        self._check_all_chk.blockSignals(True)
        self._check_all_chk.setChecked(total > 0 and checked == total)
        self._check_all_chk.blockSignals(False)

    def _set_visible_checked(self, checked: bool):
        """批量设置当前可见（未隐藏）信号项的勾选状态。"""
        self._sig_list.blockSignals(True)
        for i in range(self._sig_list.topLevelItemCount()):
            top = self._sig_list.topLevelItem(i)
            items = [top] + [top.child(k) for k in range(top.childCount())]
            for it in items:
                if (it.flags() & Qt.ItemIsUserCheckable) and not it.isHidden():
                    it.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
        self._sig_list.blockSignals(False)
        self._on_sig_checked(None, 0)

    # ────────────────────── 配置文件保存/加载 ──────────────────────

    def _save_config(self):
        if not self._groups:
            QMessageBox.information(self, "提示", "没有分组可保存")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "保存分组配置", "", "JSON Files (*.json)"
        )
        if not path:
            return

        self._write_config(path)
        self._config_path = path
        self._dirty = False
        # 通知主窗口记住此路径
        self.config_saved.emit(path)

    def _write_config(self, path: str):
        """把当前分组配置写入指定 JSON 文件（无 GUI 交互，供手动保存与自动保存共用）。"""
        config = {
            "groups": [
                {
                    "name": g.name,
                    "signals": [
                        {
                            "msg_name": s.msg_name,
                            "sig_name": s.sig_name,
                            "frame_id": s.frame_id,
                            "remark": s.remark,
                        }
                        for s in g.signals
                    ],
                }
                for g in self._groups
            ]
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    def _autosave(self):
        """定时检查脏标记，若已设置配置文件路径则静默自动保存。"""
        if not self._dirty or not self._config_path:
            return
        try:
            self._write_config(self._config_path)
            self._dirty = False
        except Exception:  # noqa: BLE001
            # 自动保存失败不弹窗打断用户，下一次定时仍会重试
            pass

    def _load_config(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "加载分组配置", "", "JSON Files (*.json)"
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                config = json.load(f)

            self._groups.clear()
            for g_data in config.get("groups", []):
                signals = [
                    SignalRef(
                        msg_name=s["msg_name"],
                        sig_name=s["sig_name"],
                        frame_id=s.get("frame_id", ""),
                        remark=s.get("remark", ""),
                    )
                    for s in g_data.get("signals", [])
                ]
                self._groups.append(SignalGroup(name=g_data["name"], signals=signals))

            self._refresh_combo()
            self._config_path = path
            self._dirty = False

        except Exception as e:
            QMessageBox.critical(self, "加载失败", str(e))

    def load_config_from_path(self, path: str):
        """从指定路径加载分组配置（用于启动时自动加载）

        Args:
            path: JSON 配置文件路径

        Raises:
            Exception: 加载或解析失败时抛出异常
        """
        with open(path, "r", encoding="utf-8") as f:
            config = json.load(f)

        self._groups.clear()
        for g_data in config.get("groups", []):
            signals = [
                SignalRef(
                    msg_name=s["msg_name"],
                    sig_name=s["sig_name"],
                    frame_id=s.get("frame_id", ""),
                    remark=s.get("remark", ""),
                )
                for s in g_data.get("signals", [])
            ]
            self._groups.append(SignalGroup(name=g_data["name"], signals=signals))

        self._refresh_combo()
        self._config_path = path
        self._dirty = False
