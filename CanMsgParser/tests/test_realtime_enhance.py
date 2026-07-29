# tests/test_realtime_enhance.py
"""实时报文页 / 监控页增强功能测试（offscreen，无 matplotlib 渲染）"""
import sys
import os
import tempfile

import cantools
from PyQt5.QtWidgets import QApplication

from core.can_data import MessageDef
from widgets.realtime_message_widget import RealtimeMessageWidget
from widgets.realtime_monitor_widget import RealtimeMonitorWidget

# 全局单一 QApplication：同一进程多次实例化 QApplication 会在退出时段错误
APP = QApplication([])


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
	BA_DEF_DEF_REL_
	BA_REL_
	BA_DEF_DEF_REL_
	BU_EV_REL_
	BO_REL_
	BA_DEF_DEF_REL_
	ST_VAL_

BS_:

BU_:

BO_ 274 TCU_3: 8 Vector__XXX
 SG_ TCU_Drivemode : 0|8@1+ (1,0) [0|0] "" Vector__XXX
 SG_ Speed : 8|16@1+ (0.1,0) [0|0] "km/h" Vector__XXX

VAL_ 274 TCU_Drivemode 0 "停车" 1 "前进" 2 "倒退" ;
"""


def make_dbc():
    fd, path = tempfile.mkstemp(suffix=".dbc")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(DBC_TEXT)
    return path


def test_message_tree_hex_and_desc():
    print("[A] 实时报文页：16进制值(左) + 信号描述列 ...")
    db = cantools.database.load_string(DBC_TEXT)

    w = RealtimeMessageWidget()
    # 直接注入 db（避免文件加载路径），并构造一帧：
    # TCU_Drivemode=1 (前进), Speed raw=100 -> 10.0 km/h
    w._db = db
    w._msg_names = {0x112: "TCU_3"}

    data = bytes([1, 100, 0, 0, 0, 0, 0, 0])
    w._on_frame(0.0, 0x112, 8, data, False, False)
    w._ensure_children(0x112)
    children = w._child_items[0x112]
    assert children, "应解析出信号子项"
    assert "TCU_Drivemode" in children and "Speed" in children

    dm = children["TCU_Drivemode"]
    sp = children["Speed"]
    # 十六进制原始值列（左，col1）
    assert dm.text(1) == "0x1", \
        f"TCU_Drivemode 十六进制列(左)应为 '0x1', got={dm.text(1)!r}"
    # 十进制值列（右，col2，含枚举名）
    assert dm.text(2) == "1 (前进)", \
        f"TCU_Drivemode 十进制列(右)应为 '1 (前进)', got={dm.text(2)!r}"
    # 信号描述列（DBC choices 优先）
    assert dm.text(4) == "前进", \
        f"TCU_Drivemode 描述列应为 '前进', got={dm.text(4)!r}"
    # Speed 十六进制(左) + 十进制(右)
    assert sp.text(1) == "0x64", \
        f"Speed 十六进制列(左)应为 '0x64', got={sp.text(1)!r}"
    assert sp.text(2) == "10.0", \
        f"Speed 十进制列(右)应为 '10.0', got={sp.text(2)!r}"
    assert sp.text(4) == "", \
        f"Speed 无描述应为空, got={sp.text(4)!r}"
    print("    OK: 十六进制(左)/十进制(右)/描述列均正确（DBC choices 优先）")

    # Excel 兜底：DBC 无对应描述时（用 Excel 描述）应显示 Excel 描述
    w.set_value_descriptions({"TCU_Drivemode": {1: "Excel前进"}})
    del w._child_items[0x112]
    w._ensure_children(0x112)
    dm2 = w._child_items[0x112]["TCU_Drivemode"]
    assert dm2.text(4) == "Excel前进", \
        f"Excel 兜底应显示 'Excel前进', got={dm2.text(4)!r}"
    print("    OK: Excel 表描述兜底生效")

    # 纯函数鲁棒性
    assert RealtimeMessageWidget._raw_to_hex(255) == "0xFF"
    assert RealtimeMessageWidget._raw_to_hex(None) == ""
    assert RealtimeMessageWidget._raw_to_hex("bad") == ""
    print("    OK: _raw_to_hex 鲁棒")
    print("[A] 通过\n")


def test_realtime_expand_placeholder():
    print("[B] 实时报文页：占位箭头 + 双击展开解码 ...")
    db = cantools.database.load_string(DBC_TEXT)
    w = RealtimeMessageWidget()
    w._db = db
    w._msg_names = {0x112: "TCU_3"}
    data = bytes([1, 100, 0, 0, 0, 0, 0, 0])
    # 收到一帧后，顶层行应带占位子项（否则 QTreeWidget 不显示展开箭头）
    w._on_frame(0.0, 0x112, 8, data, False, False)
    top = w._rows[0x112]
    assert top.childCount() == 1, "应存在占位子项（展开箭头）"
    assert "展开" in top.child(0).text(1), "占位提示应含'展开'"

    # 模拟用户双击展开（itemExpanded 信号回调）
    w._on_item_expanded(top)
    children = w._child_items[0x112]
    assert children, "展开后应解析出信号子项"
    assert top.childCount() >= 1 and "展开" not in top.child(0).text(1), \
        "占位提示应被真实信号子项替换"
    dm = children["TCU_Drivemode"]
    assert dm.text(0) == "TCU_Drivemode"
    assert dm.text(1) == "0x1", f"十六进制应在左列, got={dm.text(1)!r}"
    assert dm.text(2) == "1 (前进)", f"十进制应在右列, got={dm.text(2)!r}"
    assert dm.text(4) == "前进"
    print("    OK: 占位箭头 + 双击展开 + 十六进制(左)/十进制(右) 均正确")
    print("[B] 通过\n")


def test_realtime_pause_state():
    print("[C] 实时报文页：暂停/开始状态机 ...")
    w = RealtimeMessageWidget()

    # 用桩管理器替代真实硬件连接
    class StubMgr:
        def __init__(self):
            self._cfg = {"interface_type": "pcan",
                         "channel": "PCAN_USBBUS1", "bitrate": 500000}

        def get_config(self):
            return dict(self._cfg)

        def ensure_connected(self, *a, **k):
            return (object(), None)  # 假总线

        def add_listener(self, cb):
            return object()

        def remove_listener(self, cb):
            pass

    w._manager = StubMgr()
    # 桩掉 start/stop_capture，避免真实建线程/开硬件
    w.start_capture = lambda *a, **k: (setattr(w, "_capturing", True),
                                        w._update_pause_button())
    w.stop_capture = lambda: (setattr(w, "_capturing", False),
                               w._update_pause_button())

    # 初始：未监听
    assert w._capturing is False
    assert w._pause_btn.text() == "▶ 开始监听"
    # 连接设备 -> 自动监听
    w._on_conn_state_changed(True, "pcan")
    assert w._capturing is True, "连接后应自动监听"
    assert w._pause_btn.text() == "⏸ 暂停监听"
    # 手动暂停
    w._on_toggle_listen()
    assert w._capturing is False and w._user_paused is True
    assert w._pause_btn.text() == "▶ 开始监听"
    # 设备掉线后重连，应保持暂停（不自动恢复）
    w._on_conn_state_changed(False, "")
    w._on_conn_state_changed(True, "pcan")
    assert w._capturing is False, "用户暂停态重连不应自动恢复"
    assert w._pause_btn.text() == "▶ 开始监听"
    # 手动开始
    w._on_toggle_listen()
    assert w._capturing is True and w._user_paused is False
    assert w._pause_btn.text() == "⏸ 暂停监听"
    print("    OK: 暂停/开始状态机、按钮文案、重连不自动恢复 均正确")
    print("[C] 通过\n")


def test_monitor_frame_id_lookup():
    print("[D] 监控页 _frame_id_of 反查报文 ID ...")
    # 绕过 __init__（避免 PlotWidget 实例化触发 matplotlib），仅测纯逻辑
    w = RealtimeMonitorWidget.__new__(RealtimeMonitorWidget)
    w._messages = [
        MessageDef(frame_id=0x112, name="TCU_3", dlc=8, is_fd=False, signals=[]),
        MessageDef(frame_id=0x200, name="BCM_1", dlc=8, is_fd=False, signals=[]),
    ]
    assert w._frame_id_of("TCU_3") == 0x112
    assert w._frame_id_of("BCM_1") == 0x200
    assert w._frame_id_of("NotExist") is None
    print("    OK: _frame_id_of 正确反查报文 ID（用于图例 0x112）")
    print("[D] 通过\n")


if __name__ == "__main__":
    test_message_tree_hex_and_desc()
    test_realtime_expand_placeholder()
    test_realtime_pause_state()
    test_monitor_frame_id_lookup()
    print("ALL TESTS PASSED")
