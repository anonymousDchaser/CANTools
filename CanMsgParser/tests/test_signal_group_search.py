import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
from widgets.signal_group_panel import SignalGroupPanel, SignalGroup, SignalRef

app = QApplication.instance() or QApplication(sys.argv)


def _iter_signal_items(w):
    """Iterate all signal items (top-level + cross-group children)."""
    items = []
    for i in range(w._sig_list.topLevelItemCount()):
        top = w._sig_list.topLevelItem(i)
        if top.data(0, Qt.UserRole) is not None:
            items.append(top)
        for j in range(top.childCount()):
            child = top.child(j)
            if child.data(0, Qt.UserRole) is not None:
                items.append(child)
    return items


def _find(w, sig_name):
    for it in _iter_signal_items(w):
        sr = it.data(0, Qt.UserRole)
        if sr is not None and sr.sig_name == sig_name:
            return it
    return None


def _mk_widget():
    w = SignalGroupPanel()
    # Provide matching DBC so signals become checkable
    w._messages = [
        SimpleNamespace(name="MsgA", signals=[
            SimpleNamespace(name="SigOne"), SimpleNamespace(name="SigTwo")]),
        SimpleNamespace(name="MsgB", signals=[SimpleNamespace(name="SigThree")]),
    ]
    w.add_signals([("MsgA", "SigOne", "0x100"), ("MsgA", "SigTwo", "0x100")])
    # Second group containing SigThree
    w._groups.append(SignalGroup(name="GroupB"))
    w._groups[1].signals.append(SignalRef("MsgB", "SigThree", "0x200"))
    w._groups[1].signals[0].remark = "backup brake signal"
    w._refresh_signal_list()
    return w


def test_group_search_cross_group():
    """Cross-group search: filter by sig/msg/remark, grouped by owning group, data unchanged."""
    w = _mk_widget()
    # No search: current group (default) shows 2 top-level signal items
    assert w._sig_list.topLevelItemCount() == 2, "no search should show current group 2 signals"

    # Search signal name SigThree -> cross-group hit on GroupB only
    w._sig_search.setText("SigThree")
    assert w._sig_list.topLevelItemCount() == 1, "only GroupB matches -> 1 group header"
    it = _find(w, "SigThree")
    assert it is not None and not it.isHidden(), "SigThree should be visible"
    assert _find(w, "SigOne") is None, "SigOne not in result"

    # Search message name MsgA -> both default-group signals match
    w._sig_search.setText("MsgA")
    assert _find(w, "SigOne") is not None and not _find(w, "SigOne").isHidden()
    assert _find(w, "SigTwo") is not None and not _find(w, "SigTwo").isHidden()
    assert _find(w, "SigThree") is None

    # Search remark -> only matching SigThree visible
    w._sig_search.setText("backup brake")
    it = _find(w, "SigThree")
    assert it is not None and not it.isHidden()
    assert _find(w, "SigOne") is None

    # Clear -> restore current group fully visible, underlying data unchanged
    w._sig_search.setText("")
    assert _find(w, "SigOne") is not None and not _find(w, "SigOne").isHidden()
    assert _find(w, "SigTwo") is not None and not _find(w, "SigTwo").isHidden()
    assert len(w._groups[0].signals) == 2, "filter must not alter underlying group data"
    print("    OK: cross-group search filter + restore + data unchanged")


def test_group_search_checkbox_reset_on_research():
    """Re-search should reset checked state (single search keeps only current checks)."""
    w = _mk_widget()
    it = _find(w, "SigOne")
    it.setCheckState(0, Qt.Checked)
    assert ("MsgA", "SigOne") in w.get_checked_signals(), "SigOne should be checked"

    # Re-search -> list rebuilt, checks cleared
    w._sig_search.setText("Sig")
    assert w.get_checked_signals() == [], "re-search should clear checks"
    assert w._check_all_chk.isChecked() is False
    print("    OK: re-search resets checkbox state")


def test_switch_group_clears_search():
    """切分组时若处于跨分组搜索态，必须清空搜索并切到所选分组。

    回归点：原实现在搜索态下 _refresh_signal_list 显示【全部】分组命中项，
    切换 combo 不改变列表，表现为"切了分组下方列表没切"（偶发，搜索框有残留词时）。
    """
    w = _mk_widget()
    w._refresh_combo()  # 让 combo 感知两个分组

    # 激活跨分组搜索（三个信号都含 'Sig'）
    w._sig_search.setText("Sig")
    assert w._search_text != "", "搜索应处于激活态"
    assert _find(w, "SigOne") is not None, "跨分组视图应含默认组信号"
    assert _find(w, "SigThree") is not None, "跨分组视图应含 GroupB 信号"

    # 模拟用户在 combo 选中 GroupB（index 1）
    w._on_group_changed(1)

    # 搜索被清空
    assert w._search_text == "", "切分组必须清空激活态搜索"
    assert w._sig_search.text() == "", "搜索框 UI 必须同步清空"
    # 列表切到所选分组（GroupB 仅 SigThree）
    assert w._current_group_idx == 1
    assert _find(w, "SigThree") is not None, "所选分组信号应可见"
    assert _find(w, "SigOne") is None, "其它分组信号切分组后应不可见"
    print("    OK: 切分组清空搜索并切到所选分组")


if __name__ == "__main__":
    test_group_search_cross_group()
    test_group_search_checkbox_reset_on_research()
    test_switch_group_clears_search()
    print("ALL GROUP SEARCH TESTS PASSED")
