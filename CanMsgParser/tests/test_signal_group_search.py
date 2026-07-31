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
    assert w._uncheck_all_chk.isChecked() is False
    print("    OK: re-search resets checkbox state")


if __name__ == "__main__":
    test_group_search_cross_group()
    test_group_search_checkbox_reset_on_research()
    print("ALL GROUP SEARCH TESTS PASSED")
