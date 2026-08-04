# tests/test_realtime_remove_sync.py
"""Bug1 回归：实时监控页移除已选信号后，曲线与采集应同步停止。

覆盖三层：
- PlotWidget.remove_realtime_signals / add_realtime_signal（曲线增删）
- CanCaptureWorker.sync_signals（采集集合增删，含线程安全锁）
- RealtimeMonitorWidget._sync_monitor_signals（选择变化后驱动曲线+采集同步）
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication

APP = QApplication.instance() or QApplication([])

from widgets.plot_widget import PlotWidget
from workers.can_capture_worker import CanCaptureWorker
from widgets.realtime_monitor_widget import RealtimeMonitorWidget

DBC_TEXT = """VERSION ""

NS_ :

BS_:

BU_:

BO_ 972 Msg3CC: 8 Vector__XXX
 SG_ A : 0|8@1+ (1,0) [0|255] "" Vector__XXX
 SG_ B : 8|8@1+ (1,0) [0|255] "" Vector__XXX
"""


def make_dbc():
    fd, path = tempfile.mkstemp(suffix=".dbc")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(DBC_TEXT)
    return path


def test_plot_remove_add_realtime_signal():
    print("[1] PlotWidget 实时信号增删 ...")
    p = PlotWidget()
    p.start_realtime([(0x3CC, "Msg3CC", "A"), (0x3CC, "Msg3CC", "B")])
    assert len(p._rt_meta) == 2, "start_realtime 应建立 2 条曲线"
    # 移除 B（保留 A）
    p.remove_realtime_signals({("Msg3CC", "A")})
    assert len(p._rt_meta) == 1, "应移除不在选择中的 B"
    assert p._rt_meta[0] == (0x3CC, "Msg3CC", "A"), f"应保留 A, got={p._rt_meta}"
    # 全部移除 -> 回到非实时占位（_rt_meta 清空）
    p.remove_realtime_signals(set())
    assert len(p._rt_meta) == 0, "清空选择应移除全部曲线"
    # 运行时新增
    p.add_realtime_signal(0x3CC, "Msg3CC", "C")
    assert p.has_realtime_signal("Msg3CC", "C"), "新增 C 应进入实时模式"
    assert not p.has_realtime_signal("Msg3CC", "Z"), "未新增的信号不应存在"
    print("    OK: PlotWidget.remove_realtime_signals / add_realtime_signal 正确")
    print("[1] 通过\n")


def test_worker_sync_signals():
    print("[2] CanCaptureWorker.sync_signals 集合增删 ...")
    dbc = make_dbc()
    try:
        w = CanCaptureWorker(dbc, [("Msg3CC", "A")])
        assert ("Msg3CC", "A") in w._signals, "初始应含 A"
        # 新增 B
        w.sync_signals({("Msg3CC", "A"), ("Msg3CC", "B")})
        assert ("Msg3CC", "B") in w._signals, "sync 应加入缺失的 B"
        # frame_map 同步（0x3CC 同时映射 A、B）
        assert ("Msg3CC", "A") in w._frame_map.get(0x3CC, [])
        assert ("Msg3CC", "B") in w._frame_map.get(0x3CC, [])
        # 移除 B
        w.sync_signals({("Msg3CC", "A")})
        assert ("Msg3CC", "B") not in w._signals, "sync 应移除不在选择中的 B"
        assert ("Msg3CC", "A") in w._frame_map.get(0x3CC, [])
        print("    OK: CanCaptureWorker.sync_signals 增删与 frame_map 同步正确")
    finally:
        os.unlink(dbc)
    print("[2] 通过\n")


def test_monitor_sync_removes_curve_and_capture():
    print("[3] RealtimeMonitorWidget._sync_monitor_signals 移除时同步曲线+采集 ...")
    w = RealtimeMonitorWidget.__new__(RealtimeMonitorWidget)
    w._monitoring = True
    w._sel_signals = {("Msg3CC", "A"), ("Msg3CC", "B")}

    class StubPlot:
        def __init__(self):
            self._rt_meta = [(0x3CC, "Msg3CC", "A"), (0x3CC, "Msg3CC", "B")]

        def remove_realtime_signals(self, sel):
            self._rt_meta = [k for k in self._rt_meta if (k[1], k[2]) in sel]

        def has_realtime_signal(self, m, s):
            return any(k[1] == m and k[2] == s for k in self._rt_meta)

        def add_realtime_signal(self, fid, m, s):
            self._rt_meta.append((fid, m, s))

    class StubWorker:
        def __init__(self):
            self._signals = [("Msg3CC", "A"), ("Msg3CC", "B")]

        def sync_signals(self, sel):
            cur = set(self._signals)
            need = [s for s in sel if s not in cur]
            self._signals = [s for s in self._signals if s in sel]
            self._signals.extend(need)

    w._plot = StubPlot()
    w._plot._realtime = True
    w._capture_worker = StubWorker()
    w._frame_id_of = lambda m: 0x3CC

    # 移除 B
    w._sel_signals = {("Msg3CC", "A")}
    w._sync_monitor_signals()
    assert (0x3CC, "Msg3CC", "B") not in w._plot._rt_meta, "B 曲线应被移除"
    assert (0x3CC, "Msg3CC", "A") in w._plot._rt_meta, "A 曲线应保留"
    assert ("Msg3CC", "B") not in w._capture_worker._signals, "B 采集应停止"
    print("    OK: 移除信号后曲线与采集同步停止")

    # 监控中新增 C：应同步进曲线与采集
    w._sel_signals = {("Msg3CC", "A"), ("Msg3CC", "C")}
    w._sync_monitor_signals()
    assert (0x3CC, "Msg3CC", "C") in w._plot._rt_meta, "新增 C 应进入曲线"
    assert ("Msg3CC", "C") in w._capture_worker._signals, "新增 C 应进入采集"
    print("    OK: 监控中新增信号同步进曲线与采集")
    print("[3] 通过\n")


if __name__ == "__main__":
    test_plot_remove_add_realtime_signal()
    test_worker_sync_signals()
    test_monitor_sync_removes_curve_and_capture()
    print("REALTIME REMOVE SYNC TESTS PASSED")
