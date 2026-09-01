"""支持长按把手拖拽排序的列表/树控件。

用于「曲线图页 · 已选信号列表」「实时监控页 · 已选信号列表」「信号分组 · 组内
信号列表」等需要用户自定义顺序的视图：
- 第一列行右侧绘制一个 ⋮⋮ 拖动把手图标（由 delegate 绘制，文字区域自动右缩进）；
- 鼠标按住把手长按约 LONG_PRESS_MS 后进入拖拽状态（无移动取消阈值），随后
  移动鼠标即可拖动该行；
- 拖动过程中绘制插入位置指示线，落下后移动行并发出 orderChanged 信号，
  由外部（主窗口 / 页面）同步数据顺序并按新顺序重绘曲线。

DragReorderMixin 提供通用拖拽逻辑，QListWidget 与 QTreeWidget 各自适配：
- DragReorderListWidget：QListWidget 子类，所有行均可拖拽；
- DragReorderTreeWidget：QTreeWidget 子类，仅「携带 UserRole 数据」的顶层项
  可拖拽（信号分组面板中分组标题行 / 子项不参与）。
"""
from PyQt5.QtCore import Qt, QTimer, QRect, QPoint, pyqtSignal, QMimeData
from PyQt5.QtGui import QColor, QPainter, QPen, QDrag
from PyQt5.QtWidgets import (
    QApplication, QListWidget, QTreeWidget, QStyledItemDelegate, QStyle,
    QStyleOptionViewItem,
)

# 拖拽行号的自定义 MIME 类型（仅限本控件内部移动）
_MIME_TYPE = "application/x-canmsgparser-drag-row"


class _HandleDelegate(QStyledItemDelegate):
    """行绘制代理：第一列文字区域右侧留出把手位，并绘制 ⋮⋮ grip 图标。

    - 拖拽已激活（armed）且为当前按下的行时，把手点亮为高亮蓝并叠加
      半透明背景，给出「已可拖动」的视觉反馈；
    - 不可拖拽的行（QTreeWidget 的分组标题行 / 子项）不缩进、不画把手。
    """

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self._owner = owner  # 宿主 DragReorderListWidget / DragReorderTreeWidget

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        # 仅第一列的顶层可拖拽行绘制把手；其余（备注列 / 分组标题 / 子项）
        # 保持原样绘制，避免把手与备注编辑框或分组标题重叠
        is_handle_col = (
            index.column() == 0
            and not index.parent().isValid()
            and self._owner._dnd_row_allowed(index.row())
        )
        if is_handle_col:
            # 文本/图标区域右缩进，避免与把手重叠
            opt.rect = option.rect.adjusted(0, 0, -DragReorderMixin.HANDLE_WIDTH, 0)

        widget = opt.widget
        style = widget.style() if widget else QApplication.style()

        armed_row = self._owner.armedRow()
        if is_handle_col and armed_row == index.row():
            # 长按激活行：叠加淡蓝色背景提示「可拖动」
            painter.fillRect(option.rect, QColor(79, 195, 247, 30))

        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)
        if is_handle_col:
            self._draw_handle(painter, option.rect, index.row(), armed_row)

    @staticmethod
    def _draw_handle(painter, rect: QRect, row: int, armed_row: int):
        """在行矩形右侧绘制 2 列 × 3 行的 grip 圆点，armed 行点亮为高亮蓝。"""
        r = rect
        cx = r.right() - DragReorderMixin.HANDLE_WIDTH // 2
        cy = r.center().y()
        color = QColor("#4fc3f7") if row == armed_row else QColor("#6a6a7e")
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        for dx in (-3, 3):
            for dy in (-6, 0, 6):
                painter.drawEllipse(QPoint(cx + dx, cy + dy), 1.6, 1.6)
        painter.restore()


class DragReorderMixin:
    """长按右侧把手拖拽调整行顺序的通用实现。

    子类（QListWidget / QTreeWidget 子类）需实现以下「适配接口」，本类的事件
    处理与拖拽逻辑全部基于这些接口，与具体宿主无关：

        _dnd_count() -> int             行数（顶层行数）
        _dnd_item_at(pos) -> item       命中 item / None（pos 为 viewport 坐标）
        _dnd_row_of(item) -> int        行号（非顶层 item 返回 -1）
        _dnd_row_item(row) -> item      取指定行 item
        _dnd_to_top(item) -> item       把命中 item 提升为其顶层行
        _dnd_take_row(row) -> item      取走行（Qt 会销毁该行的 item widget）
        _dnd_insert_row(row, item)      插入行
        _dnd_visual_rect(row) -> QRect  行矩形（viewport 坐标）
        _dnd_handle_rect() -> QRect     把手命中区域（viewport 坐标）
        _dnd_row_allowed(row) -> bool   该行是否可参与拖拽

    顺序变化后发出 orderChanged()（无参数），外部遍历各行 data(Qt.UserRole)
    即可得到新顺序。被移动行的新行号可通过 lastMovedRow() 查询——宿主若用
    setItemWidget 挂载了行内控件（如备注编辑框），只需重建该行即可，
    其余行的 item widget 不受 take/insert 影响。
    """

    orderChanged = pyqtSignal()

    HANDLE_WIDTH = 24      # 行右侧把手区域宽度（像素）
    LONG_PRESS_MS = 350    # 长按激活拖拽的时长（激活后无移动取消阈值）

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._delegate = _HandleDelegate(self, self)
        self.setItemDelegate(self._delegate)
        # 关键：让 viewport 接受 drop。QAbstractItemView 默认 dragDropMode 为
        # NoDragDrop，viewport 不接受 drop，QDrag.exec() 会找不到有效落点，
        # dragEnter/dragMove/drop 事件一概不触发——表现为「拖得动但落不下」。
        self.viewport().setAcceptDrops(True)
        self._press_pos = None         # 按下位置（viewport 坐标）
        self._press_row = -1           # 按下的行号
        self._press_on_handle = False  # 按下位置是否落在把手区域
        self._drag_armed = False       # 长按已激活、允许拖动
        self._drop_row = -1            # 拖动中的插入指示行（-1 表示不显示）
        self._last_moved_row = -1      # 最近一次成功移动后的目标行号
        # 防重入：drag.exec() 是阻塞模态循环，期间若 mouseMoveEvent 被意外
        # 重入（Qt 在部分场景会把鼠标事件回发给拖拽源），_drag_armed 仍为
        # True，不加保护会再次 _start_drag -> 嵌套模态循环 -> UI 卡死。
        self._drag_exec_active = False
        self._long_press_timer = QTimer(self)
        self._long_press_timer.setSingleShot(True)
        self._long_press_timer.timeout.connect(self._on_long_press)

    # ────────────────────── 对外查询 ──────────────────────

    def armedRow(self) -> int:
        """当前长按激活（可拖动）的行号，未激活返回 -1。供 delegate 绘制反馈。"""
        return self._press_row if self._drag_armed else -1

    def lastMovedRow(self) -> int:
        """最近一次 _move_row 成功后的目标行号，未发生过移动返回 -1。"""
        return self._last_moved_row

    # ────────────────────── 鼠标交互 ──────────────────────

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            item = self._dnd_item_at(e.pos())
            self._press_row = self._dnd_row_of(item)
            self._press_pos = e.pos()
            self._press_on_handle = (
                self._press_row >= 0
                and self._dnd_handle_rect().contains(e.pos())
                and self._dnd_row_allowed(self._press_row)
            )
            self._drag_armed = False
            if self._press_on_handle:
                self._long_press_timer.start(self.LONG_PRESS_MS)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._press_on_handle and not self._drag_armed:
            # 长按等待期：无移动取消阈值，吞掉移动事件避免触发选择拖动
            e.accept()
            return
        if self._drag_armed and (e.buttons() & Qt.LeftButton):
            self._start_drag()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._long_press_timer.stop()
        was_armed = self._drag_armed
        self._drag_armed = False
        self._press_pos = None
        self._press_row = -1
        self._press_on_handle = False
        self._drop_row = -1
        if was_armed:
            self.viewport().update()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        """拖动中按 Esc 取消本次拖拽。"""
        if e.key() == Qt.Key_Escape and self._drag_armed:
            self._long_press_timer.stop()
            self._drag_armed = False
            self._press_pos = None
            self._press_row = -1
            self._press_on_handle = False
            self._drop_row = -1
            self.viewport().update()
            e.accept()
            return
        super().keyPressEvent(e)

    def _on_long_press(self):
        """长按计时到：激活拖拽并重绘把手高亮。"""
        if self._press_row >= 0 and self._press_on_handle:
            self._drag_armed = True
            self.viewport().update()

    # ────────────────────── 落位计算 ──────────────────────

    def _move_row(self, src_row: int, dst_row: int) -> bool:
        """把 src_row 行移动到插入位置 dst_row（0..count）。

        原位落下（dst == src 或 dst == src+1）视为无变化，返回 False；
        移动成功后发出 orderChanged()，返回 True。
        """
        if (src_row < 0 or src_row >= self._dnd_count()
                or dst_row < 0 or dst_row > self._dnd_count()
                or dst_row == src_row or dst_row == src_row + 1):
            return False
        item = self._dnd_take_row(src_row)
        if dst_row > src_row:
            dst_row -= 1  # 取走源行后目标行号前移
        self._dnd_insert_row(dst_row, item)
        self._last_moved_row = dst_row  # 宿主据此只重建该行的 item widget
        cur = self._dnd_row_item(dst_row)
        if cur is not None:
            self.setCurrentItem(cur)
        self.orderChanged.emit()
        return True

    # ────────────────────── 拖拽实现 ──────────────────────

    def _start_drag(self):
        """以自定义 QDrag 启动拖拽（exec 阻塞至落下/取消）。

        exec() 为模态事件循环，期间不得再次进入本方法（防嵌套模态循环卡死）；
        状态复位放在 finally，保证拖拽被取消 / drop 处理异常时也能复位。

        注意：此处不调用 setPixmap 设置拖拽预览图。此前用 QPainter 在离屏
        QPixmap 上自绘半透明圆角条 + 文字作为预览，Windows 真机在该绘制阶段
        崩溃（日志停在 step3 make pixmap 之后、step4 setPixmap 之前）。拖拽
        落点反馈已由 paintEvent 的插入指示线 + delegate 的把手高亮提供，去掉
        预览图不影响拖拽排序功能。
        """
        if self._drag_exec_active or self._press_row < 0:
            return
        self._drag_exec_active = True
        try:
            drag = QDrag(self)
            mime = QMimeData()
            mime.setData(_MIME_TYPE, str(self._press_row).encode())
            drag.setMimeData(mime)
            drag.exec(Qt.MoveAction)
        finally:
            self._drag_exec_active = False
            self._drag_armed = False
            self._press_pos = None
            self._press_row = -1
            self._press_on_handle = False
            self._drop_row = -1
            self.viewport().update()

    def _target_row_at(self, pos) -> int:
        """把 viewport 坐标换算为插入行号（0..count），无效位置返回 -1。"""
        item = self._dnd_item_at(pos)
        if item is not None:
            item = self._dnd_to_top(item)
            row = self._dnd_row_of(item)
            if row < 0:
                return -1
            r = self._dnd_visual_rect(row)
            return row if pos.y() <= r.center().y() else row + 1
        if self._dnd_count() == 0:
            return 0
        first = self._dnd_visual_rect(0)
        last = self._dnd_visual_rect(self._dnd_count() - 1)
        if pos.y() < first.top():
            return 0
        if pos.y() > last.bottom():
            return self._dnd_count()
        return -1  # 行间隙等无效区域

    def dragEnterEvent(self, e):
        if e.source() is self and e.mimeData().hasFormat(_MIME_TYPE):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.source() is self and e.mimeData().hasFormat(_MIME_TYPE):
            e.acceptProposedAction()
            row = self._target_row_at(e.pos())
            if row != self._drop_row:
                self._drop_row = row
                self.viewport().update()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self._drop_row = -1
        self.viewport().update()
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        if e.source() is not self or not e.mimeData().hasFormat(_MIME_TYPE):
            e.ignore()
            return
        src_row = int(bytes(e.mimeData().data(_MIME_TYPE)).decode())
        dst_row = self._target_row_at(e.pos())
        self._drop_row = -1
        if dst_row < 0 or src_row < 0 or src_row >= self._dnd_count():
            # 无效目标：无顺序变化
            e.setDropAction(Qt.IgnoreAction)
            e.accept()
            self.viewport().update()
            return
        e.setDropAction(Qt.MoveAction)
        e.accept()
        self._move_row(src_row, dst_row)
        self.viewport().update()

    # ────────────────────── 指示线绘制 ──────────────────────

    def paintEvent(self, e):
        """叠加绘制插入位置指示线。"""
        super().paintEvent(e)
        if self._drag_armed and 0 <= self._drop_row <= self._dnd_count():
            painter = QPainter(self.viewport())
            painter.setPen(QPen(QColor("#4fc3f7"), 2))
            y = self._indicator_y(self._drop_row)
            if y is not None:
                painter.drawLine(0, y, self.viewport().width(), y)

    def _indicator_y(self, row):
        """插入指示线的 y 坐标：目标行上缘，或最后一行下缘。"""
        if row < self._dnd_count():
            return self._dnd_visual_rect(row).top() - 1
        if self._dnd_count() > 0:
            return self._dnd_visual_rect(self._dnd_count() - 1).bottom()
        return None


class DragReorderListWidget(DragReorderMixin, QListWidget):
    """QListWidget 版：所有行均可长按把手拖拽排序。

    注意：Mixin 必须排在 Qt 基类之前。若写作 (QListWidget, DragReorderMixin)，
    C3 线性化会把 Mixin 排到全部 Qt 类之后、object 之前，Mixin 内所有
    super().xxx() 调用都会落到 object 上（paintEvent/mousePressEvent 等
    虚方法抛 AttributeError 后直接触发 Qt abort，exit 127）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)

    # ────────────────────── Mixin 适配接口 ──────────────────────

    def _dnd_count(self) -> int:
        return self.count()

    def _dnd_item_at(self, pos):
        return self.itemAt(pos)

    def _dnd_row_of(self, item) -> int:
        return self.row(item) if item is not None else -1

    def _dnd_row_item(self, row):
        return self.item(row) if 0 <= row < self.count() else None

    def _dnd_to_top(self, item):
        return item

    def _dnd_take_row(self, row):
        return self.takeItem(row)

    def _dnd_insert_row(self, row, item):
        self.insertItem(row, item)

    def _dnd_visual_rect(self, row) -> QRect:
        if 0 <= row < self.count():
            return self.visualItemRect(self.item(row))
        return QRect()

    def _dnd_handle_rect(self) -> QRect:
        return QRect(
            self.viewport().width() - self.HANDLE_WIDTH, 0,
            self.HANDLE_WIDTH, self.viewport().height(),
        )

    def _dnd_row_allowed(self, row) -> bool:
        return True


class DragReorderTreeWidget(DragReorderMixin, QTreeWidget):
    """QTreeWidget 版：仅携带 UserRole 数据的顶层项可长按把手拖拽排序。

    用于「信号分组」面板：非搜索态下顶层项即分组信号（可拖拽排序）；
    跨分组搜索态下顶层项为分组标题（无 UserRole 数据），不参与拖拽。
    """

    def __init__(self, parent=None):
        super().__init__(parent)

    # ────────────────────── Mixin 适配接口 ──────────────────────

    def _dnd_count(self) -> int:
        return self.topLevelItemCount()

    def _dnd_item_at(self, pos):
        return self.itemAt(pos)

    def _dnd_row_of(self, item) -> int:
        return self.indexOfTopLevelItem(item) if item is not None else -1

    def _dnd_row_item(self, row):
        return self.topLevelItem(row) if 0 <= row < self.topLevelItemCount() else None

    def _dnd_to_top(self, item):
        parent = item.parent()
        return parent if parent is not None else item

    def _dnd_take_row(self, row):
        return self.takeTopLevelItem(row)

    def _dnd_insert_row(self, row, item):
        self.insertTopLevelItem(row, item)

    def _dnd_visual_rect(self, row) -> QRect:
        if 0 <= row < self.topLevelItemCount():
            return self.visualItemRect(self.topLevelItem(row))
        return QRect()

    def _dnd_handle_rect(self) -> QRect:
        # 把手位于第一列（信号名列）右侧，避免与第二列备注编辑框重叠
        return QRect(
            self.columnWidth(0) - self.HANDLE_WIDTH, 0,
            self.HANDLE_WIDTH, self.viewport().height(),
        )

    def _dnd_row_allowed(self, row) -> bool:
        item = self.topLevelItem(row) if 0 <= row < self.topLevelItemCount() else None
        return item is not None and item.data(0, Qt.UserRole) is not None
