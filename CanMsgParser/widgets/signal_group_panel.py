# widgets/signal_group_panel.py
"""信号分组管理面板：创建/加载/保存分组，管理分组内信号勾选

功能特性：
- 多分组管理（新建、删除、切换）
- JSON 配置文件保存/加载
- DBC 匹配检查（未匹配信号置灰）
- 全选/全不选/移除选中
- 与信号树联动添加
- 专业深色主题样式
"""
import json
from dataclasses import dataclass, field
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton,
    QListWidget, QListWidgetItem, QInputDialog, QMessageBox,
    QFileDialog, QLabel, QAbstractItemView,
)
from PyQt5.QtCore import pyqtSignal, Qt
from core.can_data import MessageDef
from widgets.signal_tree import SignalTreeWidget


@dataclass
class SignalRef:
    """分组中的信号引用"""
    msg_name: str
    sig_name: str
    frame_id: str  # hex string like "0x1A0"


@dataclass
class SignalGroup:
    """信号分组"""
    name: str
    signals: list = field(default_factory=list)  # list[SignalRef]


class SignalGroupPanel(QWidget):
    """信号分组管理面板，嵌入曲线图 Tab 内"""

    # 组内勾选状态变化时发射（参数为当前分组已勾选的信号列表）
    checked_changed = pyqtSignal(list)
    # 用户保存分组配置时发射文件路径（用于主窗口记住路径）
    config_saved = pyqtSignal(str)
    # 分发信号到曲线图/实时监控/模拟上报页：(target, [(msg_name, sig_name), ...])
    dispatch_requested = pyqtSignal(str, list)

    # ─── QSS 样式表（与设计系统一致） ───
    _QSS = """
        QWidget {
            background-color: #1e1e2e;
            color: #e0e0e0;
            font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
            font-size: 13px;
        }
        QLabel {
            color: #9090a0;
            font-weight: 500;
            padding: 0 4px;
        }
        QComboBox {
            background-color: #2a2a3e;
            color: #e0e0e0;
            border: 1px solid #3a3a4e;
            border-radius: 4px;
            padding: 6px 12px;
            min-height: 28px;
        }
        QComboBox:hover {
            border-color: #4fc3f7;
        }
        QComboBox::drop-down {
            border: none;
            width: 24px;
        }
        QComboBox QAbstractItemView {
            background-color: #252535;
            color: #e0e0e0;
            selection-background-color: #1e3a5a;
            selection-color: #4fc3f7;
            border: 1px solid #3a3a4e;
            outline: none;
        }
        QPushButton {
            background-color: #3a3a4e;
            color: #e0e0e0;
            border: 1px solid #4a4a5e;
            border-radius: 4px;
            padding: 6px 14px;
            min-height: 28px;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: #4a4a5e;
            border-color: #4fc3f7;
        }
        QPushButton:pressed {
            background-color: #2a2a3e;
        }
        QListWidget {
            background-color: #1e1e2e;
            alternate-background-color: #252535;
            color: #e0e0e0;
            border: 1px solid #3a3a4e;
            border-radius: 4px;
            padding: 4px;
            outline: none;
        }
        QListWidget::item {
            padding: 5px 8px;
        }
        QListWidget::item:selected {
            background-color: #1e3a5a;
            color: #4fc3f7;
        }
        QListWidget::item:hover {
            background-color: #2a2a4e;
        }
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._groups: list[SignalGroup] = []
        self._current_group_idx: int = -1
        self._messages: list[MessageDef] = []  # 当前 DBC 报文定义

        self.setStyleSheet(self._QSS)
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

        # ─── 内嵌信号搜索树（公共搜索入口，自带分发按钮）───
        self._embed_tree = SignalTreeWidget()
        # 搜索树的分发信号直接转发给本面板的 dispatch_requested
        self._embed_tree.dispatch_requested.connect(self.dispatch_requested)
        layout.addWidget(self._embed_tree, stretch=2)

        # 把搜索树中勾选的信号加入当前分组
        self._add_tree_to_group_btn = QPushButton("加入分组")
        self._add_tree_to_group_btn.setProperty("class", "primary")
        self._add_tree_to_group_btn.setToolTip("将上方搜索树中勾选的信号加入当前分组")
        self._add_tree_to_group_btn.clicked.connect(self._add_tree_to_group)
        layout.addWidget(self._add_tree_to_group_btn)

        # ─── 信号列表 ───
        self._sig_list = QListWidget()
        self._sig_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._sig_list.setAlternatingRowColors(True)
        layout.addWidget(self._sig_list, stretch=1)

        # ─── 操作按钮栏 ───
        action_bar = QHBoxLayout()
        action_bar.setSpacing(8)

        self._remove_btn = QPushButton("移除选中")
        self._remove_btn.setToolTip("从当前分组中移除选中的信号")
        self._remove_btn.clicked.connect(self._remove_selected)
        action_bar.addWidget(self._remove_btn)

        self._select_all_btn = QPushButton("全选")
        self._select_all_btn.setToolTip("勾选当前分组中所有可用信号")
        self._select_all_btn.clicked.connect(self._select_all_signals)
        action_bar.addWidget(self._select_all_btn)

        self._deselect_all_btn = QPushButton("全不选")
        self._deselect_all_btn.setToolTip("取消所有信号的勾选")
        self._deselect_all_btn.clicked.connect(self._deselect_all_signals)
        action_bar.addWidget(self._deselect_all_btn)

        action_bar.addStretch()
        layout.addLayout(action_bar)

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

        # 勾选变化通知（供曲线图/实时监控/模拟上报页联动）
        self._sig_list.itemChanged.connect(self._on_sig_checked)

    # ────────────────────── 公共接口 ──────────────────────

    def set_messages(self, messages: list[MessageDef]):
        """更新当前 DBC 报文定义，用于匹配检查，并同步给内嵌搜索树"""
        self._messages = messages
        self._embed_tree.load_messages(messages)
        self._refresh_signal_list()

    def add_signals(self, signals: list[tuple[str, str, str]]):
        """批量添加信号到当前分组（由各页的"添加到分组"按钮调用）。

        Args:
            signals: [(msg_name, sig_name, frame_id_hex), ...]
        """
        # 若还没有任何分组，自动创建一个默认分组，避免无目标可添加
        if self._current_group_idx < 0:
            self._groups.append(SignalGroup(name="默认分组"))
            self._refresh_combo()
            self._group_combo.setCurrentIndex(0)

        group = self._groups[self._current_group_idx]
        existing = {(s.msg_name, s.sig_name) for s in group.signals}

        for msg_name, sig_name, frame_id in signals:
            if (msg_name, sig_name) not in existing:
                group.signals.append(SignalRef(msg_name, sig_name, frame_id))

        self._refresh_signal_list()
        # 更新 combo 显示
        idx = self._current_group_idx
        self._group_combo.setItemText(idx, f"{group.name} ({len(group.signals)} 信号)")

    def get_current_group_name(self) -> str:
        """返回当前选中分组名称，无分组时返回空字符串"""
        if 0 <= self._current_group_idx < len(self._groups):
            return self._groups[self._current_group_idx].name
        return ""

    def get_checked_signals(self) -> list[tuple[str, str]]:
        """返回当前分组中已勾选的 (msg_name, sig_name) 列表"""
        result = []
        for i in range(self._sig_list.count()):
            item = self._sig_list.item(i)
            if (item.flags() & Qt.ItemIsUserCheckable) and item.checkState() == Qt.Checked:
                sig_ref = item.data(Qt.UserRole)
                result.append((sig_ref.msg_name, sig_ref.sig_name))
        return result

    # ────────────────────── 分发 / 添加 ──────────────────────

    def _add_tree_to_group(self):
        """把内嵌搜索树中勾选的信号加入当前分组"""
        checked = self._embed_tree.get_checked_signals()
        if not checked:
            QMessageBox.information(self, "提示", "请先在搜索树中勾选信号")
            return
        msg_lookup = {m.name: m for m in self._messages}
        signals = []
        for msg_name, sig_name in checked:
            msg = msg_lookup.get(msg_name)
            frame_id_hex = f"0x{msg.frame_id:03X}" if msg else ""
            signals.append((msg_name, sig_name, frame_id_hex))
        self.add_signals(signals)

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
            self._refresh_combo()

    def _on_group_changed(self, idx: int):
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
        """刷新信号列表，检查 DBC 匹配状态"""
        # 构建期间屏蔽勾选信号，避免刷新触发 checked_changed 误报
        self._sig_list.blockSignals(True)
        self._sig_list.clear()
        if self._current_group_idx < 0:
            self._sig_list.blockSignals(False)
            return

        group = self._groups[self._current_group_idx]

        # 构建 DBC 查找索引
        dbc_lookup = {}
        for msg in self._messages:
            for sig in msg.signals:
                dbc_lookup[(msg.name, sig.name)] = True

        for sig_ref in group.signals:
            key = (sig_ref.msg_name, sig_ref.sig_name)
            matched = key in dbc_lookup

            display_text = f"{sig_ref.sig_name}  ({sig_ref.msg_name} · {sig_ref.frame_id})"
            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, sig_ref)

            if matched:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)
            else:
                # 置灰不可勾选
                item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable & ~Qt.ItemIsEnabled)
                item.setToolTip("当前 DBC 中未找到此信号")
                from PyQt5.QtGui import QColor
                item.setForeground(QColor("#555560"))

            self._sig_list.addItem(item)
        self._sig_list.blockSignals(False)

    # ────────────────────── 信号操作 ──────────────────────

    def _on_sig_checked(self, item):
        """组内信号勾选变化：通知外部页面联动刷新

        注意：_sig_list 是 QListWidget，其 itemChanged 信号仅发射 (item)
        一个参数，因此本槽只接收 item（不要加 column 参数，否则勾选时会
        抛出 "missing 1 required positional argument: 'column'"）。
        """
        self.checked_changed.emit(self.get_checked_signals())

    def _remove_selected(self):
        """移除选中的信号"""
        if self._current_group_idx < 0:
            return

        group = self._groups[self._current_group_idx]
        selected = self._sig_list.selectedItems()
        for item in selected:
            sig_ref = item.data(Qt.UserRole)
            group.signals = [s for s in group.signals if not (
                s.msg_name == sig_ref.msg_name and s.sig_name == sig_ref.sig_name
            )]

        self._refresh_signal_list()
        # 更新 combo 显示
        idx = self._current_group_idx
        self._group_combo.setItemText(idx, f"{group.name} ({len(group.signals)} 信号)")

    def _select_all_signals(self):
        """全选当前分组中所有可勾选的信号"""
        for i in range(self._sig_list.count()):
            item = self._sig_list.item(i)
            if item.flags() & Qt.ItemIsUserCheckable:
                item.setCheckState(Qt.Checked)

    def _deselect_all_signals(self):
        """全不选"""
        for i in range(self._sig_list.count()):
            item = self._sig_list.item(i)
            if item.flags() & Qt.ItemIsUserCheckable:
                item.setCheckState(Qt.Unchecked)

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

        config = {
            "groups": [
                {
                    "name": g.name,
                    "signals": [
                        {
                            "msg_name": s.msg_name,
                            "sig_name": s.sig_name,
                            "frame_id": s.frame_id,
                        }
                        for s in g.signals
                    ],
                }
                for g in self._groups
            ]
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

        # 通知主窗口记住此路径
        self.config_saved.emit(path)

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
                    )
                    for s in g_data.get("signals", [])
                ]
                self._groups.append(SignalGroup(name=g_data["name"], signals=signals))

            self._refresh_combo()

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
                )
                for s in g_data.get("signals", [])
            ]
            self._groups.append(SignalGroup(name=g_data["name"], signals=signals))

        self._refresh_combo()
