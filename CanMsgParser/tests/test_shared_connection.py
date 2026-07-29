# tests/test_shared_connection.py
"""共享 CAN 连接管理器的集成测试（虚拟总线，无需硬件）。

覆盖：
1. 共享总线：connect 后总线唯一；ensure_connected 幂等复用同一对象。
2. 配置不一致且已连接时，再次 connect 被拒绝（避免踢掉正在用的设备）。
3. fan-out：唯一收帧线程把同一帧分发给所有监听者（监控页+报文页都收到）。
4. 监听者全部移除后收帧线程自动停止；disconnect 关闭共享总线。
5. CanCaptureWorker.process_message 解码（含枚举 NamedSignalValue 取 .value）。
6. CanRawCaptureWorker.process_message 广播原始帧。
"""
import sys
import time
import tempfile
import os

from PyQt5.QtCore import QCoreApplication

import can
from core.can_connection import CanConnectionManager
from workers.can_capture_worker import CanCaptureWorker
from workers.can_raw_capture_worker import CanRawCaptureWorker


DBC_TEXT = """VERSION ""

NS_ :
	NS_DESC_
	CM_
	BA_DEF_
	BA_
	VAL_
	CAT_DEF_
	CAT_
	FILTER
	BA_DEF_DEF_
	EV_DATA_
	ENVVAR_DATA_
	SGTYPE
	SGTYPE_VAL_
	BA_DEF_SGTYPE_
	BA_SGTYPE_
	SIG_TYPE_REF_
	VAL_TABLE_
	SIG_GROUP_
	SIG_VALTYPE_
	BO_TX_BU_
	BA_DEF_REL_
	BA_REL_
	BA_DEF_DEF_REL_
	BU_SG_REL_
	BU_EV_REL_
	BO_REL_
	BA_DEF_DEF_REL_
	ST_VAL_

BS_:

BU_:

BO_ 256 Msg1: 1 Vector__XXX
 SG_ ADU_TestSig : 0|8@1+ (1,0) [0|255] "" Vector__XXX

VAL_ 256 ADU_TestSig 0 "Not Active" 1 "ON" 2 "OFF" 3 "Reserved" ;
"""


def make_dbc():
    fd, path = tempfile.mkstemp(suffix=".dbc")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(DBC_TEXT)
    return path


def test_manager_shared_and_fanout():
    print("[1] 共享总线 + fan-out 测试 ...")
    mgr = CanConnectionManager()
    bus, err = mgr.connect("virtual", "virtual", 500000)
    assert bus is not None, f"connect 失败: {err}"
    assert mgr.is_connected

    got1, got2 = [], []
    mgr.add_listener(lambda m: got1.append(m))
    mgr.add_listener(lambda m: got2.append(m))

    # 用第二条同通道虚拟总线发送，共享总线应能收到（虚拟路由）
    tx = can.Bus(interface="virtual", channel="virtual")
    tx.send(can.Message(arbitration_id=0x100, data=b"\x01\x02", is_extended_id=False))
    time.sleep(0.3)

    assert len(got1) >= 1, f"监听者1 未收到帧 (got={len(got1)})"
    assert len(got2) >= 1, f"监听者2 未收到帧 (got={len(got2)})"
    # 两监听者收到的是同一帧（fan-out，非瓜分）
    assert got1[0].arbitration_id == got2[0].arbitration_id == 0x100
    print(f"    OK: 两监听者各收到 {len(got1)}/{len(got2)} 帧，均为 0x100（fan-out 正确）")

    # 幂等：ensure_connected 返回同一总线对象
    bus2, _ = mgr.ensure_connected("virtual", "virtual", 500000)
    assert bus2 is bus, "ensure_connected 应复用同一总线"
    print("    OK: ensure_connected 幂等复用同一总线（自动连接语义）")

    # 配置不一致且已连接 -> 拒绝
    bad, berr = mgr.connect("peak", "PCAN_USBBUS1", 500000)
    assert bad is None and "不同配置" in berr, f"应拒绝配置不一致, got={berr}"
    print("    OK: 配置不一致时拒绝重复连接")

    # 移除全部监听者 -> 收帧线程停止
    mgr.remove_listener(mgr._listeners[0])
    mgr.remove_listener(mgr._listeners[0])
    assert mgr._dispatcher is None, "监听者清空后收帧线程应已停止"
    print("    OK: 监听者清空后收帧线程自动停止")

    # 断开
    mgr.disconnect()
    assert not mgr.is_connected and mgr.get_bus() is None
    print("    OK: disconnect 关闭共享总线")
    print("[1] 通过\n")


def test_capture_worker_decode():
    print("[2] CanCaptureWorker 解码测试（含枚举 NamedSignalValue）...")
    dbc = make_dbc()
    samples = []
    w = CanCaptureWorker(dbc, [("Msg1", "ADU_TestSig")])
    w.sample_received.connect(lambda fid, m, s, t, v: samples.append((m, s, v)))
    ok = w.start_monitoring()
    assert ok, "start_monitoring 应成功（DBC 可加载）"

    # 发送枚举值 2 (OFF)
    w.process_message(can.Message(arbitration_id=0x100, data=b"\x02", is_extended_id=False))
    assert len(samples) == 1, f"应解码出 1 个采样, got={len(samples)}"
    m, s, v = samples[0]
    assert m == "Msg1" and s == "ADU_TestSig"
    assert v == 2.0, f"枚举值应取 .value=2, got={v}"
    print(f"    OK: 解码 Msg1.ADU_TestSig = {v}（NamedSignalValue.value 正确）")
    print("[2] 通过\n")


def test_raw_worker():
    print("[3] CanRawCaptureWorker 广播测试 ...")
    frames = []
    w = CanRawCaptureWorker("virtual", "virtual", 500000)
    w.frame_received.connect(lambda rel, cid, dlc, data, ext, fd: frames.append((cid, dlc, data)))
    w.start_monitoring()
    w.process_message(can.Message(arbitration_id=0x200, data=b"\xAA\xBB",
                                   is_extended_id=False, dlc=2))
    assert len(frames) == 1, f"应广播 1 帧, got={len(frames)}"
    cid, dlc, data = frames[0]
    assert cid == 0x200 and dlc == 2 and data == b"\xAA\xBB"
    print(f"    OK: 广播帧 id=0x{cid:X} dlc={dlc} data={data.hex()}")
    print("[3] 通过\n")


def test_dispatch_sim_frame_to_monitor():
    print("[4] 模拟页自发帧(dispatch)到达监控 worker + 外部下发帧经 dispatcher ...")
    dbc = make_dbc()
    samples = []
    w = CanCaptureWorker(dbc, [("Msg1", "ADU_TestSig")])
    w.sample_received.connect(lambda fid, m, s, t, v: samples.append((m, s, v)))

    mgr = CanConnectionManager()
    bus, err = mgr.connect("virtual", "vch_disp", 500000)
    assert bus is not None, err
    assert w.start_monitoring()
    mgr.add_listener(w.process_message)

    # (a) 外部下发帧：由唯一收帧线程(dispatcher)读出并 fan-out 到监控 worker
    tx = can.Bus(interface="virtual", channel="vch_disp")
    tx.send(can.Message(arbitration_id=0x100, data=b"\x02", is_extended_id=False))
    time.sleep(0.3)
    QCoreApplication.instance().processEvents()
    n_ext = len(samples)
    assert n_ext >= 1, "外部下发帧应经 dispatcher 到达监控 worker"
    print(f"    OK: 外部下发帧经 dispatcher 到达监控 worker（{n_ext} 个采样）")

    # (b) 模拟上报自发帧：硬件不回环，必须由 manager.dispatch 主动 fan-out
    sim_frame = can.Message(arbitration_id=0x100, data=b"\x03", is_extended_id=False)
    mgr.dispatch(sim_frame)  # 等同模拟页 _send_frame 中调用
    QCoreApplication.instance().processEvents()
    assert len(samples) == n_ext + 1, (
        f"dispatch 的模拟帧应额外到达监控 worker, 外部={n_ext} 总={len(samples)}"
    )
    m, s, v = samples[-1]
    assert (m, s, v) == ("Msg1", "ADU_TestSig", 3.0), f"模拟帧应解码=3, got={(m, s, v)}"
    print(f"    OK: 模拟上报自发帧经 dispatch 到达监控 worker（值={v}）")
    print("[4] 通过\n")


if __name__ == "__main__":
    app = QCoreApplication(sys.argv)
    test_manager_shared_and_fanout()
    test_capture_worker_decode()
    test_raw_worker()
    test_dispatch_sim_frame_to_monitor()
    print("ALL TESTS PASSED")
