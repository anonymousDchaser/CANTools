"""验证实时监控页：停止监控后切换「共享Y轴/独立子图」图表不消失。

核心回归：原 stop_realtime 把 _realtime=False 并清空缓冲，
导致 redraw 落入「请勾选信号」占位图分支，曲线被清掉。
"""
import sys

from PyQt5.QtWidgets import QApplication


def test_plot_freeze_after_stop():
    app = QApplication(sys.argv)
    from widgets.plot_widget import PlotWidget

    pw = PlotWidget()
    meta = [(0x100, "MsgA", "Sig1"), (0x100, "MsgA", "Sig2")]
    pw.start_realtime(meta)

    # 推送若干采样点
    for i in range(10):
        pw.push_sample(0x100, "MsgA", "Sig1", float(i), float(i))
        pw.push_sample(0x100, "MsgA", "Sig2", float(i), float(i) * 2)

    assert pw._realtime is True
    assert pw._rt_running is True
    assert pw._rt_buffers[(0x100, "MsgA", "Sig1")]["t"] == list(range(10))

    # 停止监控（保留画面）
    pw.stop_realtime()
    assert pw._realtime is True, "停止后应保持实时模式以便保留画面"
    assert pw._rt_running is False, "停止后不应再接收采样"
    assert pw._rt_buffers[(0x100, "MsgA", "Sig1")]["t"] == list(range(10)), "缓冲应保留最后画面数据"

    # 停止后继续 push 不应再改变数据
    pw.push_sample(0x100, "MsgA", "Sig1", 999.0, 999.0)
    assert pw._rt_buffers[(0x100, "MsgA", "Sig1")]["t"] == list(range(10)), "停止后 push 被忽略"

    # 显式切到共享Y轴，再切到独立子图：图表不应消失
    # （曲线图默认已为独立子图，这里先回到共享Y轴再验证双向切换均保留画面）
    pw.set_subplot_mode(False)  # -> 共享Y轴
    pw._toggle_mode()  # -> 独立子图
    assert pw._subplot_mode is True
    assert pw._realtime is True
    # 独立子图模式应有 2 个子图 axes，且至少一个 line 含数据
    assert len(pw._fig.axes) == 2, f"独立子图应有2个axes, 实际{len(pw._fig.axes)}"
    total_pts = sum(len(ln.get_xdata()) for ax in pw._fig.axes for ln in ax.get_lines())
    assert total_pts > 0, "切换为独立子图后曲线数据应保留（不应消失）"

    # 独立子图 -> 共享Y轴：图表仍不消失
    pw._toggle_mode()  # -> 共享Y轴
    assert pw._subplot_mode is False
    assert len(pw._fig.axes) == 1, f"共享Y轴应有1个axes, 实际{len(pw._fig.axes)}"
    total_pts = sum(len(ln.get_xdata()) for ax in pw._fig.axes for ln in ax.get_lines())
    assert total_pts > 0, "切回共享Y轴后曲线数据应保留（不应消失）"

    # 重新开始时缓冲应被重置
    pw.start_realtime(meta)
    assert pw._rt_running is True
    assert pw._rt_buffers[(0x100, "MsgA", "Sig1")]["t"] == [], "重新开始应清空缓冲"
    print("OK: 停止监控后切换模式图表保留，重新开始时缓冲重置")


if __name__ == "__main__":
    test_plot_freeze_after_stop()
    print("PLOT_FREEZE_TEST_PASSED")
