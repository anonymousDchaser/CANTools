# tests/test_signal_sim_addsends.py
"""Bug3 回归：模拟上报页 —— 同报文组已在发送时加入新信号，新信号状态应显示「发送中」。

复现场景（用户报障）：先添加 0x3CC 的 A 信号并开始模拟上报，A 显示「发送中」；
此时再添加同属 0x3CC 的 B 信号，B 状态却错误地显示「停止」。
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication

APP = QApplication.instance() or QApplication([])

from widgets.signal_sim_widget import SignalSimWidget, COL_STATUS
from core.can_data import MessageDef, SignalDef
from core.can_utils import load_dbc

# 0x3CC = 972，A/B 同属该报文
SIM_DBC = """VERSION ""

NS_ :

BS_:

BU_:

BO_ 972 Msg3CC: 8 Vector__XXX
 SG_ A : 0|8@1+ (1,0) [0|255] "" Vector__XXX
 SG_ B : 8|8@1+ (1,0) [0|255] "" Vector__XXX
"""


def make_sim_dbc():
    fd, path = tempfile.mkstemp(suffix=".dbc")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(SIM_DBC)
    return path


def _build_message_defs(db):
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
        msgs.append(MessageDef(
            frame_id=m.frame_id, name=m.name, dlc=m.length, is_fd=False, signals=sigs))
    return msgs


def test_add_signal_to_sending_group_shows_sending():
    print("[1] 同报文组发送中新增信号状态为『发送中』...")
    dbc_path = make_sim_dbc()
    try:
        db, err = load_dbc(dbc_path)
        assert db is not None, f"DBC 加载失败: {err}"
        msgs = _build_message_defs(db)

        w = SignalSimWidget()
        w.set_messages(msgs)
        w.set_dbc_path(dbc_path)
        # 假总线，避免真实硬件/弹窗
        w._bus = object()
        w._ensure_bus = lambda: True

        # 添加 A 并加入 0x3CC 报文组
        w.add_selected_signals([("Msg3CC", "A")])
        QApplication.instance().processEvents()

        # 开始该报文组发送
        w._start_group(0x3CC)
        g = w._groups[0x3CC]
        assert g["sending"] is True, "0x3CC 应进入发送态"
        a_rd = w._row_data[("Msg3CC", "A")]
        assert a_rd["status_item"].text(COL_STATUS) == "发送中", \
            f"A 应显示发送中, got={a_rd['status_item'].text(COL_STATUS)}"

        # 监控进行中加入同属 0x3CC 的 B 信号
        w.add_selected_signals([("Msg3CC", "B")])
        QApplication.instance().processEvents()

        b_rd = w._row_data[("Msg3CC", "B")]
        assert b_rd["status_item"].text(COL_STATUS) == "发送中", \
            (f"同组发送中新增信号应显示发送中, got="
             f"{b_rd['status_item'].text(COL_STATUS)}")
        # 确认 B 确实已被纳入组并随整帧发送（tick 包含 B）
        assert ("Msg3CC", "B") in g["keys"], "B 应加入 0x3CC 报文组"
        # 反向验证：未发送状态的新信号不应被错误标为发送中
        w._stop_group(0x3CC)
        assert b_rd["status_item"].text(COL_STATUS) == "停止", \
            "停止后 B 应显示停止"
        print("    OK: 发送中新增同组信号显示『发送中』；停止后正确回退为『停止』")
        w.close()
    finally:
        os.unlink(dbc_path)
    print("[1] 通过\n")


if __name__ == "__main__":
    test_add_signal_to_sending_group_shows_sending()
    print("SIGNAL SIM ADDSENDS TESTS PASSED")
