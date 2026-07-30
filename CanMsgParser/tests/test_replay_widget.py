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


if __name__ == "__main__":
    test_replay_filters_and_sends()
    test_replay_loop_stops()
    print("ALL REPLAY WIDGET TESTS PASSED")
