# tests/test_signal_sim_negative.py
"""模拟上报「负数转无符号」报错修复（Issue 5）offscreen 测试。

场景：
- 无符号信号手动输入负值（如 -5）-> 旧实现直接把负数交给 cantools encode，
  抛 "can't convert negative int to unsigned"；修复后做范围钳制 + 友好报错。
- 同帧存在带「负 offset」的无符号信号（默认填充值会被钳制为下限），不再崩溃。
- 超出位宽上限的正值（如 300 喂 8 位）-> 钳制为上限（255）。

无需硬件：python-can virtual 接口 + cantools 内存库。
"""
import os
import sys
import tempfile

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)

from widgets.signal_sim_widget import SignalSimWidget
from core.can_data import MessageDef, SignalDef
from core.can_utils import load_dbc
from core.can_connection import CanConnectionManager

# ESP_1 含两个信号：BrakeAssistModeSts（手动喂负值测试对象）、Temp（无符号+负 offset 默认填充）
NEG_DBC = """VERSION ""

NS_ :

BS_:

BU_:

BO_ 418 ESP_1: 8 Vector__XXX
 SG_ BrakeAssistModeSts : 0|8@1+ (1,0) [0|255] "" Vector__XXX
 SG_ Temp : 8|8@1+ (1,-40) [-40|215] "" Vector__XXX
"""


def _make_dbc():
    fd, path = tempfile.mkstemp(suffix=".dbc")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(NEG_DBC)
    return path


def _build():
    dbc_path = _make_dbc()
    db, err = load_dbc(dbc_path)
    assert db is not None, err
    msgs = []
    for m in db.messages:
        sigs = [SignalDef(
            name=s.name, start_bit=s.start, length=s.length,
            byte_order="intel" if s.byte_order == "little_endian" else "motorola",
            scale=s.scale, offset=s.offset, unit=s.unit or "",
            min_val=s.minimum if s.minimum is not None else 0.0,
            max_val=s.maximum if s.maximum is not None else 100.0,
            choices=dict(s.choices) if s.choices else {},
        ) for s in m.signals]
        msgs.append(MessageDef(frame_id=m.frame_id, name=m.name, dlc=m.length,
                               is_fd=False, signals=sigs))
    w = SignalSimWidget()
    w.set_messages(msgs)
    w.set_dbc_path(dbc_path)
    mgr = CanConnectionManager()
    bus, berr = mgr.connect("virtual", "vgrp", 500000)
    assert bus is not None, berr
    captured = []
    mgr.add_listener(lambda m: captured.append(m))
    w.set_connection_manager(mgr)
    w._bus = bus
    w._ensure_bus = lambda: True  # 跳过真实连接逻辑（offscreen 下避免 QMessageBox）
    return w, db, captured, dbc_path


def test_negative_manual_clamped():
    print("[5] 无符号信号手动负值 -> 钳制不崩溃 ...")
    w, db, captured, dbc_path = _build()
    try:
        w.add_selected_signals([("ESP_1", "BrakeAssistModeSts")])
        QApplication.instance().processEvents()
        key = ("ESP_1", "BrakeAssistModeSts")
        rd = w._row_data[key]
        # 切到「手动模拟」并输入负值 -5
        rd["value_combo"].setCurrentIndex(rd["value_combo"].count() - 1)
        rd["manual_edit"].setText("-5")

        captured.clear()
        w._send_frame(0x1A2, w._groups[0x1A2]["keys"])
        assert len(captured) == 1, f"应成功编码并发送 1 帧, 实际={len(captured)}"
        frame = captured[0]
        dec = db.decode_message(0x1A2, frame.data)
        # 负值 -5 钳制为下限 0（BrakeAssistModeSts 无符号 8 位 -> raw 0 -> 物理 0）
        assert dec["BrakeAssistModeSts"] == 0, f"负值应钳制为0, got={dec['BrakeAssistModeSts']}"
        # Temp 为同帧带「负 offset(-40)」的无符号信号：其默认填充 raw 已被钳制为 0，
        # 故 cantools 能正常编码（否则会抛 "can't convert negative int to unsigned"）；
        # 物理解码值 -40 是 offset 的正常体现，关键证据是 encode 未崩溃且成功发出 1 帧。
        # 该行应被标记为错误并给出友好提示
        assert rd["status_item"].text(5) == "错误", "越界行应标记错误"
        det = rd["detail_item"].text(6)
        assert "范围" in det or "超出" in det, f"详情应提示越界, got={det}"
        print(f"    OK: 负值/负offset 均钳制发送，帧正常发出，行报错={det}")
        w.close()
    finally:
        os.unlink(dbc_path)


def test_positive_overflow_clamped():
    print("[5] 超出位宽上限的正值 -> 钳制为上限 ...")
    w, db, captured, dbc_path = _build()
    try:
        w.add_selected_signals([("ESP_1", "BrakeAssistModeSts")])
        QApplication.instance().processEvents()
        key = ("ESP_1", "BrakeAssistModeSts")
        rd = w._row_data[key]
        rd["value_combo"].setCurrentIndex(rd["value_combo"].count() - 1)
        rd["manual_edit"].setText("300")  # 8 位无符号上限 255

        captured.clear()
        w._send_frame(0x1A2, w._groups[0x1A2]["keys"])
        assert len(captured) == 1
        dec = db.decode_message(0x1A2, captured[0].data)
        assert dec["BrakeAssistModeSts"] == 255, f"应钳制为255, got={dec['BrakeAssistModeSts']}"
        print("    OK: 超上限正值钳制为 255，帧正常发送")
        w.close()
    finally:
        os.unlink(dbc_path)


if __name__ == "__main__":
    test_negative_manual_clamped()
    test_positive_overflow_clamped()
    print("SIGNAL SIM NEGATIVE TESTS PASSED")
