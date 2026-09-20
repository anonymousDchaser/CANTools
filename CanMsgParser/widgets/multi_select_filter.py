# widgets/multi_select_filter.py
"""多选报文 ID 筛选按钮：点击弹出可勾选列表，一次筛选多个报文 ID。

用于「报文表格」与「实时报文」两页的报文 ID 筛选（原先两页/单页只能选一个 ID）：

- 弹出菜单内嵌 QListWidget，每项带复选框，勾选后菜单不关闭，可连续勾选多个；
- 整行可点：QListWidget 原生只在点击勾选框小方块时切换状态，这里额外捕获
  itemPressed/itemClicked 判断「Qt 是否已自行切换」，未切换则在点击整行时手动
  切换，避免用户必须精确点中小方块；
- 首项「全部」为快捷项：勾选即清空其它勾选（= 不做 ID 过滤）；勾选任一具体 ID
  会自动取消「全部」；取消「全部」而其它项也没勾选时自动恢复（不允许"空筛选"）；
- 按钮文字只给摘要（「全部」/「0x1A0」/「已选 3 个 ID」），完整列表放在 tooltip，
  避免按钮被长文本撑宽；
- selectionChanged 在每次勾选变化后发出，使用方决定是否立即重新过滤；
- 菜单顶部内嵌搜索框：ID 很多时输入片段（1A0 / 0x1A0）即时过滤列表，只隐藏不重建，
  勾选状态不受影响；命中唯一项时按回车可直接切换其勾选，便于键盘快速操作。
"""
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QToolButton, QMenu, QListWidget, QListWidgetItem, QWidgetAction,
    QLabel, QLineEdit,
)

from utils.font_helper import UI_FONT_FAMILY
from utils.ui_scale import dp

# 「全部」项的显示文本（其 UserRole 为 None，作为快捷项标记）
_ALL_TEXT = "全部"
# 按钮摘要中最多直接展示几个 ID，超出改为「已选 N 个 ID」
_INLINE_MAX = 2
# 顶部搜索框的占位提示
_SEARCH_PLACEHOLDER = "搜索报文 ID，如 1A0 / 0x1A0"


class _SearchEdit(QLineEdit):
    """筛选菜单顶部的搜索框。

    只把「回车」重定义为提交语义（唯一命中项时切换勾选），其余按键照常交给
    QLineEdit 处理；Esc 显式 ignore，让事件继续上抛给 QMenu 关闭弹出菜单。
    """

    submitted = pyqtSignal()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.submitted.emit()
            e.accept()
            return
        if e.key() == Qt.Key_Escape:
            e.ignore()
            return
        super().keyPressEvent(e)


class _CheckListWidget(QListWidget):
    """勾选列表：在 Qt 处理鼠标按下**之前**记录被点项及其勾选状态。

    「整行可点」需要判断「Qt 是否已经自己切换了复选框」，而判断必须在按下前拿到
    状态：

    - 点击落在复选框判定区内时，Qt 会通过 delegate 的 editorEvent 消费这次按下并
      直接切换状态，**`pressed` 信号根本不发出**（fusion / Windows 样式实测如此）；
      改用 `itemPressed` 回调记录，拿到的就是上一次交互的残留值。
    - 残留值一旦恰好等于切换后的状态，补偿逻辑就会误判为「Qt 没切换」再补一次，
      把用户刚勾上的又切回去——表现为「点复选框没反应，点行的其他位置才可以」。

    这里的 mousePressEvent 在调用 super() 之前读取，因此无论 Qt 何时/是否切换，
    记录到的都一定是「本次交互之前」的状态。
    """

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self._owner = owner

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._owner._remember_press(self.itemAt(e.pos()))
        super().mousePressEvent(e)


class MultiSelectIdFilter(QToolButton):
    """可勾选多个报文 ID 的筛选按钮（InstantPopup）。"""

    selectionChanged = pyqtSignal()

    _QSS = """
        QToolButton {
            background-color: #2a2a3e;
            color: #e0e0e0;
            border: 1px solid #3a3a4e;
            border-radius: 4px;
            padding: 6px 10px;
            min-height: 28px;
            text-align: left;
            font-family: %s;
        }
        QToolButton:hover {
            border-color: #4fc3f7;
        }
        QToolButton::menu-indicator {
            image: none;
        }
        QMenu {
            background-color: #252535;
            color: #e0e0e0;
            border: 1px solid #3a3a4e;
        }
        QListWidget {
            background-color: #252535;
            color: #e0e0e0;
            border: none;
            outline: none;
            font-family: %s;
        }
        QListWidget::item {
            padding: 3px 6px;
        }
        QLineEdit {
            background-color: #1f1f2e;
            color: #e0e0e0;
            border: 1px solid #3a3a4e;
            border-radius: 3px;
            padding: 4px 6px;
            margin: 4px 6px 2px 6px;
            font-family: %s;
        }
        QLineEdit:focus {
            border-color: #4fc3f7;
        }
        QLabel#filterEmptyHint {
            color: #808090;
            padding: 8px 6px;
        }
    """ % (UI_FONT_FAMILY, UI_FONT_FAMILY, UI_FONT_FAMILY)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ids: list[int] = []
        self._checked: set[int] = set()
        self._updating = False
        self._press_state = None     # 本次按下**之前**的勾选状态
        self._press_item = None      # 本次按下命中的列表项

        self.setPopupMode(QToolButton.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.setStyleSheet(self._QSS)

        self._menu = QMenu(self)

        # ── 菜单顶部：搜索框（ID 多时先搜再勾）──
        self._search = _SearchEdit(self._menu)
        self._search.setPlaceholderText(_SEARCH_PLACEHOLDER)
        self._search.setMinimumWidth(dp(150))
        search_action = QWidgetAction(self._menu)
        search_action.setDefaultWidget(self._search)
        self._menu.addAction(search_action)

        # ── 可勾选列表 ──
        self._list = _CheckListWidget(self, self._menu)
        self._list.setSelectionMode(QListWidget.NoSelection)
        self._list.setMinimumWidth(dp(150))
        self._list.setMaximumHeight(dp(320))
        action = QWidgetAction(self._menu)
        action.setDefaultWidget(self._list)
        self._menu.addAction(action)

        # ── 无匹配提示（只在搜索无结果时出现）──
        self._empty_label = QLabel("无匹配的报文 ID", self._menu)
        self._empty_label.setObjectName("filterEmptyHint")
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_action = QWidgetAction(self._menu)
        self._empty_action.setDefaultWidget(self._empty_label)
        self._empty_action.setVisible(False)
        self._menu.addAction(self._empty_action)

        self.setMenu(self._menu)

        self._search.textChanged.connect(self._apply_search)
        self._search.submitted.connect(self._on_search_submit)
        self._list.itemChanged.connect(self._on_item_changed)
        self._list.itemClicked.connect(self._on_item_clicked)
        self._menu.aboutToShow.connect(self._on_menu_about_to_show)

        self._refill()

    # ────────────────────── 对外接口 ──────────────────────

    def set_ids(self, ids, keep_selection: bool = True) -> None:
        """重建候选 ID 列表。

        Args:
            ids: 新的候选 ID 序列
            keep_selection: True 保留仍然存在的已勾选项（实时报文页：候选随报文
                累积，勾选不应被重建掉）；False 清空勾选（报文表格页：换了数据源
                后表格展示的是新文件的全部帧，勾选必须同步回到「全部」，否则会
                出现「控件显示已筛选、表格却是全量」的不一致）
        """
        new_ids = [int(i) for i in ids]
        self._ids = new_ids
        self._checked = (self._checked & set(new_ids)) if keep_selection else set()
        self._refill()

    def selected_ids(self) -> list:
        """当前勾选的报文 ID（升序）；返回空列表表示不按 ID 过滤（全部）。"""
        return sorted(self._checked)

    def set_selected_ids(self, ids) -> None:
        """程序化设置勾选（不要求 ID 已在候选列表中），并发出 selectionChanged。"""
        self._checked = {int(i) for i in ids}
        self._refill()
        self.selectionChanged.emit()

    def clear_selection(self) -> None:
        """清空勾选（等价于「全部」）。"""
        if not self._checked and self._all_is_checked():
            return
        self._checked.clear()
        self._refill()
        self.selectionChanged.emit()

    def summary_text(self) -> str:
        ids = self.selected_ids()
        if not ids:
            return _ALL_TEXT
        if len(ids) <= _INLINE_MAX:
            return ", ".join(f"0x{i:03X}" for i in ids)
        return f"已选 {len(ids)} 个 ID"

    # ────────────────────── 内部实现 ──────────────────────

    def _all_is_checked(self) -> bool:
        item = self._list.item(0)
        return item is not None and item.checkState() == Qt.Checked

    def _refill(self) -> None:
        """按 _ids / _checked 重建列表项。"""
        self._updating = True
        try:
            self._list.clear()
            all_item = self._new_item(_ALL_TEXT, None)
            all_item.setCheckState(Qt.Checked if not self._checked else Qt.Unchecked)
            self._list.addItem(all_item)
            for aid in self._ids:
                item = self._new_item(f"0x{aid:03X}", aid)
                item.setCheckState(
                    Qt.Checked if aid in self._checked else Qt.Unchecked
                )
                self._list.addItem(item)
        finally:
            self._updating = False
        # 列表重建后重新套用当前搜索词（实时报文页的候选 ID 会持续累积）
        self._apply_search(self._search.text())
        self._update_text()

    @staticmethod
    def _new_item(text, data):
        item = QListWidgetItem(text)
        item.setData(Qt.UserRole, data)
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
        return item

    def _sync_check_states(self) -> None:
        """把 _checked 同步到各列表项（用 _updating 抑制回调）。"""
        self._updating = True
        try:
            for i in range(self._list.count()):
                item = self._list.item(i)
                aid = item.data(Qt.UserRole)
                if aid is None:
                    item.setCheckState(
                        Qt.Checked if not self._checked else Qt.Unchecked
                    )
                else:
                    item.setCheckState(
                        Qt.Checked if aid in self._checked else Qt.Unchecked
                    )
        finally:
            self._updating = False
        self._update_text()

    def _update_text(self) -> None:
        text = self.summary_text()
        self.setText(f"{text}  ▾")
        ids = self.selected_ids()
        if ids:
            self.setToolTip("已选报文 ID（点击修改，可在顶部搜索 ID）:\n"
                            + ", ".join(f"0x{i:03X}" for i in ids))
        else:
            self.setToolTip("未限制报文 ID（点击勾选需要筛选的 ID，可在顶部搜索）")

    # ────────────────────── 搜索过滤 ──────────────────────

    @staticmethod
    def _normalize_query(text: str) -> str:
        """归一化搜索词：去空白、大写、去掉 0x 前缀（与列表里的十六进制文本对齐）。"""
        q = (text or "").strip().upper()
        if q.startswith("0X"):
            q = q[2:]
        return q

    def _visible_id_items(self) -> list:
        """当前可见的具体 ID 项（不含「全部」快捷项）。"""
        out = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.UserRole) is not None and not item.isHidden():
                out.append(item)
        return out

    def _apply_search(self, text: str) -> None:
        """按搜索词过滤列表项：只隐藏、不重建，勾选状态与 _checked 完全不受影响。"""
        query = self._normalize_query(text)
        matched = 0
        for i in range(self._list.count()):
            item = self._list.item(i)
            aid = item.data(Qt.UserRole)
            if aid is None:
                # 「全部」快捷项：搜索时让位给具体 ID（清空搜索即恢复显示）
                item.setHidden(bool(query))
                continue
            hit = not query or query in f"{int(aid):03X}"
            item.setHidden(not hit)
            if hit:
                matched += 1
        self._empty_action.setVisible(bool(query) and matched == 0)

    def _on_search_submit(self) -> None:
        """回车：唯一命中项时直接切换它的勾选（键盘快速操作）。"""
        visible = self._visible_id_items()
        if len(visible) != 1:
            return
        item = visible[0]
        item.setCheckState(
            Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked
        )

    # ────────────────────── 事件回调 ──────────────────────

    def _on_menu_about_to_show(self) -> None:
        """每次展开都从干净的搜索态开始，并让搜索框立即获得焦点以便直接输入。"""
        self._search.blockSignals(True)
        self._search.setText("")
        self._search.blockSignals(False)
        self._apply_search("")
        QTimer.singleShot(0, self._search.setFocus)

    def _remember_press(self, item) -> None:
        """记录本次按下的命中项与「按下之前」的勾选状态（由 _CheckListWidget 调用）。

        必须在 Qt 处理该次按下之前执行：Qt 可能自己就把复选框切了（点击落在复选框
        判定区内），事后拿不到原始状态。
        """
        self._press_item = item
        self._press_state = None if item is None else item.checkState()

    def _on_item_clicked(self, item) -> None:
        """点击未落在复选框判定区时，Qt 不会切换状态，这里补一次整行切换。

        判定依据是「本次按下之前」的状态：状态变了说明 Qt 已经切换过，直接放过；
        状态没变才补偿。用事后读取的状态判断会把两种情况搞反（详见 _CheckListWidget）。
        """
        if self._updating or item is not self._press_item:
            return
        if item.checkState() != self._press_state:
            return
        item.setCheckState(
            Qt.Unchecked if self._press_state == Qt.Checked else Qt.Checked
        )

    def _on_item_changed(self, item) -> None:
        if self._updating:
            return
        aid = item.data(Qt.UserRole)
        checked = item.checkState() == Qt.Checked
        if aid is None:
            # 「全部」：勾选 = 清空其它勾选；取消勾选而其它项也没勾选 → 恢复
            if checked:
                self._checked.clear()
            elif not self._checked:
                self._sync_check_states()
                return
        else:
            if checked:
                self._checked.add(int(aid))
            else:
                self._checked.discard(int(aid))
        self._sync_check_states()
        self.selectionChanged.emit()
