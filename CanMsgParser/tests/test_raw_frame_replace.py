# tests/test_raw_frame_replace.py
"""三项 UI 需求的回归测试（offscreen）：

1) 「信号分组」→ 勾选「替换」后分发，改为用【整组信号】替换目标页；
2) 信号加入「模拟上报」页后，所属报文组默认展开；
3) 「模拟上报」页支持手工原始报文组（自定义 ID + 逐字节原始数据，
   长度优先按 DBC 识别，识别不到默认 8 字节并可手动改 8/64 等合法长度）。

无需硬件：使用 python-can 的 virtual 接口 + 内存 DBC。
"""
import os
import sys
import tempfile

# 自包含：把项目根目录加入 sys.path，确保 `widgets`/`core` 等顶层包可导入
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402
from PyQt5.QtCore import Qt  # noqa: E402

_APP = QApplication.instance() or QApplication(sys.argv)

from core.can_data import MessageDef, SignalDef  # noqa: E402
from core.can_utils import load_dbc  # noqa: E402
from core.can_connection import CanConnectionManager  # noqa: E402
from widgets.signal_sim_widget import (  # noqa: E402
    SignalSimWidget, COL_STATUS, COL_DETAIL, RAW_DLC_CHOICES, LEGAL_DLC,
    MAX_RAW_BYTES, CAN_FD_MAX_BYTES, _parse_hex_bytes,
)
from widgets.signal_group_panel import SignalGroupPanel  # noqa: E402

DBC_TEXT = """VERSION ""

NS_ :

BS_:

BU_:

BO_ 995 Msg3E3: 8 Vector__XXX
 SG_ SigA : 0|8@1+ (1,0) [0|255] "" Vector__XXX
 SG_ SigB : 8|8@1+ (1,0) [0|255] "" Vector__XXX
"""


def make_dbc():
    fd, path = tempfile.mkstemp(suffix=".dbc")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(DBC_TEXT)
    return path


def build_message_defs(db):
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
            frame_id=m.frame_id, name=m.name, dlc=m.length,
            is_fd=False, signals=sigs))
    return msgs


def _check_hex_parse():
    print("[0] 十六进制输入解析 ...")
    assert _parse_hex_bytes("00 11 22") == b"\x00\x11\x22"
    assert _parse_hex_bytes("001122") == b"\x00\x11\x22"
    assert _parse_hex_bytes("0x00,0x11") == b"\x00\x11"
    assert _parse_hex_bytes("") == b""
    assert _parse_hex_bytes("0 11") is None      # 奇数位
    assert _parse_hex_bytes("zz") is None        # 非十六进制
    print("    OK: 空格/连续/0x 前缀均可解析，非法输入返回 None")


def _check_group_auto_expand(dbc_path, msgs):
    print("[1] 信号加入后报文组默认展开 ...")
    w = SignalSimWidget()
    w.set_messages(msgs)
    w.set_dbc_path(dbc_path)
    w.add_selected_signals([("Msg3E3", "SigA"), ("Msg3E3", "SigB")])
    QApplication.instance().processEvents()
    grp = w._groups.get(0x3E3)
    assert grp is not None, "0x3E3 报文组应已创建"
    assert grp["item"].isExpanded(), "加入信号后报文组应默认展开"
    # 再补一条同组信号，仍保持展开
    w.add_selected_signals([("Msg3E3", "SigA")])
    QApplication.instance().processEvents()
    assert grp["item"].isExpanded(), "重复加入同组信号后组应保持展开"
    w.close()
    print("    OK: 报文组默认展开")


def _check_replace_dispatch(dbc_path, msgs):
    print("[2] 「替换」分发整组信号 ...")
    p = SignalGroupPanel()
    p.set_messages(msgs)
    p.add_signals([("Msg3E3", "SigA"), ("Msg3E3", "SigB")])

    got = []
    p.dispatch_requested.connect(lambda t, s: got.append(("append", t, s)))
    p.replace_dispatch_requested.connect(
        lambda t, s: got.append(("replace", t, s))
    )

    # 默认不勾选：只分发已勾选信号（此处勾上 SigA）
    assert p._replace_chk.isChecked() is False, "「替换」复选框应默认不勾选"
    p._sig_list.topLevelItem(0).setCheckState(0, Qt.Checked)
    p._on_group_dispatch("curve")
    assert got[-1][0] == "append" and got[-1][2] == [("Msg3E3", "SigA")], \
        f"未勾选「替换」时应只分发已勾选信号, got={got[-1]}"

    # 勾选「替换」：整组信号（忽略勾选状态）
    p._replace_chk.setChecked(True)
    p._on_group_dispatch("sim")
    kind, target, signals = got[-1]
    assert kind == "replace" and target == "sim", f"应走替换分发, got={got[-1]}"
    assert signals == [("Msg3E3", "SigA"), ("Msg3E3", "SigB")], \
        f"替换应填入整组信号, got={signals}"
    print("    OK: 未勾选取勾选信号追加；勾选取整组信号替换")


def _check_raw_frame(dbc_path, msgs):
    print("[3] 手工原始报文组（识别长度 / 编辑 / 发送）...")
    w = SignalSimWidget()
    w.set_messages(msgs)
    w.set_dbc_path(dbc_path)

    # 3.1 DBC 中不存在的 ID：长度回落 8 字节
    w._create_raw_group(0x112)
    grp = w._raw_groups.get(0x112)
    assert grp is not None, "0x112 原始报文组应已创建"
    assert grp["dlc"] == 8 and grp["data"] == bytes(8), \
        f"无法识别长度时应默认 8 字节 0 数据, got={grp['dlc']}/{grp['data']!r}"
    assert grp["status_item"].text(COL_STATUS) == "停止"

    # 3.1b 原始数据编辑框在组内「数据」子行，且该行首列跨列（占满整行）
    assert grp["item"].isExpanded(), "新建原始报文组应默认展开数据子行"
    assert grp["data_item"].isFirstColumnSpanned(), "数据子行应跨列显示"
    assert grp["data_item"].parent() is grp["item"], "数据子行应挂在组行之下"
    assert w._value_table.itemWidget(grp["data_item"], 0) is not None, \
        "数据子行首列应挂载编辑控件"
    assert w._value_table.itemWidget(grp["item"], COL_DETAIL) is None, \
        "详情列不应再放原始数据编辑框"
    assert grp["data_edit"].text() == " ".join(["00"] * 8), \
        f"编辑框应显示 8 字节 0 数据, got={grp['data_edit'].text()!r}"

    # 3.2 DBC 中存在的 ID：长度自动识别为 8 且带出报文名
    w._create_raw_group(0x3E3)
    g2 = w._raw_groups[0x3E3]
    assert g2["dlc"] == 8 and g2["msg_name"] == "Msg3E3", \
        f"应按 DBC 识别长度与报文名, got={g2['dlc']}/{g2['msg_name']}"

    # 3.3 手动改为 64 字节（CAN FD），数据自动补零
    combo = grp["dlc_combo"]
    combo.setCurrentIndex(combo.findData(64))
    QApplication.instance().processEvents()
    assert grp["dlc"] == 64 and len(grp["data"]) == 64, \
        f"长度下拉改 64 后数据应补零到 64 字节, got={len(grp['data'])}"

    # 3.4 直接粘贴 8 字节数据：长度合法 -> 自动切回 8 字节
    grp["data_edit"].setText("11 22 33 44 55 66 77 88")
    assert w._on_raw_data_edited(0x112) is True
    assert grp["dlc"] == 8 and grp["data"] == bytes.fromhex("1122334455667788"), \
        f"输入合法长度应自动同步长度, got={grp['dlc']}/{grp['data']!r}"

    # 3.5 非法长度（9 字节）与非法字符：置错且不改数据
    grp["data_edit"].setText("00 01 02 03 04 05 06 07 08")
    assert w._on_raw_data_edited(0x112) is False, "9 字节不是合法 DLC，应判为非法"
    assert grp["status_item"].text(COL_STATUS) == "错误"
    assert grp["data"] == bytes.fromhex("1122334455667788"), "非法输入不应改写数据"
    grp["data_edit"].setText("zz zz")
    assert w._on_raw_data_edited(0x112) is False, "非法字符应判为非法"

    # 3.6 发送：原始字节原样上线（不依赖 DBC、不经编码）
    grp["data_edit"].setText("0A 0B 0C 0D 0E 0F 10 11")
    assert w._on_raw_data_edited(0x112) is True
    mgr = CanConnectionManager()
    bus, err = mgr.connect("virtual", "vraw", 500000)
    assert bus is not None, err
    captured = []
    mgr.add_listener(lambda m: captured.append(m))
    w.set_connection_manager(mgr)
    w._bus = bus
    w._ensure_bus = lambda: True
    # 先只拉起发送线程（尚无启用的组，不会周期发送），单独验证单帧内容
    w._ensure_worker()
    captured.clear()
    w._worker._send_raw(0x112, grp["data"])
    assert len(captured) == 1, f"应发出 1 帧, got={len(captured)}"
    frame = captured[0]
    assert frame.arbitration_id == 0x112
    assert bytes(frame.data) == bytes.fromhex("0A0B0C0D0E0F1011"), \
        f"应原样发出用户输入的字节, got={frame.data!r}"
    assert frame.is_fd is False, "8 字节应为经典 CAN 帧"

    # 3.7 组级发送状态机（周期发送由独立线程承担，不依赖主线程事件循环）
    w._start_raw_group(0x112)
    assert grp["sending"] is True and w._sending is True, "原始报文组应进入发送态"
    assert grp["status_item"].text(COL_STATUS) == "发送中"
    assert w._worker.isRunning(), "原始报文组发送应由独立发送线程承担"
    assert w._worker._raw_groups.get(0x112, {}).get("enabled") is True, \
        "发送线程应启用该原始报文组"
    w._stop_raw_group(0x112)
    assert grp["sending"] is False and w._sending is False, "停止后应复位全局状态"
    print("    OK: 长度识别/自动补零/长度同步/非法拦截/原样发送均正确")

    # 3.7b 128 字节档位：可编辑，但发送被明确拦下（CAN FD 上限 64）
    from PyQt5.QtWidgets import QMessageBox
    combo = grp["dlc_combo"]
    assert combo.findData(128) >= 0, "长度下拉应含 128 字节档位"
    combo.setCurrentIndex(combo.findData(128))
    assert grp["dlc"] == 128 and len(grp["data"]) == 128, \
        f"切到 128 档位后数据应补零到 128 字节, got={len(grp['data'])}"
    assert w._on_raw_data_edited(0x112) is True, "128 字节应允许解析（仅本地编辑）"
    assert grp["status_item"].text(COL_STATUS) == "超限", \
        f"128 字节状态应标为「超限」, got={grp['status_item'].text(COL_STATUS)}"
    warns, errs = [], []
    _orig_warn = QMessageBox.warning
    QMessageBox.warning = staticmethod(
        lambda *a, **k: warns.append(a[2] if len(a) > 2 else "")
    )
    w._worker.error.connect(lambda fid, msg: errs.append((fid, msg)))
    try:
        w._start_raw_group(0x112)
        assert grp["sending"] is False, "128 字节不应进入发送态"
        captured.clear()
        w._worker._send_raw(0x112, grp["data"])
    finally:
        QMessageBox.warning = _orig_warn
    assert warns and "64" in warns[-1], f"开始发送应提示超过上限, got={warns}"
    assert not captured, "128 字节不应真的上线"
    assert errs and "64" in errs[-1][1], f"发送线程应回报超限, got={errs}"
    combo.setCurrentIndex(combo.findData(8))   # 复位，避免影响后续用例
    assert w._on_raw_data_edited(0x112) is True
    assert grp["status_item"].text(COL_STATUS) == "停止", \
        "回到 8 字节后状态应恢复为「停止」"
    print("    OK: 128 档位可编辑但被拦在总线之外（状态标超限 + 提示 + 线程回报）")

    # 3.8 同一 ID 冲突：已有原始报文组时不再添加该 ID 的 DBC 信号
    shown = []
    _orig_info = QMessageBox.information
    QMessageBox.information = staticmethod(
        lambda *a, **k: shown.append(a[2] if len(a) > 2 else "")
    )
    try:
        w.add_selected_signals([("Msg3E3", "SigA")])
        QApplication.instance().processEvents()
    finally:
        QMessageBox.information = _orig_info
    assert ("Msg3E3", "SigA") not in w._sel_signals, \
        "已存在同 ID 原始报文组时不应再加入该 ID 的信号"
    assert shown and "0x3E3" in shown[-1], f"应提示冲突的 ID, got={shown}"
    print("    OK: 原始报文组与同 ID 信号组互斥（跳过并提示）")

    # 3.9 清空已选信号不应删掉手工原始报文
    w.clear_selected_signals()
    QApplication.instance().processEvents()
    assert not w._groups and not w._row_data, "清空后不应残留信号报文组"
    assert 0x112 in w._raw_groups and 0x3E3 in w._raw_groups, \
        "清空已选信号不应删除手工原始报文组"
    w._stop_all()
    w.close()
    print("    OK: 「清空已选信号」保留原始报文组")


def _check_dlc_choices():
    print("[4] 数据长度档位 ...")
    assert RAW_DLC_CHOICES == (8, 16, 32, 64, 128), \
        f"下拉档位应为 8/16/32/64/128, got={RAW_DLC_CHOICES}"
    assert CAN_FD_MAX_BYTES == 64 and MAX_RAW_BYTES == 128
    # 128 超出 CAN FD 上限，故不能算"合法可发送长度"，但允许本地编辑
    assert 128 not in LEGAL_DLC, "128 字节不属于 CAN / CAN FD 合法长度"
    for n in LEGAL_DLC:
        assert n <= CAN_FD_MAX_BYTES, f"合法长度清单混入超限值 {n}"
    print("    OK: 档位 8/16/32/64/128；128 仅本地编辑，发送侧被拦截")


def main():
    dbc_path = make_dbc()
    try:
        db, err = load_dbc(dbc_path)
        assert db is not None, f"DBC 加载失败: {err}"
        msgs = build_message_defs(db)
        _check_hex_parse()
        _check_dlc_choices()
        _check_group_auto_expand(dbc_path, msgs)
        _check_replace_dispatch(dbc_path, msgs)
        _check_raw_frame(dbc_path, msgs)
    finally:
        os.unlink(dbc_path)
    print("RAW FRAME / REPLACE TESTS PASSED")


def test_raw_frame_and_replace():
    """pytest 入口：整组跑一遍（临时 DBC 在 main 内自建自清）。"""
    main()


if __name__ == "__main__":
    main()
