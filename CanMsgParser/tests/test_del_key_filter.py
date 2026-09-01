import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication, QListWidget
from PyQt5.QtCore import Qt, QEvent
from PyQt5.QtGui import QKeyEvent

from widgets.del_key_filter import DelKeyFilter
from widgets.realtime_monitor_widget import RealtimeMonitorWidget
from widgets.signal_group_panel import SignalGroupPanel
from widgets.signal_sim_widget import SignalSimWidget

app = QApplication(sys.argv)


def _send_del(widget):
    """向目标控件同步派发一次 Delete 键按下事件（会经事件过滤器拦截）。"""
    QApplication.sendEvent(
        widget, QKeyEvent(QEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier)
    )


def test_del_key_filter_unit():
    """DelKeyFilter 单元：Delete 键应触发回调并删除选中项。"""
    lw = QListWidget()
    lw.addItem("a")
    lw.addItem("b")
    lw.item(0).setSelected(True)
    called = []
    f = DelKeyFilter(lw, lambda: (called.append(1), lw.takeItem(0)))
    _send_del(lw)
    assert called == [1], "Delete 键应触发回调"
    assert lw.count() == 1, "回调删除后仅剩 1 项"
    print("    OK: DelKeyFilter 单元——Delete 触发回调并删除选中项")


def test_del_on_realtime_sel_list():
    """实时监控页：选中已选信号按 Delete 应移除。"""
    w = RealtimeMonitorWidget()
    w.add_selected_signals([("MsgA", "Sig1"), ("MsgA", "Sig2")])
    assert w._sel_list.count() == 2, "应已添加 2 个已选信号"
    w._sel_list.item(0).setSelected(True)
    _send_del(w._sel_list)
    assert w._sel_list.count() == 1, "按 Delete 后应移除 1 个已选信号"
    assert w._sel_signals == [("MsgA", "Sig2")], f"剩余应为 Sig2, got={w._sel_signals}"
    print("    OK: 实时监控页——选中信号按 Delete 移除")


def test_del_on_group_sig_list():
    """信号分组页：选中组内信号按 Delete 应移除。"""
    w = SignalGroupPanel()
    # 注入一个最简「DBC」使信号匹配（主窗口在加载 DBC 后也会 set_messages），
    # 否则信号会被判为「当前 DBC 未匹配」而置灰为 disabled，无法被选中。
    w._messages = [
        SimpleNamespace(name="MsgA", signals=[
            SimpleNamespace(name="Sig1"), SimpleNamespace(name="Sig2"),
        ])
    ]
    w.add_signals([("MsgA", "Sig1", "0x1"), ("MsgA", "Sig2", "0x1")])
    assert w._sig_list.topLevelItemCount() == 2, "默认分组应有 2 个信号"
    w._sig_list.topLevelItem(0).setSelected(True)
    _send_del(w._sig_list)
    assert w._sig_list.topLevelItemCount() == 1, "按 Delete 后应移除 1 个组内信号"
    print("    OK: 信号分组页——选中信号按 Delete 移除")


def test_del_on_sim_sel_list():
    """模拟上报页：选中已选信号按 Delete 应移除。

    说明：聚焦验证「DEL -> _remove_selected -> 列表刷新」链路。这里直接驱动
    _sel_signals + _refresh_sel_list，而不走 add_selected_signals（后者经
    QTimer.singleShot 触发的 _add_table_rows 需要完整含 frame_id 的 key，与
    本次 DEL 快捷键验证无关）。
    """
    w = SignalSimWidget()
    w._sel_signals = {("MsgA", "Sig1"), ("MsgA", "Sig2")}
    w._refresh_sel_list()
    assert w._sel_list.count() == 2, "模拟上报已选列表应有 2 项"
    w._sel_list.item(0).setSelected(True)
    _send_del(w._sel_list)
    assert w._sel_list.count() == 1, "按 Delete 后应移除 1 个已选信号"
    assert ("MsgA", "Sig2") in w._sel_signals, "剩余应为 Sig2"
    print("    OK: 模拟上报页——选中已选信号按 Delete 移除")


def test_del_on_signal_tree_checked_list():
    """信号树组件：选中已勾选信号按 Delete 应移除（并同步取消搜索树勾选）。"""
    from widgets.signal_tree import SignalTreeWidget
    from types import SimpleNamespace
    w = SignalTreeWidget()
    w.load_messages([SimpleNamespace(name="MsgA", frame_id=0x1, signals=[
        SimpleNamespace(name="Sig1", unit=""), SimpleNamespace(name="Sig2", unit=""),
    ])])
    w.set_signal_checked("MsgA", "Sig1", True)
    w.set_signal_checked("MsgA", "Sig2", True)
    assert w._checked_list.count() == 2, "勾选后应列出 2 个已勾选信号"
    w._checked_list.item(0).setSelected(True)
    _send_del(w._checked_list)
    assert w._checked_list.count() == 1, "按 Delete 后应移除 1 个已勾选信号"
    print("    OK: 信号树——选中已勾选信号按 Delete 移除")


def test_del_on_mainwindow_selected_list():
    """主窗口曲线图页：选中已选信号按 Delete 应移除。"""
    from main_window import MainWindow
    w = MainWindow()
    w._curve_signals = [("MsgA", "Sig1"), ("MsgA", "Sig2")]
    w._refresh_curve_list()
    assert w._selected_list.count() == 2, "曲线图已选列表应有 2 项"
    w._selected_list.item(0).setSelected(True)
    _send_del(w._selected_list)
    assert w._selected_list.count() == 1, "按 Delete 后应移除 1 个已选信号"
    assert ("MsgA", "Sig2") in w._curve_signals, "剩余应为 Sig2"
    print("    OK: 曲线图页（主窗口）——选中已选信号按 Delete 移除")


if __name__ == "__main__":
    test_del_key_filter_unit()
    test_del_on_realtime_sel_list()
    test_del_on_group_sig_list()
    test_del_on_sim_sel_list()
    test_del_on_signal_tree_checked_list()
    test_del_on_mainwindow_selected_list()
    print("ALL DEL-KEY TESTS PASSED")
