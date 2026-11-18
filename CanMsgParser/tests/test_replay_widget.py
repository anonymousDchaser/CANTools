import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
import can
from widgets.replay_widget import ReplayWidget

app = QApplication.instance() or QApplication(sys.argv)


class _FakeBus:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)


class _FakeManager:
    def __init__(self, bus):
        self._bus = bus
        self._cfg = {"interface_type": "virtual",
                     "channel": "vreplay", "bitrate": 500000}

    def get_config(self):
        return dict(self._cfg)

    def ensure_connected(self, *a):
        return self._bus, None

    def dispatch(self, msg):
        pass


def _make_frames():
    return [
        can.Message(arbitration_id=0x100, data=b"\x01\x02",
                    is_extended_id=False, timestamp=1.0),
        can.Message(arbitration_id=0x100, data=b"\x03\x04",
                    is_extended_id=False, timestamp=1.05),
        can.Message(arbitration_id=0x200, data=b"\x05",
                    is_extended_id=False, timestamp=1.1),
    ]


def _pump(w, seconds=3.0, stop_after=None):
    """驱动 QTimer.singleShot 链，直到回放结束或超时。"""
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        if stop_after is not None:
            if len(w._bus.sent) >= stop_after:
                return
        if not w._playing and w._progress_label.text().startswith("完成"):
            return
        time.sleep(0.02)


def test_replay_filters_and_sends():
    """Task #3：回放按勾选 ID 过滤并按时序发送；无回放时不发送。"""
    w = ReplayWidget()
    bus = _FakeBus()
    w._manager = _FakeManager(bus)
    frames = _make_frames()
    w._frames = frames
    w._id_counts = {0x100: 2, 0x200: 1}
    w._refresh_id_list()
    assert w._id_list.count() == 2

    # 未开始回放 -> 不发送
    assert len(bus.sent) == 0

    # 全选（默认勾选），开始回放 1 次
    w._start_replay()
    assert w._playing is True
    _pump(w, seconds=3.0)
    assert w._playing is False, "回放应在 1 轮后结束"
    assert len(bus.sent) == 3, f"应发送 3 帧，实际 {len(bus.sent)}"
    # 时序保持：第 1 帧先发、第 3 帧（0x200）最后发
    assert bus.sent[0].arbitration_id == 0x100
    assert bus.sent[-1].arbitration_id == 0x200
    print("    OK: 全选回放 1 次 -> 3 帧按原时序发送")

    # 仅勾选 0x100 -> 只发该 ID 的 2 帧
    bus.sent.clear()
    w._id_list.item(1).setCheckState(0)  # 取消 0x200
    w._start_replay()
    _pump(w, seconds=3.0)
    assert len(bus.sent) == 2, f"仅 0x100 应发 2 帧，实际 {len(bus.sent)}"
    assert all(m.arbitration_id == 0x100 for m in bus.sent)
    print("    OK: 仅勾选 0x100 -> 只发 2 帧")


def test_replay_loop_stops():
    """Task #3：循环回放可正常停止。"""
    w = ReplayWidget()
    bus = _FakeBus()
    w._manager = _FakeManager(bus)
    w._frames = _make_frames()
    w._id_counts = {0x100: 2, 0x200: 1}
    w._refresh_id_list()
    w._loop_chk.setChecked(True)
    w._start_replay()
    _pump(w, seconds=1.0, stop_after=4)  # 发够 4 帧即停
    assert len(bus.sent) >= 4, f"循环回放应发送多轮，实际 {len(bus.sent)}"
    w._stop_replay()
    assert w._playing is False
    print("    OK: 循环回放可正常停止")


def test_replay_timing_accuracy():
    """1x 必须按原始时间轴推进（回归：旧实现逐帧 QTimer 只有 194 帧/秒）。"""
    w = ReplayWidget()
    bus = _FakeBus()
    w._manager = _FakeManager(bus)
    w._frames = [
        can.Message(arbitration_id=0x100, data=b"\x01",
                    is_extended_id=False, timestamp=1.0),
        can.Message(arbitration_id=0x100, data=b"\x02",
                    is_extended_id=False, timestamp=1.2),
        can.Message(arbitration_id=0x200, data=b"\x03",
                    is_extended_id=False, timestamp=1.4),
    ]
    w._id_counts = {0x100: 2, 0x200: 1}
    w._refresh_id_list()
    t0 = time.time()
    w._start_replay()
    _pump(w, seconds=5.0)
    elapsed = time.time() - t0
    assert w._playing is False, "1 轮应正常结束（含尾部时间轴）"
    assert 3 == len(bus.sent), f"应发 3 帧，实际 {len(bus.sent)}"
    assert 0.35 <= elapsed <= 0.8, (
        f"1x 播放 0.4s 的日志实际用了 {elapsed:.3f}s，时序明显偏离"
    )
    print(f"    OK: 1x 时序准确（0.4s 日志实测 {elapsed:.3f}s）")


def test_replay_auto_connect():
    """未在连接页连接时，回放页应自动连接（而不是静默「没反应」）。"""
    calls = []

    class _Mgr:
        def __init__(self):
            self.bus = _FakeBus()

        def get_config(self):
            return None                     # 尚未连接

        def ensure_connected(self, it, ch, br):
            calls.append((it, ch, br))
            return self.bus, None

        def dispatch(self, msg):
            pass

    w = ReplayWidget()
    mgr = _Mgr()
    w.set_connection_manager(mgr)
    w.set_connection("virtual", "vauto", 250000)
    w._frames = _make_frames()
    w._id_counts = {0x100: 2, 0x200: 1}
    w._refresh_id_list()
    w._start_replay()
    assert calls == [("virtual", "vauto", 250000)], calls
    _pump(w, seconds=1.0, stop_after=3)
    assert len(mgr.bus.sent) == 3, len(mgr.bus.sent)
    w._stop_replay()
    print("    OK: 未连接时按 set_connection 参数自动连接")


def test_replay_dispatch_fanout():
    """回放发出的帧要 fan-out 给监听者（实时报文/监控页才能看到）。"""
    got = []

    class _Mgr:
        def __init__(self):
            self.bus = _FakeBus()

        def get_config(self):
            return {"interface_type": "virtual", "channel": "x",
                    "bitrate": 500000}

        def ensure_connected(self, *a):
            return self.bus, None

        def dispatch(self, msg):
            got.append(msg)

    w = ReplayWidget()
    w._manager = _Mgr()
    w._frames = [can.Message(arbitration_id=0x300, data=b"\xaa",
                             is_extended_id=False, timestamp=1.0)
                 for _ in range(10)]
    w._id_counts = {0x300: 10}
    w._refresh_id_list()
    w._start_replay()
    _pump(w, seconds=2.0, stop_after=10)
    assert len(w._manager.bus.sent) == 10
    assert len(got) == 10, f"dispatch 应扇出 10 帧，实际 {len(got)}"
    w._stop_replay()
    print("    OK: 回放帧全部 fan-out 给监听者")


def test_replay_accepts_drop():
    """拖入日志文件：本页识别为可回放报文，.asc/.blf 再通知主窗口分析。"""
    import tempfile
    from PyQt5.QtCore import QMimeData, QUrl, QPointF, Qt
    from PyQt5.QtGui import QDropEvent

    fd, asc = tempfile.mkstemp(suffix=".asc")
    os.close(fd)
    with open(asc, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("date Wed Nov 18 10:49:23 AM 2026\n"
                 "base hex timestamps absolute\n"
                 "0.000000 1 317 Rx d 8 00 00 00 00 00 00 00 00\n"
                 "0.100000 1 10e Rx d 8 00 00 2b 00 4f 00 00 e4\n")

    w = ReplayWidget()
    seen = []
    w.file_dropped.connect(seen.append)

    def _drop(path):
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(path)])
        ev = QDropEvent(QPointF(10, 10), Qt.CopyAction, md,
                        Qt.LeftButton, Qt.NoModifier)
        # 返回 mime 一起持有：QDropEvent 不接管 QMimeData 的所有权，
        # 只返回事件时 mime 会被 GC 掉，Qt 侧留下野指针（实测静默退出）。
        return ev, md

    try:
        ev, md = _drop(asc)          # md 必须存活到事件处理结束
        w.dropEvent(ev)
        for _ in range(100):
            app.processEvents()
            if w._frames:
                break
            time.sleep(0.02)
        assert len(w._frames) == 2, f"拖入应加载 2 帧，实际 {len(w._frames)}"
        assert len(seen) == 1, "拖入 .asc 应通知主窗口同步分析"
        assert os.path.normcase(os.path.abspath(seen[0])) == \
            os.path.normcase(os.path.abspath(asc))

        # 同一文件重复 load_file 不应二次解析（正/反斜杠差异也要识别为同一份）
        thread = w._load_thread
        assert w.load_file(asc.replace("\\", "/")) is True
        assert w._load_thread is thread, "同一文件不应重复解析"

        # 不支持的格式：拒绝且不清空已加载报文
        bad = asc[:-4] + ".txt"
        open(bad, "w").write("x")
        assert w.load_file(bad) is False
        assert len(w._frames) == 2, "拒绝时应保留原有报文"
        os.unlink(bad)
    finally:
        os.unlink(asc)
        w.close()
    print("    OK: 拖入日志即成为可回放报文（并通知分析页）")


if __name__ == "__main__":
    test_replay_filters_and_sends()
    test_replay_loop_stops()
    test_replay_timing_accuracy()
    test_replay_auto_connect()
    test_replay_dispatch_fanout()
    test_replay_accepts_drop()
    print("ALL REPLAY WIDGET TESTS PASSED")
