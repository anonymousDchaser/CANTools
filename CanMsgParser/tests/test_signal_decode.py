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


# ── 回归：容量越界判定必须用「网络位起点 seq0」而非 DBC 原始 start 位 ──
#
# 历史缺陷（用户反馈：曲线图某个信号恒为 0，同帧「实时报文」却有值）：
# 旧实现用 `start + length > 矩阵位数` 判越界，而 Motorola 起始位是锯齿形编号
# （同一字节内 7=MSB → 0=LSB），其网络位起点
#   seq0 = 8*(start//8) + (7 - start%8)        恒 >= start
# 凡 MSB 不在字节内第 7 位的 Motorola 信号，容量判定都会多算 (7 - start%8) 位；
# 占用范围恰好贴合数据末尾的信号因此被误判「矩阵宽度不足」而整条置 0。
# 实测样例：64 字节 CAN FD 报文 DEV_LBMS_Debug_1 的 TX_Reserve_Signal_14
# （start=503, len=16 → 网络位 496~511，正好用到第 64 字节末位）。
# 实时报文页走 cantools 逐帧 decode 不受影响，故出现「报文有值、曲线为 0」。


def _build_dbc_ext(specs, base_id=0x19000100):
    """每条信号独立一条报文（扩展帧 ID 避开 11 位限制），specs 需带 msg_len。"""
    lines = [
        'VERSION ""', "", "NS_ :",
        "\tNS_DESC_", "\tCM_", "\tBA_DEF_", "\tBA_", "\tVAL_", "\tCAT_DEF_",
        "\tCAT_", "\tFILTER", "\tBA_DEF_DEF_", "\tEV_DATA_", "\tENVVAR_DATA_",
        "\tSGTYPE_", "\tSGTYPE_VAL_", "\tBA_DEF_SGTYPE_", "\tBA_SGTYPE_",
        "\tSIG_TYPE_REF_", "\tVAL_TABLE_", "BS_:", "BU_: Vector__XXX", "",
    ]
    for i, s in enumerate(specs):
        bo = "0" if s["byte_order"] == "big" else "1"
        sign = "-" if s["signed"] else "+"
        # 2147483648 (0x80000000) + id 为 DBC 扩展帧写法
        lines.append(f"BO_ {2147483648 + base_id + i} M{i}: {s['msg_len']} Vector__XXX")
        lines.append(
            f" SG_ S{i} : {s['start']}|{s['length']}@{bo}{sign} "
            f"(1,0) [0|0] \"\" Vector__XXX"
        )
        lines.append("")
    return "\n".join(lines)


def _compare_all(db, raw_mat, expect_nonzero_idx=None):
    """逐报文比对向量化解码与 cantools 逐帧解码。"""
    for i, msg in enumerate(db.messages):
        sig = msg.signals[0]
        vec = decode_signal_matrix(raw_mat, sig)
        ref = np.array([
            msg.decode(bytes(raw_mat[r]), decode_choices=False, scaling=True)[sig.name]
            for r in range(raw_mat.shape[0])
        ], dtype=np.float64)
        assert np.allclose(vec, ref, rtol=0, atol=1e-6), (
            f"信号 S{i}(start={sig.start},len={sig.length},bo={sig.byte_order})"
            f" 解码不一致: vec={vec[:4]} ref={ref[:4]}"
        )
        if expect_nonzero_idx is not None and i in expect_nonzero_idx:
            assert np.count_nonzero(vec) > 0, (
                f"信号 S{i}(start={sig.start},len={sig.length},bo={sig.byte_order})"
                f" 被误判越界而整条置 0（旧缺陷回归）"
            )


def test_boundary_signals_not_zeroed():
    """8 字节报文下穷举全部合法位组合，覆盖「占用贴合末尾」的边界信号。"""
    w, M = 8, 4
    specs, old_bug_idx = [], []
    for big in (True, False):
        for length in range(1, w * 8 + 1):
            for start in range(w * 8):
                seq0 = (8 * (start // 8)) + (7 - start % 8) if big else start
                if seq0 + length > w * 8:
                    continue                      # 该组合本就超出数据范围
                specs.append({"start": start, "length": length,
                              "byte_order": "big" if big else "little",
                              "signed": False, "msg_len": w})
                if start + length > w * 8:        # 旧实现会整条置 0 的组合
                    old_bug_idx.append(len(specs) - 1)
    assert old_bug_idx, "用例未覆盖旧缺陷，边界组合选取失效"

    db = cantools.database.load_string(_build_dbc_ext(specs),
                                       database_format="dbc")
    rng = np.random.default_rng(2026)
    raw_mat = rng.integers(0, 256, size=(M, w)).astype(np.uint8)
    _compare_all(db, raw_mat, expect_nonzero_idx=set(old_bug_idx))


def test_fd_last_byte_signal_matches_cantools():
    """复现实测样例：64 字节 FD 报文里占用到末字节末位的 Motorola 信号。"""
    specs = [
        {"start": 503, "length": 16, "byte_order": "big",
         "signed": False, "msg_len": 64},   # TX_Reserve_Signal_14 的位布局
        {"start": 511, "length": 8, "byte_order": "big",
         "signed": False, "msg_len": 64},   # 整字节落在最后一字节
        {"start": 495, "length": 16, "byte_order": "big",
         "signed": False, "msg_len": 64},   # 网络位 488~503，未触及末尾（对照）
    ]
    db = cantools.database.load_string(_build_dbc_ext(specs),
                                       database_format="dbc")
    rng = np.random.default_rng(7)
    raw_mat = rng.integers(0, 256, size=(16, 64)).astype(np.uint8)
    _compare_all(db, raw_mat, expect_nonzero_idx={0, 1, 2})


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
