import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
from widgets.signal_sim_widget import SignalSimWidget

app = QApplication.instance() or QApplication(sys.argv)


class _FakeBus:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)


class _FakeManager:
    def __init__(self, bus):
        self._bus = bus

    def ensure_connected(self, *a):
        return self._bus, None

    def dispatch(self, msg):
        pass


def _build_widget():
    w = SignalSimWidget()
    # 提供最小报文定义，使 _add_table_rows 能构建报文组
    w._messages = [
        SimpleNamespace(
            name="MsgA", frame_id=0x100,
            signals=[SimpleNamespace(name="SigOne", min_val=0, max_val=10,
                                     unit="", scale=1, offset=0, choices=None),
                     SimpleNamespace(name="SigTwo", min_val=0, max_val=10,
                                     unit="", scale=1, offset=0, choices=None)],
        )
    ]
    bus = _FakeBus()
    w._manager = _FakeManager(bus)
    w._dbc_path = "dummy.dbc"
    w._dbc = SimpleNamespace()  # 非空即可通过 _start_all 的 DBC 检查
    return w, bus


def test_button_reflects_state():
    """Task #2：顶部按钮按实际发送状态刷新（无发送=开始；有发送=停止）。"""
    w, bus = _build_widget()

    # 添加信号（经 singleShot 延迟建行），先处理事件
    w.add_selected_signals([("MsgA", "SigOne"), ("MsgA", "SigTwo")])
    app.processEvents()

    # 添加后未开始发送 -> 按钮应为「开始模拟上报」
    assert w._start_btn.text() == "开始模拟上报", \
        f"未发送时按钮应为开始，实际={w._start_btn.text()}"
    print("    OK: 添加信号未发送 -> 按钮=开始模拟上报")

    # 开始全部 -> 按钮应为「停止模拟上报」
    w._start_all()
    assert w._start_btn.text() == "停止模拟上报", \
        f"发送中按钮应为停止，实际={w._start_btn.text()}"
    assert w._sending is True
    print("    OK: 开始发送 -> 按钮=停止模拟上报")

    # 停止全部 -> 按钮复位为「开始模拟上报」
    w._stop_all()
    assert w._start_btn.text() == "开始模拟上报", \
        f"停止后按钮应为开始，实际={w._start_btn.text()}"
    assert w._sending is False
    print("    OK: 停止发送 -> 按钮=开始模拟上报")

    # 单独启动一个报文组 -> 按钮=停止；停止该组 -> 按钮=开始
    w._start_group(0x100)
    assert w._start_btn.text() == "停止模拟上报"
    w._stop_group(0x100)
    assert w._start_btn.text() == "开始模拟上报"
    print("    OK: 单组启动/停止也正确刷新按钮")


if __name__ == "__main__":
    test_button_reflects_state()
    print("ALL SIM BUTTON TESTS PASSED")
