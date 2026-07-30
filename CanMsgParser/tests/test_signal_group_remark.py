# tests/test_signal_group_remark.py
"""信号分组面板「备注列 + 自动保存」（Issue 4）offscreen 测试。

无需硬件：虚拟 QApplication（offscreen）。
"""
import os
import sys
import json
import tempfile

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QLineEdit
from PyQt5.QtCore import Qt

_app = QApplication.instance() or QApplication(sys.argv)

from widgets.signal_group_panel import SignalGroupPanel
from core.can_data import MessageDef, SignalDef


def _build_widget():
    w = SignalGroupPanel()
    sdef = SignalDef(
        name="S1", start_bit=0, length=8, byte_order="intel", scale=1, offset=0,
        unit="", min_val=0, max_val=255, choices={},
    )
    mdef = MessageDef(frame_id=0x100, name="MsgA", dlc=8, is_fd=False, signals=[sdef])
    w.set_messages([mdef])
    return w


def test_remark_edit_and_autosave():
    print("[4] 备注编辑 + 自动保存 ...")
    w = _build_widget()
    w.add_signals([("MsgA", "S1", "0x100")])
    assert w._sig_list.topLevelItemCount() == 1, "应新增一行信号"
    item = w._sig_list.topLevelItem(0)
    le = w._sig_list.itemWidget(item, 1)
    assert isinstance(le, QLineEdit), "备注列应为可编辑 QLineEdit"

    # 编辑备注并触发 editingFinished
    le.setText("制动辅助模式状态")
    le.editingFinished.emit()
    sig_ref = item.data(0, Qt.UserRole)
    assert sig_ref.remark == "制动辅助模式状态", f"备注应写回 SignalRef, got={sig_ref.remark}"
    assert w._dirty is True, "编辑备注应置脏标记"

    # 指定配置文件路径后自动保存
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        w._config_path = path
        w._autosave()
        assert w._dirty is False, "自动保存后脏标记应清除"
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        assert cfg["groups"][0]["signals"][0]["remark"] == "制动辅助模式状态", \
            f"JSON 应含备注, got={cfg['groups'][0]['signals'][0].get('remark')}"
        print("    OK: 备注写回 SignalRef 并经自动保存持久化到 JSON")
    finally:
        os.unlink(path)
    w.close()


def test_remark_roundtrip_on_load():
    print("[4] 备注加载回显 ...")
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"groups": [{
                "name": "G",
                "signals": [{"msg_name": "MsgA", "sig_name": "S1",
                             "frame_id": "0x100", "remark": "刹车状态"}],
            }]}, f)
        w = _build_widget()
        w.load_config_from_path(path)
        assert w._sig_list.topLevelItemCount() == 1
        item = w._sig_list.topLevelItem(0)
        le = w._sig_list.itemWidget(item, 1)
        assert le.text() == "刹车状态", f"加载后备注应回显, got={le.text()}"
        sig_ref = item.data(0, Qt.UserRole)
        assert sig_ref.remark == "刹车状态"
        print("    OK: 加载配置后备注正确回显到可编辑列")
    finally:
        os.unlink(path)
    w.close()


if __name__ == "__main__":
    test_remark_edit_and_autosave()
    test_remark_roundtrip_on_load()
    print("SIGNAL GROUP REMARK TESTS PASSED")
