import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
from widgets.signal_group_panel import SignalGroupPanel

app = QApplication.instance() or QApplication(sys.argv)


def test_group_search_filter():
    """Task #1：组内信号搜索框按信号名/报文名/备注内容过滤（不改动底层数据）。"""
    w = SignalGroupPanel()
    w.add_signals([
        ("MsgA", "SigOne", "0x100"),
        ("MsgA", "SigTwo", "0x100"),
    ])
    # 给第二个信号加备注（需先构建列表后才能拿到 item 的 SignalRef）
    grp = w._groups[0]
    grp.signals[1].remark = "刹车助力模式"
    w._refresh_signal_list()

    assert w._sig_list.topLevelItemCount() == 2, "应有 2 个信号"

    # 搜索信号名 SigOne -> 仅 SigOne 可见
    w._sig_search.setText("SigOne")
    assert w._sig_list.topLevelItem(0).isHidden() is False
    assert w._sig_list.topLevelItem(1).isHidden() is True
    print("    OK: 按信号名过滤 -> 仅 SigOne 可见")

    # 搜索报文名 MsgA -> 两个都可见
    w._sig_search.setText("MsgA")
    assert w._sig_list.topLevelItem(0).isHidden() is False
    assert w._sig_list.topLevelItem(1).isHidden() is False
    print("    OK: 按报文名过滤 -> 全部可见")

    # 搜索备注内容 -> 仅带该备注的 SigTwo 可见
    w._sig_search.setText("刹车")
    assert w._sig_list.topLevelItem(0).isHidden() is True
    assert w._sig_list.topLevelItem(1).isHidden() is False
    print("    OK: 按备注内容过滤 -> 仅 SigTwo 可见")

    # 清空搜索 -> 全部恢复可见，底层数据未变
    w._sig_search.setText("")
    assert w._sig_list.topLevelItem(0).isHidden() is False
    assert w._sig_list.topLevelItem(1).isHidden() is False
    assert len(grp.signals) == 2, "过滤不应改动底层分组数据"
    print("    OK: 清空搜索恢复可见，底层数据不变")


if __name__ == "__main__":
    test_group_search_filter()
    print("ALL GROUP SEARCH TESTS PASSED")
