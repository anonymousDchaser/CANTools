"""验证解码后信号时间戳使用全局统一原点(测量起点)，而非按本信号首帧二次归零。

回归保护：曾因 load_worker 中 `ts_arr -= ts_arr[0]` 导致每个信号各自归零，
使得不同信号时间轴原点不同，"下发信号 vs 上报信号"的反馈时长对不上，
且与 TSMaster 等市面工具的时间轴不一致。
"""
import os
import tempfile

import numpy as np
import pandas as pd
from PyQt5.QtCore import QCoreApplication

from workers.load_worker import DecodeWorker
from core.signal_cache import SignalCache

DBC_TEXT = """
VERSION ""

NS_ :
	NS_DESC_ CM_ BA_DEF_ BA_ VAL_ CAT_DEF_ CAT_ FILTER BA_DEF_DEF_ EV_DATA_
	ENVVAR_DATA_ SGTYPE SGROUP SIG_TYPE_REF VAL_TABLE SIG_GROUP BA_DEF_SGTYPE_
	BA_SGTYPE_ SIG_VALTYPE_ SIGTYPE_VALTYPE_ BO_TX_BU_ BA_DEF_REL_ BA_REL_
	BA_DEF_DEF_REL_ BA_REL_ BA_DEF_SGTYPE_REL_ BA_SGTYPE_REL_

BS_:

BU_:

BO_ 256 Msg100: 8 Vector__XXX
 SG_ A : 0|8@1+ (1,0) [0|255] "" Vector__XXX
"""


def _make_dbc(tmp_dir: str) -> str:
    p = os.path.join(tmp_dir, "origin.dbc")
    with open(p, "w", encoding="utf-8") as f:
        f.write(DBC_TEXT)
    return p


def test_decode_keeps_global_origin():
    # 与项目其它测试保持一致：用 tempfile 而非 pytest fixture，
    # 这样 pytest 与「直接 python 运行本文件」两种方式都能真正执行。
    app = QCoreApplication.instance() or QCoreApplication([])
    assert app is not None

    with tempfile.TemporaryDirectory() as tmp_dir:
        dbc = _make_dbc(tmp_dir)
        # 时间戳已是"测量起点为原点"的全局时间(由 log_loader 归一化得到)
        fi = pd.DataFrame({
            "frame_id": [0, 1, 2],
            "timestamp": [10.0, 20.0, 30.0],
            "arbitration_id": [0x100, 0x100, 0x100],
            "dlc": [8, 8, 8],
            "channel": [0, 0, 0],
            "is_fd": [False, False, False],
        })
        raw = np.zeros((3, 8), dtype=np.uint8)
        raw[0, 0] = 5
        raw[1, 0] = 7
        raw[2, 0] = 9

        captured = {}
        w = DecodeWorker(dbc, "Msg100", "A", fi, raw, SignalCache())
        w.finished.connect(lambda ds: captured.update(ds=ds))
        w.run()

        assert "ds" in captured, "DecodeWorker 未通过 finished 信号返回结果"
        ds = captured["ds"]
        # 关键断言：时间戳应等于全局时间 [10,20,30]，而非按本信号首帧归零成 [0,10,20]
        assert np.allclose(ds.timestamps, [10.0, 20.0, 30.0]), \
            "信号时间戳被按本信号首帧归零，时间原点不正确: %s" % ds.timestamps
        assert np.allclose(ds.values, [5.0, 7.0, 9.0])
    print("OK: 解码后信号时间戳保持全局原点(未按本信号首帧二次归零)")


if __name__ == "__main__":
    test_decode_keeps_global_origin()
    print("DECODE ORIGIN TEST PASSED")
