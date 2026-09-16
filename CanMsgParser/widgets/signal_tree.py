# widgets/signal_tree.py
"""信号检索视图（独立停靠窗）：搜索 DBC 报文与信号，勾选后分发或加入分组。

布局采用**左右分栏**：左侧是搜索树（+ 分发按钮），右侧是「已勾选信号」列表
（+ 移除/加入分组按钮）。两侧各自纵向占满，避免二者在同一纵向布局里平分高度、
把搜索树挤到只剩一两行。面板较窄时（如停靠在主窗口左侧栏）自动切换为上下堆叠。
"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLineEdit, QTreeWidget, QTreeWidgetItem,
    QPushButton, QHBoxLayout, QListWidget, QListWidgetItem,
    QAbstractItemView, QLabel, QCheckBox, QSplitter, QHeaderView,
)
from PyQt5.QtCore import pyqtSignal, Qt
from core.can_data import MessageDef
from widgets.del_key_filter import DelKeyFilter
from widgets.theme import DARK_PANEL_QSS
from utils.ui_scale import dp


class SignalTreeWidget(QWidget):
    """信号检索视图（独立停靠窗）：浏览/搜索 DBC 报文与信号，勾选后可分发或加入分组"""
    selection_changed = pyqtSignal(list)
    # (target, signals) - target in {"curve","monitor","sim"}
    dispatch_requested = pyqtSignal(str, list)
    # 将已勾选信号加入"信号分组"视图的当前分组
    add_to_group_requested = pyqtSignal(list)

    # 面板宽度低于该设计基准值时，左右分栏切换为上下堆叠（见 _sync_split_orientation）。
    # 取 430：实测停靠列宽 460、490、650 时左右分栏都能给出
    # 更多可见行（树取整个面板高，而不像上下堆叠那样被
    # 已勾选列表分走一半高度）。
    _SPLIT_FLIP_WIDTH = 430

    def __init__(self, parent=None):
        super().__init__(parent)
        self._messages: list[MessageDef] = []
        self._all_items: dict[str, QTreeWidgetItem] = {}
        self.setStyleSheet(DARK_PANEL_QSS)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(dp(6), dp(6), dp(6), dp(6))
        layout.setSpacing(dp(6))

        # ── 顶部工具行：搜索框 + 全选（合并为同一行，省下一整行高度）──
        top_bar = QHBoxLayout()
        top_bar.setSpacing(dp(6))

        # 搜索框（带搜索图标占位提示）
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("\U0001f50d 搜索报文 / 信号 / CAN ID")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(self._on_search)
        top_bar.addWidget(self._search_input, stretch=1)

        # 单一「全选」勾选框（操作当前可见信号）：勾选=全选可见信号，
        # 取消=全不选；任一可见信号取消勾选时，本框自动变为未勾选。
        self._check_all_chk = QCheckBox("全选")
        self._check_all_chk.setToolTip(
            "勾选：选中当前搜索结果中所有可见信号；\n"
            "取消：取消全部可见信号；\n"
            "搜索结果中存在未勾选信号时，本框自动取消勾选"
        )
        self._check_all_chk.stateChanged.connect(
            lambda _s: self._on_check_all(self._check_all_chk.isChecked())
        )
        top_bar.addWidget(self._check_all_chk)
        layout.addLayout(top_bar)

        # ── 主体：左右分栏（左=搜索树，右=已勾选信号）──
        # 旧布局把树与勾选列表纵向堆叠，两者都未设拉伸系数，Qt 会把剩余高度
        # 50/50 平分，再扣掉搜索框/全选/分发/按钮等固定行，树只剩一两行。
        # 改为左右分栏后两侧各自纵向占满，树的行数只取决于面板高度。
        self._split = QSplitter(Qt.Horizontal)
        self._split.setChildrenCollapsible(False)
        self._split.setHandleWidth(dp(4))

        # ─── 左列：搜索树 + 分发按钮 ───
        left_col = QWidget()
        left_layout = QVBoxLayout(left_col)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(dp(4))

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["名称", "ID/类型"])
        self._tree.setAlternatingRowColors(True)
        # 行高统一时可跳过逐行测量，长列表滚动更顺滑
        self._tree.setUniformRowHeights(True)
        self._tree.setMinimumWidth(dp(150))
        # 缩进 20px -> 14px：信号是报文的子项，缩进直接吃掉名称列的可视宽度，
        # 收紧后长信号名能多显示 1~2 个字符
        self._tree.setIndentation(dp(14))
        # 名称列自适应剩余宽度、ID 列按内容自适应：窄面板下不再被固定列宽撑开
        _header = self._tree.header()
        _header.setSectionResizeMode(0, QHeaderView.Stretch)
        _header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        _header.setStretchLastSection(False)
        self._tree.itemChanged.connect(self._on_item_changed)
        left_layout.addWidget(self._tree, stretch=1)

        # 分发按钮栏：将搜索树中勾选的信号发送到曲线图/实时监控/模拟上报
        # （文案精简 + compact 样式，三个按钮一行放得下，省去一整行高度）
        dispatch_bar = QHBoxLayout()
        dispatch_bar.setSpacing(dp(4))
        for _target, _label, _tip in (
            ("curve", "曲线图", "将已勾选信号添加到曲线图"),
            ("monitor", "实时监控", "将已勾选信号添加到实时监控"),
            ("sim", "模拟上报", "将已勾选信号添加到模拟上报"),
        ):
            _btn = QPushButton(_label)
            _btn.setProperty("class", "compact")
            _btn.setToolTip(_tip)
            _btn.clicked.connect(
                lambda _checked=False, t=_target: self._on_dispatch(t)
            )
            dispatch_bar.addWidget(_btn)
        dispatch_bar.addStretch()
        left_layout.addLayout(dispatch_bar)
        self._split.addWidget(left_col)

        # ─── 右列：当前已勾选信号显示（跨搜索持久、可单独移除）───
        right_col = QWidget()
        right_layout = QVBoxLayout(right_col)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(dp(4))

        self._checked_count_label = QLabel("已勾选: 0")
        self._checked_count_label.setToolTip(
            "当前已勾选（含因搜索被隐藏）的信号数量；\n"
            "列表项可移除，会同步取消搜索树中的勾选"
        )
        right_layout.addWidget(self._checked_count_label)

        self._checked_list = QListWidget()
        self._checked_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._checked_list.setAlternatingRowColors(True)
        self._checked_list.setMinimumWidth(dp(130))
        self._checked_list.setMinimumHeight(dp(60))
        self._checked_list.setToolTip(
            "当前已勾选（含因搜索被隐藏）的信号；可在此移除，会同步取消搜索树中的勾选"
        )
        right_layout.addWidget(self._checked_list, stretch=1)

        # 两个按钮并排：统一走紧凑尺寸（compact / compact-primary 的 padding、
        # min-height、字号完全一致），并各占一半宽度，避免一大一小不齐。
        checked_btn_bar = QHBoxLayout()
        checked_btn_bar.setSpacing(dp(4))

        self._checked_remove_btn = QPushButton("移除选中")
        self._checked_remove_btn.setProperty("class", "compact")
        self._checked_remove_btn.setToolTip("从已勾选列表中移除，并同步取消搜索树中的勾选")
        self._checked_remove_btn.clicked.connect(self._on_remove_checked)
        checked_btn_bar.addWidget(self._checked_remove_btn, 1)

        self._add_to_group_btn = QPushButton("加入分组")
        self._add_to_group_btn.setProperty("class", "compact-primary")
        self._add_to_group_btn.setToolTip("将已勾选信号加入「信号分组」视图的当前分组")
        self._add_to_group_btn.clicked.connect(
            lambda: self.add_to_group_requested.emit(self.get_checked_signals())
        )
        checked_btn_bar.addWidget(self._add_to_group_btn, 1)
        right_layout.addLayout(checked_btn_bar)
        self._split.addWidget(right_col)

        self._split.setStretchFactor(0, 3)
        self._split.setStretchFactor(1, 2)
        self._split.setSizes([dp(420), dp(280)])
        layout.addWidget(self._split, stretch=1)

        # Delete 键移除选中的已勾选信号（等价于「移除选中」按钮）
        self._del_filter = DelKeyFilter(self._checked_list, self._on_remove_checked)

    # ────────────────────── 响应式布局 ──────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_split_orientation()

    def _sync_split_orientation(self):
        """面板变窄时把左右分栏切成上下堆叠。

        停靠在主窗口左侧栏时面板往往只有 300~520px 宽，继续左右分栏会让搜索树
        窄到看不清信号名；此时改为上下堆叠（树在上、已勾选在下），两侧仍各自保留
        可用的高度。阈值取 520px（设计基准值，随全局缩放系数换算）。
        """
        if not hasattr(self, "_split"):
            return
        want = (
            Qt.Horizontal
            if self.width() >= dp(self._SPLIT_FLIP_WIDTH)
            else Qt.Vertical
        )
        if want == self._split.orientation():
            return
        self._split.setOrientation(want)
        if want == Qt.Horizontal:
            self._split.setSizes([dp(420), dp(280)])
        else:
            self._split.setSizes([dp(300), dp(140)])

    def _on_dispatch(self, target: str):
        """把搜索树中勾选的信号分发到指定目标页（曲线图/实时监控/模拟上报）"""
        signals = self.get_checked_signals()
        self.dispatch_requested.emit(target, signals)

    def load_messages(self, messages: list[MessageDef]):
        self._messages = messages
        self._tree.clear()
        self._all_items.clear()
        self._tree.blockSignals(True)
        for msg in messages:
            msg_item = QTreeWidgetItem(self._tree)
            msg_item.setText(0, msg.name)
            msg_item.setText(1, f"0x{msg.frame_id:03X}")
            msg_item.setData(0, Qt.UserRole, msg)
            self._all_items[msg.name] = msg_item
            for sig in msg.signals:
                sig_item = QTreeWidgetItem(msg_item)
                sig_item.setText(0, sig.name)
                sig_item.setText(1, sig.unit)
                sig_item.setFlags(sig_item.flags() | Qt.ItemIsUserCheckable)
                sig_item.setCheckState(0, Qt.Unchecked)
                sig_item.setData(0, Qt.UserRole, sig)
        self._tree.blockSignals(False)
        self._refresh_checked_list()

    def get_checked_signals(self) -> list[tuple[str, str]]:
        result = []
        for i in range(self._tree.topLevelItemCount()):
            msg_item = self._tree.topLevelItem(i)
            msg_name = msg_item.text(0)
            for j in range(msg_item.childCount()):
                sig_item = msg_item.child(j)
                if sig_item.checkState(0) == Qt.Checked:
                    result.append((msg_name, sig_item.text(0)))
        return result

    def set_signal_checked(self, msg_name: str, sig_name: str, checked: bool):
        """按 (msg_name, sig_name) 设置信号勾选状态（供已选信号列表删除时调用）"""
        for i in range(self._tree.topLevelItemCount()):
            msg_item = self._tree.topLevelItem(i)
            if msg_item.text(0) != msg_name:
                continue
            for j in range(msg_item.childCount()):
                sig_item = msg_item.child(j)
                if sig_item.text(0) == sig_name:
                    self._tree.blockSignals(True)
                    sig_item.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
                    self._tree.blockSignals(False)
                    self._on_item_changed(None, 0)
                    return

    def _on_search(self, text: str):
        """搜索报文/信号，支持名称模糊搜索和 CAN ID 十六进制搜索。

        修复点：
        - 信号名搜索不再受 hex 检测影响（如 "AEB" 既像 hex 又像信号名，
          原逻辑会被 hex 检测劫持导致信号名搜不到）；
        - 报文名 / CAN ID 命中时，展开该报文并显示其下全部信号，便于勾选。
        """
        text = text.strip()
        if not text:
            # 搜索框清空：显示所有项并折叠
            for i in range(self._tree.topLevelItemCount()):
                msg_item = self._tree.topLevelItem(i)
                msg_item.setHidden(False)
                for j in range(msg_item.childCount()):
                    msg_item.child(j).setHidden(False)
                msg_item.setExpanded(False)
            return

        text_lower = text.lower()

        # 判断是否为十六进制 ID 搜索（仅用于报文 ID 匹配，不影响信号名搜索）
        is_hex_search = False
        search_id = 0
        search_hex = ""
        try:
            search_hex = text.replace("0x", "").replace("0X", "")
            search_id = int(search_hex, 16)
            is_hex_search = True
        except ValueError:
            pass

        for i in range(self._tree.topLevelItemCount()):
            msg_item = self._tree.topLevelItem(i)
            msg_name = msg_item.text(0).lower()
            msg_id_text = msg_item.text(1).lower()  # "0x1a0" 等
            msg_def = msg_item.data(0, Qt.UserRole)

            # 按名称匹配（始终生效）
            name_match = text_lower in msg_name
            # 按 ID 匹配 — 支持精确与模糊（如 "1A" 匹配 "0x1A0"、"0x1A1"）
            id_match = False
            if is_hex_search and msg_def:
                if msg_def.frame_id == search_id:
                    id_match = True
                elif search_hex:
                    msg_hex = f"{msg_def.frame_id:03X}".lower()
                    id_match = search_hex in msg_hex
            elif text_lower in msg_id_text:
                id_match = True

            msg_matched = name_match or id_match
            any_sig_visible = False

            for j in range(msg_item.childCount()):
                sig_item = msg_item.child(j)
                sig_name = sig_item.text(0).lower()
                # 信号名始终参与搜索；报文名/ID 命中时展开显示其下全部信号
                sig_visible = (text_lower in sig_name) or msg_matched
                sig_item.setHidden(not sig_visible)
                if sig_visible:
                    any_sig_visible = True

            msg_item.setHidden(not (msg_matched or any_sig_visible))
            if msg_matched or any_sig_visible:
                msg_item.setExpanded(True)

    def _on_item_changed(self, item, column):
        checked = self.get_checked_signals()
        self._refresh_checked_list()
        self._update_check_all_state()
        self.selection_changed.emit(checked)

    def _refresh_checked_list(self):
        """刷新『已勾选信号』列表（与搜索树勾选状态双向同步）。

        该列表展示所有已勾选信号，含因搜索被隐藏的项，使用户清楚看到
        “当前到底勾选了哪些”，避免在重新搜索后误把旧勾选项一并添加。
        """
        self._checked_list.blockSignals(True)
        self._checked_list.clear()
        pairs = self.get_checked_signals()
        for msg_name, sig_name in pairs:
            item = QListWidgetItem(f"{sig_name}  ({msg_name})")
            item.setData(Qt.UserRole, (msg_name, sig_name))
            # 右列较窄时文本仍可能被截断，悬停可见完整「信号名 + 所属报文」
            item.setToolTip(f"{sig_name}\n所属报文: {msg_name}")
            self._checked_list.addItem(item)
        self._checked_list.blockSignals(False)
        # 数量提示：面板较窄、列表看不全时，靠计数即可确认当前勾选规模
        self._checked_count_label.setText(f"已勾选: {len(pairs)}")

    def _on_remove_checked(self):
        """从已勾选列表中移除选中项，并同步取消搜索树中的对应勾选。

        即使目标信号因当前搜索被隐藏，set_signal_checked 仍会遍历所有项
        找到并取消其勾选，随后触发的 _on_item_changed 会刷新本列表。
        """
        pairs = [item.data(Qt.UserRole) for item in self._checked_list.selectedItems()]
        for msg_name, sig_name in pairs:
            self.set_signal_checked(msg_name, sig_name, False)

    def _on_check_all(self, checked: bool):
        """单一「全选」勾选框：勾选=勾选所有可见信号，取消=取消所有可见信号。"""
        self._set_visible_checked(checked)

    def _update_check_all_state(self):
        """根据当前可见信号勾选情况刷新「全选」勾选框：
        仅当所有可见信号都被勾选时全选框才勾选，否则取消勾选。"""
        total = 0
        checked = 0
        for i in range(self._tree.topLevelItemCount()):
            msg_item = self._tree.topLevelItem(i)
            for j in range(msg_item.childCount()):
                sig_item = msg_item.child(j)
                if (sig_item.flags() & Qt.ItemIsUserCheckable) and not sig_item.isHidden():
                    total += 1
                    if sig_item.checkState(0) == Qt.Checked:
                        checked += 1
        self._check_all_chk.blockSignals(True)
        self._check_all_chk.setChecked(total > 0 and checked == total)
        self._check_all_chk.blockSignals(False)

    def _set_visible_checked(self, checked: bool):
        """批量设置当前可见（未隐藏）的信号项勾选状态。"""
        self._tree.blockSignals(True)
        for i in range(self._tree.topLevelItemCount()):
            msg_item = self._tree.topLevelItem(i)
            for j in range(msg_item.childCount()):
                sig_item = msg_item.child(j)
                if (sig_item.flags() & Qt.ItemIsUserCheckable) and not sig_item.isHidden():
                    sig_item.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
        self._tree.blockSignals(False)
        self._on_item_changed(None, 0)
