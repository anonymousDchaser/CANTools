# tests/test_signal_search_checkall.py
"""Bug2 回归：信号检索 / 信号分组 改为单一「全选」勾选框。

行为要求：
- 已无「取消全选」勾选框；
- 勾选全选框 -> 选中所有可见信号；
- 取消全选框 -> 取消所有可见信号；
- 全选状态下取消任一可见信号 -> 全选框自动变为未勾选。
"""
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt

from widgets.signal_tree import SignalTreeWidget
from widgets.signal_group_panel import SignalGroupPanel

APP = QApplication.instance() or QApplication([])


def test_search_view_single_checkall():
    print("[1] 信号检索视图 单一全选框 ...")
    tree = SignalTreeWidget()
    tree.load_messages([
        SimpleNamespace(name="MsgA", frame_id=0x1, signals=[
            SimpleNamespace(name="Sig1", unit=""),
            SimpleNamespace(name="Sig2", unit=""),
        ]),
        SimpleNamespace(name="MsgB", frame_id=0x2, signals=[
            SimpleNamespace(name="Sig3", unit=""),
        ]),
    ])
    # 已无「取消全选」勾选框
    assert not hasattr(tree, "_uncheck_all_chk"), "应移除取消全选勾选框"

    # 勾选全选框 -> 所有可见信号勾选
    tree._check_all_chk.setChecked(True)
    checked = tree.get_checked_signals()
    assert len(checked) == 3, f"全选应勾选 3 个信号, got={checked}"
    assert tree._check_all_chk.isChecked() is True

    # 取消一个可见信号 -> 全选框自动取消
    sig1 = tree._tree.topLevelItem(0).child(0)
    sig1.setCheckState(0, Qt.Unchecked)
    tree._on_item_changed(sig1, 0)
    assert tree._check_all_chk.isChecked() is False, "存在未勾选信号时全选框应取消"
    assert len(tree.get_checked_signals()) == 2

    # 再次全选
    tree._check_all_chk.setChecked(True)
    assert tree._check_all_chk.isChecked() is True

    # 取消全选框 -> 所有可见信号取消
    tree._check_all_chk.setChecked(False)
    assert tree.get_checked_signals() == [], "取消全选框应取消所有可见信号"
    print("    OK: 信号检索 单一全选框勾选/取消/自动联动 正确")
    print("[1] 通过\n")


def _iter_group_items(w):
    items = []
    for i in range(w._sig_list.topLevelItemCount()):
        top = w._sig_list.topLevelItem(i)
        if top.flags() & Qt.ItemIsUserCheckable and top.data(0, Qt.UserRole) is not None:
            items.append(top)
        for j in range(top.childCount()):
            child = top.child(j)
            if child.flags() & Qt.ItemIsUserCheckable and child.data(0, Qt.UserRole) is not None:
                items.append(child)
    return items


def test_group_panel_single_checkall():
    print("[2] 信号分组视图 单一全选框 ...")
    w = SignalGroupPanel()
    w._messages = [SimpleNamespace(name="MsgA", signals=[
        SimpleNamespace(name="SigOne"), SimpleNamespace(name="SigTwo")])]
    w.add_signals([("MsgA", "SigOne", "0x100"), ("MsgA", "SigTwo", "0x100")])
    assert not hasattr(w, "_uncheck_all_chk"), "应移除取消全选勾选框"

    # 全选
    w._set_visible_checked(True)
    assert w._check_all_chk.isChecked() is True, "全选后勾选框应勾选"
    items = _iter_group_items(w)
    assert len(items) == 2 and all(
        it.checkState(0) == Qt.Checked for it in items), "两个信号均应被勾选"

    # 取消一个 -> 全选框自动取消
    items[0].setCheckState(0, Qt.Unchecked)
    w._on_sig_checked(items[0], 0)
    assert w._check_all_chk.isChecked() is False, "存在未勾选信号时全选框应取消"

    # 再全选
    w._set_visible_checked(True)
    assert w._check_all_chk.isChecked() is True

    # 取消全选框 -> 所有可见取消
    w._check_all_chk.setChecked(False)
    items = _iter_group_items(w)
    assert all(it.checkState(0) == Qt.Unchecked for it in items), \
        "取消全选框应取消所有可见信号"
    print("    OK: 信号分组 单一全选框勾选/取消/自动联动 正确")
    print("[2] 通过\n")


if __name__ == "__main__":
    test_search_view_single_checkall()
    test_group_panel_single_checkall()
    print("SIGNAL SEARCH CHECKALL TESTS PASSED")
