# widgets/signal_tree.py
"""信号树组件：展示 DBC 中的报文和信号，支持搜索和勾选"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLineEdit, QTreeWidget, QTreeWidgetItem,
    QPushButton, QHBoxLayout,
)
from PyQt5.QtCore import pyqtSignal, Qt
from core.can_data import MessageDef


class SignalTreeWidget(QWidget):
    """左侧信号树面板"""
    selection_changed = pyqtSignal(list)
    plot_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._messages: list[MessageDef] = []
        self._all_items: dict[str, QTreeWidgetItem] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        # 搜索框（带搜索图标占位提示）
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("\U0001f50d 搜索报文名称 / CAN ID...")
        self._search_input.textChanged.connect(self._on_search)
        layout.addWidget(self._search_input)

        # 树形列表
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["名称", "ID/类型"])
        self._tree.setColumnWidth(0, 200)
        self._tree.setAlternatingRowColors(True)
        self._tree.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._tree)

        # 按钮栏
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)

        # 主要操作按钮：绘图
        self._plot_btn = QPushButton("绘图")
        self._plot_btn.setProperty("class", "primary")
        self._plot_btn.setEnabled(False)
        self._plot_btn.clicked.connect(self._on_plot_clicked)
        btn_layout.addWidget(self._plot_btn)

        # 次要操作按钮
        self._select_all_btn = QPushButton("全选当前")
        self._select_all_btn.clicked.connect(self._on_select_all)
        btn_layout.addWidget(self._select_all_btn)

        self._deselect_all_btn = QPushButton("取消全选")
        self._deselect_all_btn.clicked.connect(self._on_deselect_all)
        btn_layout.addWidget(self._deselect_all_btn)
        layout.addLayout(btn_layout)

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

    def _on_search(self, text: str):
        """搜索报文/信号，支持名称模糊搜索和 CAN ID 十六进制搜索"""
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

        # 判断是否为十六进制 ID 搜索
        is_hex_search = False
        search_id = 0
        try:
            hex_str = text.replace("0x", "").replace("0X", "")
            search_id = int(hex_str, 16)
            is_hex_search = True
        except ValueError:
            pass

        for i in range(self._tree.topLevelItemCount()):
            msg_item = self._tree.topLevelItem(i)
            msg_name = msg_item.text(0).lower()
            msg_id_text = msg_item.text(1).lower()  # "0x1a0" 等
            msg_def = msg_item.data(0, Qt.UserRole)

            # 按名称匹配
            name_match = text_lower in msg_name
            # 按 ID 匹配 — 支持模糊搜索（如 "1A" 匹配 "0x1A0"、"0x1A1" 等）
            id_match = False
            if is_hex_search and msg_def:
                # 精确匹配
                if msg_def.frame_id == search_id:
                    id_match = True
                # 模糊匹配：搜索文本是 hex ID 的子串
                else:
                    msg_hex = f"{msg_def.frame_id:03X}".lower()
                    search_hex = text.replace("0x", "").replace("0X", "").lower()
                    id_match = search_hex in msg_hex
            elif text_lower in msg_id_text:
                id_match = True

            msg_visible = name_match or id_match
            any_sig_visible = False

            for j in range(msg_item.childCount()):
                sig_item = msg_item.child(j)
                sig_name = sig_item.text(0).lower()
                # ID 搜索时不匹配信号名（信号没有 ID）
                sig_visible = (text_lower in sig_name) if not is_hex_search else False
                sig_item.setHidden(not sig_visible)
                if sig_visible:
                    any_sig_visible = True

            msg_item.setHidden(not (msg_visible or any_sig_visible))
            if any_sig_visible or (msg_visible and text):
                msg_item.setExpanded(True)
            elif not text:
                msg_item.setExpanded(False)

    def _on_item_changed(self, item, column):
        checked = self.get_checked_signals()
        self._plot_btn.setEnabled(len(checked) > 0)
        self.selection_changed.emit(checked)

    def _on_plot_clicked(self):
        self.plot_requested.emit()

    def _on_select_all(self):
        """全选当前报文下所有可见信号"""
        current = self._tree.currentItem()
        if current is None:
            return
        if current.parent() is not None:
            msg_item = current.parent()
        else:
            msg_item = current
        self._tree.blockSignals(True)
        for j in range(msg_item.childCount()):
            sig_item = msg_item.child(j)
            if not sig_item.isHidden():
                sig_item.setCheckState(0, Qt.Checked)
        self._tree.blockSignals(False)
        self._on_item_changed(None, 0)

    def _on_deselect_all(self):
        """取消全部信号勾选"""
        self._tree.blockSignals(True)
        for i in range(self._tree.topLevelItemCount()):
            msg_item = self._tree.topLevelItem(i)
            for j in range(msg_item.childCount()):
                sig_item = msg_item.child(j)
                if sig_item.flags() & Qt.ItemIsUserCheckable:
                    sig_item.setCheckState(0, Qt.Unchecked)
        self._tree.blockSignals(False)
        self._on_item_changed(None, 0)
