"""列表 / 树中被截断文字的悬停提示。

Qt 默认只在 item 设置了 ToolTipRole 时才显示 tooltip，且不区分文字是否真的
被省略号截断；没设 tooltip 的控件（如「信号检索」的搜索树）里，长信号名被
截断后用户只能靠猜。

ElidedToolTipMixin 补上「按需提示」能力：
- 悬停时先判断该单元格文字是否放得下（用 delegate 的 sizeHint 求「完整显示
  所需宽度」，与「列宽 - 树缩进 - delegate 额外预留」比较），放不下才弹提示；
- 提示内容优先取该行已有的 ToolTipRole（通常比行文本更完整，如附带所属报文 /
  帧 ID），未设置时取单元格完整文本；
- 放得下则完全不拦截，交回 Qt 默认处理——因此能与控件级 setToolTip 的操作说明
  共存：文字完整时显示操作说明，被截断时才显示完整内容。

用法（Mixin 必须排在 Qt 基类之前：写在后面时 C3 线性化会把它排到全部 Qt 类
之后，super() 调用会全部落到 object 上）：
    class MyTree(ElidedToolTipMixin, QTreeWidget): ...
    class MyList(ElidedToolTipMixin, QListWidget): ...

若宿主用自定义 delegate 在文字两侧预留了额外宽度（如
widgets/drag_reorder_list.py 的行首眼睛图标 + 行尾拖拽把手），可在该 delegate
上实现 ``reserved_text_inset(index) -> int``，本 Mixin 会自动扣除，避免出现
「明明被截断却不提示」。
"""
from PyQt5.QtCore import Qt, QEvent
from PyQt5.QtWidgets import (
    QListWidget, QStyleOptionViewItem, QToolTip, QTreeWidget,
)

# 项目 QSS 给列表 / 树的 item 设了左右内边距（QListWidget::item 为 6px、
# QTreeWidget::item 为 5px），但 QStyleSheetStyle 不会把它算进
# delegate.sizeHint（实测 sizeHint 宽度不随 ::item padding 变化），
# 因此这里按左右各 6px 保守预留，避免「实际已被挤掉却不弹提示」的漏判。
_TEXT_SIDE_PADDING = 6


class ElidedToolTipMixin:
    """被截断的单元格文字，悬停时给出完整内容提示。"""

    def viewportEvent(self, event):
        # 只接管 ToolTip，其余事件原样交给 Qt
        if event.type() == QEvent.ToolTip and self._show_elided_tooltip(event):
            return True
        return super().viewportEvent(event)

    # ────────────────────── 内部实现 ──────────────────────

    def _show_elided_tooltip(self, event) -> bool:
        """文字放不下时弹出完整内容提示并返回 True；否则返回 False 交回默认处理。"""
        index = self.indexAt(event.pos())
        if not index.isValid() or not self._elide_is_truncated(index):
            return False
        tip = index.data(Qt.ToolTipRole)
        if not isinstance(tip, str) or not tip.strip():
            tip = index.data(Qt.DisplayRole)
        if not isinstance(tip, str) or not tip.strip():
            return False
        QToolTip.showText(event.globalPos(), tip, self)
        return True

    def _elide_is_truncated(self, index) -> bool:
        """该单元格文字是否放不下（即绘制时会被省略号截断）。"""
        text = index.data(Qt.DisplayRole)
        if not isinstance(text, str) or not text.strip():
            return False
        rect = self.visualRect(index)
        if not rect.isValid():
            return False
        # 列表控件的 item 矩形是按「内容宽度」给出的、可能比视口还宽，必须再受
        # 视口右边界约束；树控件的矩形本就是列宽，取 min 同样成立
        visible = min(rect.width(), self.viewport().width() - rect.left())
        if visible <= 0:
            return False
        # delegate.sizeHint 给出的「完整显示所需宽度」已含内边距与勾选框 / 图标，
        # 这里只需再扣掉树缩进与自定义 delegate 的额外预留（把手 / 眼睛图标）
        opt = QStyleOptionViewItem()
        try:
            self.initViewItemOption(opt)
        except Exception:
            # PyQt5 未暴露 initViewItemOption（Qt 5.5+ 的 protected 接口），
            # 退而把 styleObject 指向自身，让 sizeHint 用本控件的 style 计算，
            # 而不是回退到 QApplication.style()
            try:
                opt.styleObject = self
            except Exception:
                pass
        opt.rect = rect
        need = self.itemDelegate().sizeHint(opt, index).width()
        avail = (visible
                 - 2 * _TEXT_SIDE_PADDING
                 - self._elide_indent_inset(index)
                 - self._elide_reserved_inset(index))
        return need > avail

    def _elide_indent_inset(self, index) -> int:
        """树形控件按层级扣掉缩进（列表控件没有 indentation()，返回 0）。"""
        indent = getattr(self, "indentation", None)
        if not callable(indent):
            return 0
        # Qt 的顶层项缩进 1 级、子项逐层递增
        depth = 1
        parent = index.parent()
        while parent.isValid():
            depth += 1
            parent = parent.parent()
        return indent() * depth

    def _elide_reserved_inset(self, index) -> int:
        """向宿主 delegate 询问它在文字左右额外预留的宽度。"""
        fn = getattr(self.itemDelegate(), "reserved_text_inset", None)
        if not callable(fn):
            return 0
        try:
            return int(fn(index))
        except Exception:
            return 0


class ElidedListWidget(ElidedToolTipMixin, QListWidget):
    """已接入「截断即悬停提示」的 QListWidget。"""


class ElidedTreeWidget(ElidedToolTipMixin, QTreeWidget):
    """已接入「截断即悬停提示」的 QTreeWidget。"""
