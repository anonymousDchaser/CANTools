"""向量化信号解码正确性测试：随机信号(含 motorola/符号/各种长度) 与 cantools 逐帧解码对比。

直接以脚本运行：QT_QPA_PLATFORM=offscreen python tests/test_signal_decode.py
或以 pytest 运行。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest
import cantools

from core.signal_decode import decode_signal_matrix


def _build_dbc(specs):
    """每条信号独立放一条报文（避免同报文重叠），返回 DBC 文本。"""
    lines = [
        "VERSION \"\"",
        "",
        "NS_ :",
        "\tNS_DESC_",
        "\tCM_",
        "\tBA_DEF_",
        "\tBA_",
        "\tVAL_",
        "\tCAT_DEF_",
        "\tCAT_",
        "\tFILTER",
        "\tBA_DEF_DEF_",
        "\tEV_DATA_",
        "\tENVVAR_DATA_",
        "\tSGTYPE_",
        "\tSGTYPE_VAL_",
        "\tBA_DEF_SGTYPE_",
        "\tBA_SGTYPE_",
        "\tSIG_TYPE_REF_",
        "\tVAL_TABLE_",
        "BS_:",
        "BU_: Vector__XXX",
        "",
    ]
    for i, s in enumerate(specs):
        bo = "0" if s["byte_order"] == "big" else "1"
        sign = "-" if s["signed"] else "+"
        lines.append(f"BO_ {100 + i} Msg{i}: 8 Vector__XXX")
        lines.append(
            f" SG_ S{i} : {s['start']}|{s['length']}@{bo}{sign} "
            f"(1,0) [0|0] \"\" Vector__XXX"
        )
    lines.append("")
    return "\n".join(lines)


def _rand_specs(rng, n=40):
    specs = []
    for _ in range(n):
        length = int(rng.integers(1, 41))
        start = int(rng.integers(0, 64 - length + 1))
        bo = rng.choice(["little", "big"])
        signed = bool(rng.integers(0, 2))
        specs.append({"start": start, "length": length,
                      "byte_order": bo, "signed": signed})
    return specs


def test_vectorized_matches_cantools():
    rng = np.random.default_rng(42)
    specs = _rand_specs(rng, n=40)
    dbc_text = _build_dbc(specs)
    db = cantools.database.load_string(dbc_text, database_format="dbc")

    # 300 帧随机 8 字节原始数据
    M = 300
    raw_mat = rng.integers(0, 256, size=(M, 8)).astype(np.uint8)

    max_abs_err = 0.0
    for i, msg in enumerate(db.messages):
        sig = msg.signals[0]
        vec = decode_signal_matrix(raw_mat, sig)
        ref = np.array([
            msg.decode(bytes(raw_mat[r]), decode_choices=False, scaling=True)[sig.name]
            for r in range(M)
        ], dtype=np.float64)
        err = np.max(np.abs(vec - ref))
        max_abs_err = max(max_abs_err, err)
        assert np.allclose(vec, ref, rtol=0, atol=1e-6), \
            f"信号 S{i}(start={sig.start},len={sig.length},bo={sig.byte_order}," \
            f"signed={sig.is_signed}) 解码不一致: max_err={err}"

    assert max_abs_err <= 1e-6, f"最大误差 {max_abs_err} 超阈值"


def test_large_matrix_performance():
    """性能：20 万帧单信号向量化解码应在亚秒级。"""
    rng = np.random.default_rng(7)
    specs = _rand_specs(rng, n=1)
    dbc_text = _build_dbc(specs)
    db = cantools.database.load_string(dbc_text, database_format="dbc")
    sig = db.messages[0].signals[0]

    M = 200_000
    raw_mat = rng.integers(0, 256, size=(M, 8)).astype(np.uint8)
    import time
    t0 = time.time()
    out = decode_signal_matrix(raw_mat, sig)
    dt = time.time() - t0
    assert out.shape == (M,)
    # 不应显著慢于 1s（通常 < 0.1s）
    assert dt < 1.0, f"向量化解码过慢: {dt:.3f}s"
    print(f"[perf] 20万帧向量化解码 {dt*1000:.1f}ms")


if __name__ == "__main__":
    test_vectorized_matches_cantools()
    test_large_matrix_performance()
    print("ALL SIGNAL_DECODE TESTS PASSED")
