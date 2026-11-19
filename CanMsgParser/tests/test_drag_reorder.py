# tests/test_drag_reorder.py
"""列表/树「长按把手拖拽排序」回归测试（offscreen）。

覆盖用户实际会走的路径：
- 长按右侧把手 → 进入可拖状态（把手高亮）；
- 拖动 → 插入指示行实时更新；松手 → 行顺序真的变了并发出 orderChanged；
- 在行中部（非把手）按下不会误触发拖拽；
- 信号分组树里，未携带 payload 的顶层项（分组标题）不参与拖拽；
- 原地松手不算变化、不发信号。

这些用例在 1.5.0 之前缺失，导致「把手在、拖不动」的回归没被拦住。
拖拽实现已改为纯 Qt 内部（不依赖 QDrag/OLE），因此可用 QTest 直接驱动。
"""
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QPoint, QPointF, QEvent
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QTreeWidgetItem
from PyQt6.QtTest import QTest

_app = QApplication.instance() or QApplication(sys.argv)

from widgets.drag_reorder_list import (
    DragReorderListWidget, DragReorderMixin, DragReorderTreeWidget,
)
from widgets.item_payload import set_payload


def _make_list(texts=("A", "B", "C", "D")):
    w = DragReorderListWidget()
    w.resize(320, 260)
    for t in texts:
        w.addItem(t)
    w.show()
    QTest.qWait(80)
    return w


def _handle_pos(w, row):
    """该行把手区域内的一个点（viewport 坐标）。"""
    rect = w._dnd_visual_rect(row)
    hx = w._dnd_handle_rect().center().x()
    return QPoint(hx, rect.center().y())


def _press_handle(w, row):
    pos = _handle_pos(w, row)
    QTest.mousePress(w.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, pos)
    return pos


def _drag_to(w, pos):
    """发一个「左键按住」的 MouseMove（QTest.mouseMove 不带按键状态）。"""
    vp = w.viewport()
    ev = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(pos),
        QPointF(vp.mapToGlobal(pos)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(vp, ev)


def _release(w, pos):
    QTest.mouseRelease(w.viewport(), Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier, pos)


def _texts(w):
    return [w.item(i).text() for i in range(w.count())]


def test_long_press_arms_drag():
    """长按把手超过 LONG_PRESS_MS 后进入可拖状态。"""
    w = _make_list()
    pos = _press_handle(w, 0)
    assert w._press_on_handle is True, "按在把手区却未识别为把手按下"
    QTest.qWait(DragReorderMixin.LONG_PRESS_MS + 120)
    assert w._drag_armed is True, "长按后没有进入可拖拽状态"
    assert w.armedRow() == 0
    _release(w, pos)


def test_drag_down_moves_row_and_emits():
    """把第 1 行拖到第 3 行下方：顺序变为 B C A D，且发出 orderChanged。"""
    w = _make_list()
    fired = []
    w.orderChanged.connect(lambda: fired.append(1))

    start = _press_handle(w, 0)
    QTest.qWait(DragReorderMixin.LONG_PRESS_MS + 120)
    assert w._drag_armed

    rect2 = w._dnd_visual_rect(2)
    target = QPoint(start.x(), rect2.bottom() - 1)   # 落在第 3 行下半 → 插入位 3
    _drag_to(w, target)
    assert w._drop_row == 3, f"插入行应为 3，实际 {w._drop_row}"

    _release(w, target)
    assert _texts(w) == ["B", "C", "A", "D"], f"实际顺序 {_texts(w)}"
    assert fired, "顺序变化后没有发出 orderChanged"
    assert w.lastMovedRow() == 2


def test_drag_up_moves_row():
    """把最后一行拖到最上方：D 到第一位。"""
    w = _make_list()
    start = _press_handle(w, 3)
    QTest.qWait(DragReorderMixin.LONG_PRESS_MS + 120)
    assert w._drag_armed

    rect0 = w._dnd_visual_rect(0)
    target = QPoint(start.x(), rect0.top() + 1)      # 第 1 行上半 → 插入位 0
    _drag_to(w, target)
    assert w._drop_row == 0

    _release(w, target)
    assert _texts(w) == ["D", "A", "B", "C"], f"实际顺序 {_texts(w)}"


def test_click_row_body_does_not_arm():
    """行中部按下（非把手）不应进入拖拽，避免误触。"""
    w = _make_list()
    rect = w._dnd_visual_rect(1)
    pos = QPoint(20, rect.center().y())
    QTest.mousePress(w.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, pos)
    QTest.qWait(DragReorderMixin.LONG_PRESS_MS + 120)
    assert w._press_on_handle is False
    assert w._drag_armed is False, "非把手区域按下却激活了拖拽"
    _release(w, pos)


def test_drop_in_place_does_not_emit():
    """原地松手：不移动、不发 orderChanged。"""
    w = _make_list()
    fired = []
    w.orderChanged.connect(lambda: fired.append(1))

    start = _press_handle(w, 1)
    QTest.qWait(DragReorderMixin.LONG_PRESS_MS + 120)
    assert w._drag_armed

    rect1 = w._dnd_visual_rect(1)
    target = QPoint(start.x(), rect1.top() + 1)      # 仍是插入位 1
    _drag_to(w, target)
    _release(w, target)

    assert _texts(w) == ["A", "B", "C", "D"]
    assert not fired, "原地松手不应发出 orderChanged"


def test_tree_only_payload_rows_draggable():
    """信号分组树：带 payload 的顶层项可拖，分组标题行不可拖。"""
    w = DragReorderTreeWidget()
    w.resize(360, 260)
    item_a = QTreeWidgetItem(["sig_A"])
    set_payload(item_a, {"sig": "A"})
    item_b = QTreeWidgetItem(["sig_B"])
    set_payload(item_b, {"sig": "B"})
    group = QTreeWidgetItem(["分组标题"])          # 无 payload
    w.addTopLevelItem(item_a)
    w.addTopLevelItem(item_b)
    w.addTopLevelItem(group)
    w.show()
    QTest.qWait(80)

    assert w._dnd_row_allowed(0) is True
    assert w._dnd_row_allowed(1) is True
    assert w._dnd_row_allowed(2) is False, "无 payload 的顶层项不应可拖拽"

    # 在分组标题行的把手位置按下，不应激活
    pos = _handle_pos(w, 2)
    QTest.mousePress(w.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, pos)
    QTest.qWait(DragReorderMixin.LONG_PRESS_MS + 120)
    assert w._drag_armed is False
    _release(w, pos)


def test_tree_drag_reorders_top_level():
    """树版拖拽同样能改变顶层项顺序。"""
    w = DragReorderTreeWidget()
    w.resize(360, 260)
    for name in ("s1", "s2", "s3"):
        it = QTreeWidgetItem([name])
        set_payload(it, {"sig": name})
        w.addTopLevelItem(it)
    w.show()
    QTest.qWait(80)

    start = _handle_pos(w, 0)
    QTest.mousePress(w.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, start)
    QTest.qWait(DragReorderMixin.LONG_PRESS_MS + 120)
    assert w._drag_armed

    rect1 = w._dnd_visual_rect(1)
    target = QPoint(start.x(), rect1.bottom() - 1)
    _drag_to(w, target)
    _release(w, target)

    assert [w.topLevelItem(i).text(0) for i in range(w.topLevelItemCount())] == \
        ["s2", "s1", "s3"]
