# tests/test_message_table_enhance.py
"""报文表格页增强测试（offscreen，无 matplotlib 渲染）：
双击展开报文 ID 后，信号子项新增『十六进制值』列与『信号描述』列。
基于 MessageTableModel 接口断言（不再使用 QTreeWidget 内部 API）。
"""
import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import cantools
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt

from widgets.message_table import MessageTableWidget


DBC_TEXT = """VERSION ""

NS_ :
\tNS_DESC_
\tCM_
\tBA_DEF_
\tBA_
\tVAL_
\tCAT_DEF_
\tCAT_
\tFILTER
\tBA_DEF_DEF_
\tEV_DATA_
\tENVVAR_DATA_
\tSGTYPE
\tSGTYPE_VAL_
\tBA_DEF_SGTYPE_
\tBA_SGTYPE_
\tSIG_TYPE_REF_
\tVAL_TABLE_
\tSIG_GROUP_
\tSIG_VALTYPE_
\tBO_TX_BU_
\tBA_DEF_DEF_REL_
\tBA_REL_
\tBA_DEF_DEF_REL_
\tBU_EV_REL_
\tBO_REL_
\tBA_DEF_DEF_REL_
\tST_VAL_

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


_app = None


def get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


def _child_map(model, frame_idx):
    idx = model.index(frame_idx, 0)
    model.fetchMore(idx)
    n = model.rowCount(idx)
    return {
        model.data(model.index(i, 1, idx), Qt.DisplayRole): i
        for i in range(n)
    }, idx


def _make_widget_with_frame(dbc_path):
    get_app()
    w = MessageTableWidget()
    frame_index = pd.DataFrame({
        "frame_id": np.array([0], dtype=np.int64),
        "timestamp": np.array([0.0], dtype=np.float64),
        "arbitration_id": np.array([0x112], dtype=np.uint32),
        "dlc": np.array([8], dtype=np.uint8),
        "channel": np.array([0], dtype=np.int32),
        "is_fd": np.array([False], dtype=bool),
    })
    raw_data = np.zeros((1, 8), dtype=np.uint8)
    raw_data[0] = [1, 100, 0, 0, 0, 0, 0, 0]
    w.update_dbc(dbc_path)
    w.set_data(frame_index, raw_data, [], dbc_path)
    return w


def test_message_table_hex_and_desc():
    print("[A] 报文表格页：十六进制值 + 信号描述列 ...")
    get_app()
    dbc_path = make_dbc()
    w = _make_widget_with_frame(dbc_path)

    cmap, idx = _child_map(w._model, 0)
    assert "TCU_Drivemode" in cmap and "Speed" in cmap

    dm = cmap["TCU_Drivemode"]
    sp = cmap["Speed"]
    # 十六进制值列（左侧）
    assert w._model.data(w._model.index(dm, 2, idx), Qt.DisplayRole) == "0x1", \
        f"TCU_Drivemode 十六进制列应为 '0x1'"
    # 十进制值列（含枚举名）
    assert w._model.data(w._model.index(dm, 3, idx), Qt.DisplayRole) == "1 (前进)", \
        f"TCU_Drivemode 十进制列应为 '1 (前进)'"
    # 信号描述列（DBC choices 优先）
    assert w._model.data(w._model.index(dm, 5, idx), Qt.DisplayRole) == "前进", \
        f"TCU_Drivemode 描述列应为 '前进'"
    # Speed：十六进制 + 十进制 + 无描述
    assert w._model.data(w._model.index(sp, 2, idx), Qt.DisplayRole) == "0x64", \
        f"Speed 十六进制列应为 '0x64'"
    assert w._model.data(w._model.index(sp, 3, idx), Qt.DisplayRole) == "10.0", \
        f"Speed 十进制列应为 '10.0'"
    assert w._model.data(w._model.index(sp, 5, idx), Qt.DisplayRole) == "", \
        f"Speed 无描述应为空"
    print("    OK: 十六进制/十进制/描述列均正确（DBC choices 优先）")

    # Excel 兜底：清解码缓存后用 Excel 描述重新展开
    w._model._decoded.clear()
    w._model._decode_err.clear()
    w.set_value_descriptions({"TCU_Drivemode": {1: "Excel前进"}})
    cmap2, idx2 = _child_map(w._model, 0)
    dm2 = cmap2["TCU_Drivemode"]
    assert w._model.data(w._model.index(dm2, 5, idx2), Qt.DisplayRole) == "Excel前进", \
        f"Excel 兜底应显示 'Excel前进'"
    print("    OK: Excel 表描述兜底生效")
    print("[A] 通过\n")


if __name__ == "__main__":
    test_message_table_hex_and_desc()
    print("ALL TESTS PASSED")
