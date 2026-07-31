import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
from widgets.signal_tree import SignalTreeWidget
from widgets.signal_group_panel import SignalGroupPanel

app = QApplication.instance() or QApplication(sys.argv)


def test_search_view_add_to_group_emits():
    """信号检索视图「加入分组」按钮应发射 add_to_group_requested（含已勾选信号）。"""
    tree = SignalTreeWidget()
    tree.load_messages([SimpleNamespace(name="MsgA", frame_id=0x1, signals=[
        SimpleNamespace(name="Sig1", unit="")])])
    tree.set_signal_checked("MsgA", "Sig1", True)
    got = []
    tree.add_to_group_requested.connect(lambda s: got.append(s))
    tree._add_to_group_btn.click()
    assert got and got[0] == [("MsgA", "Sig1")], f"加入分组应发射勾选信号, got={got}"
    print("    OK: 信号检索视图「加入分组」发射 add_to_group_requested")


def test_search_view_links_group_panel():
    """信号检索 -> 信号分组 联动：将勾选信号加入分组视图当前分组。"""
    tree = SignalTreeWidget()
    panel = SignalGroupPanel()
    panel._messages = [SimpleNamespace(name="MsgA", signals=[SimpleNamespace(name="Sig1")])]
    tree.add_to_group_requested.connect(panel.add_signals)
    tree.load_messages([SimpleNamespace(name="MsgA", frame_id=0x1, signals=[
        SimpleNamespace(name="Sig1", unit="")])])
    tree.set_signal_checked("MsgA", "Sig1", True)
    tree._add_to_group_btn.click()
    assert panel.get_current_group_name() != "", "应已建立分组"
    assert len(panel._groups[0].signals) == 1, "信号应已加入分组"
    print("    OK: 信号检索 -> 信号分组 联动加入")


if __name__ == "__main__":
    test_search_view_add_to_group_emits()
    test_search_view_links_group_panel()
    print("ALL SIGNAL RETRIEVAL VIEW TESTS PASSED")
