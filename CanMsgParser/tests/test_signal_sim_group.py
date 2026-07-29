# tests/test_signal_sim_group.py
"""模拟上报页「按报文 ID 分组 + 整帧聚合」重构的 offscreen 测试。

背景（用户报障）：旧实现对每个信号单独发一帧，导致同一 CAN ID 的多个信号
互相用 offset 默认值覆盖、取值来回切换（如 0x3E3 的 A/B 信号交替被清零）。
重构后：同 ID 信号聚合为一个「报文组」，组内所有信号填入【同一帧】一次性
编码发送，上报周期只由组控制。本测试验证该聚合行为正确、无覆盖。

无需硬件：使用 python-can 的 virtual 接口 + cantools 内存库。
"""
import os
import sys
import tempfile

# 自包含：把项目根目录加入 sys.path，确保 `widgets`/`core` 等顶层包可导入
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)

import cantools  # noqa: E402

from widgets.signal_sim_widget import SignalSimWidget  # noqa: E402
from core.can_data import MessageDef, SignalDef  # noqa: E402
from core.can_utils import load_dbc  # noqa: E402
from core.can_connection import CanConnectionManager  # noqa: E402

# 0x3E3 = 995, 0x3E4 = 996；SigA/SigB 同属 0x3E3，SigC 属 0x3E4
GROUP_DBC = """VERSION ""

NS_ :

BS_:

BU_:

BO_ 995 Msg3E3: 8 Vector__XXX
 SG_ SigA : 0|8@1+ (1,0) [0|255] "" Vector__XXX
 SG_ SigB : 8|8@1+ (1,0) [0|255] "" Vector__XXX

BO_ 996 Msg3E4: 8 Vector__XXX
 SG_ SigC : 0|8@1+ (1,0) [0|255] "" Vector__XXX
"""


def make_group_dbc():
    fd, path = tempfile.mkstemp(suffix=".dbc")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(GROUP_DBC)
    return path


def build_message_defs(db):
    """从 cantools database 构造 MessageDef/SignalDef 列表（供 set_messages）。"""
    msgs = []
    for m in db.messages:
        sigs = []
        for s in m.signals:
            sigs.append(SignalDef(
                name=s.name,
                start_bit=s.start,
                length=s.length,
                byte_order="intel" if s.byte_order == "little_endian" else "motorola",
                scale=s.scale,
                offset=s.offset,
                unit=s.unit or "",
                min_val=s.minimum if s.minimum is not None else 0.0,
                max_val=s.maximum if s.maximum is not None else 100.0,
                choices=dict(s.choices) if s.choices else {},
            ))
        msgs.append(MessageDef(
            frame_id=m.frame_id, name=m.name, dlc=m.length,
            is_fd=False, signals=sigs,
        ))
    return msgs


def test_grouping_and_aggregation():
    print("[1] 报文分组 + 整帧聚合 ...")
    dbc_path = make_group_dbc()
    try:
        db, err = load_dbc(dbc_path)
        assert db is not None, f"DBC 加载失败: {err}"
        msgs = build_message_defs(db)

        w = SignalSimWidget()
        w.set_messages(msgs)
        w.set_dbc_path(dbc_path)

        # 管理器 + 虚拟总线，用来接收 dispatch 的聚合帧
        mgr = CanConnectionManager()
        bus, berr = mgr.connect("virtual", "vgrp", 500000)
        assert bus is not None, berr
        captured = []
        mgr.add_listener(lambda m: captured.append(m))
        w.set_connection_manager(mgr)
        w._bus = bus  # 直连总线（已就绪）
        # _ensure_bus 的连接逻辑由 test_shared_connection 覆盖，此处直接复用
        # 已就绪的总线，避免在 offscreen 下触发 QMessageBox 段错误。
        w._ensure_bus = lambda: True  # noqa: E501

        # 添加：同 ID 两个信号 + 另一 ID 一个信号
        w.add_selected_signals([
            ("Msg3E3", "SigA"), ("Msg3E3", "SigB"), ("Msg3E4", "SigC"),
        ])
        QApplication.instance().processEvents()  # flush singleShot(0) 行创建

        # —— 分组断言 ——
        assert len(w._groups) == 2, f"应聚合为 2 个报文组, got={len(w._groups)}"
        g3e3 = w._groups.get(0x3E3)
        assert g3e3 is not None, "0x3E3 报文组应存在"
        assert set(g3e3["keys"]) == {("Msg3E3", "SigA"), ("Msg3E3", "SigB")}, \
            f"0x3E3 应有 2 个信号, got={g3e3['keys']}"
        g3e4 = w._groups.get(0x3E4)
        assert set(g3e4["keys"]) == {("Msg3E4", "SigC")}, \
            f"0x3E4 应有 1 个信号, got={g3e4['keys']}"
        print("    OK: 同 ID 信号归入同组(0x3E3:2)，不同 ID 独立成组(0x3E4:1)")

        # —— 整帧聚合发送：SigA=1, SigB=5 同时出现，互不覆盖 ——
        fixed = {("Msg3E3", "SigA"): 1, ("Msg3E3", "SigB"): 5}
        orig = w._resolve_raw

        def fake_resolve(key):
            if key in fixed:
                return True, fixed[key], ""
            return orig(key)

        w._resolve_raw = fake_resolve
        captured.clear()
        w._send_frame(0x3E3, g3e3["keys"])
        assert len(captured) == 1, f"应 dispatch 1 帧, got={len(captured)}"
        frame = captured[0]
        assert frame.arbitration_id == 0x3E3
        dec = db.decode_message(0x3E3, frame.data)
        assert dec["SigA"] == 1 and dec["SigB"] == 5, \
            f"聚合帧应同时含 SigA=1,SigB=5, got={dec}"
        print(f"    OK: 整帧聚合发送成功，解码 SigA={dec['SigA']} SigB={dec['SigB']}"
              f"（无互相覆盖/交替切换）")

        # —— 组周期控制 ——
        print("[2] 组周期控制 ...")
        g3e3["cycle_spin"].setValue(250)
        w._on_group_cycle_changed(0x3E3)
        assert g3e3["cycle"] == 250, f"组周期应更新为 250, got={g3e3['cycle']}"
        print("    OK: 组周期 SpinBox 改变即时同步到组 cycle（运行中生效）")

        # —— 移除信号 / 组空自动删除 ——
        print("[3] 移除信号 / 组空自动删除 ...")
        w._remove_one_signal(("Msg3E3", "SigA"))
        assert ("Msg3E3", "SigA") not in g3e3["keys"]
        assert ("Msg3E3", "SigB") in g3e3["keys"], "组未空不应删除"
        print("    OK: 移除一个信号后组仍在（剩 1 个信号）")
        w._remove_one_signal(("Msg3E3", "SigB"))
        assert 0x3E3 not in w._groups, "组内信号清空后报文组应自动删除"
        assert len(w._groups) == 1 and 0x3E4 in w._groups
        print("    OK: 组内最后一个信号移除后报文组自动消失")

        # —— 报文组发送/停止（整帧周期聚合）——
        print("[4] 报文组发送/停止（整帧周期聚合）...")
        g3e4 = w._groups[0x3E4]
        w._resolve_raw = lambda k: (True, 7, "")  # SigC=7
        w._start_group(0x3E4)
        assert g3e4["sending"] is True, "组应进入发送态"
        assert g3e4["timer"] is not None, "组应创建定时器"
        captured.clear()
        w._tick_group(0x3E4)
        assert len(captured) == 1 and captured[0].arbitration_id == 0x3E4
        dec = db.decode_message(0x3E4, captured[0].data)
        assert dec["SigC"] == 7, f"组 tick 应整帧发送 SigC=7, got={dec}"
        print("    OK: 组 tick 整帧聚合发送（SigC=7）")
        w._stop_group(0x3E4)
        assert g3e4["sending"] is False
        assert w._sending is False, "停止最后一组后应复位全局状态"
        print("    OK: 停止组后复位全局状态")
        w.close()
    finally:
        os.unlink(dbc_path)
    print("[1-4] 通过\n")


if __name__ == "__main__":
    test_grouping_and_aggregation()
    print("SIGNAL SIM GROUP TESTS PASSED")
