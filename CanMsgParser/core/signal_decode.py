# core/signal_decode.py
"""向量化信号解码：对 (M, W) 原始字节矩阵，按 cantools Signal 的位布局一次性解出
M 帧的物理值（numpy 向量化），替代逐帧 msg_def.decode 的 Python 循环。

位提取语义严格对齐 cantools（见 cantools.database.utils.decode_data）：
- little_endian(intel)：顺序 start_bit，位 i 对应 byte=(start+i)//8, bit=(start+i)%8，LSB 优先。
- big_endian(motorola)：顺序位 seq=(8*(start//8))+(7-(start%8))，位 i 对应
  byte=seq//8, bit=7-(seq%8)，MSB 优先。
- 符号扩展：is_signed 且符号位为 1 时减去 2^length。
- 物理值 = raw * scale + offset。
"""
import numpy as np


def decode_signal_matrix(raw_mat: np.ndarray, signal) -> np.ndarray:
    """raw_mat: (M, W) uint8，W 需 >= W 所需字节（通常 8）。
    返回 (M,) float64 物理值。

    signal 需提供属性：start, length, byte_order('little_endian'/'big_endian'),
    is_signed(bool), scale(float), offset(float)。
    """
    start = int(signal.start)
    length = int(signal.length)
    M = raw_mat.shape[0]
    if length <= 0:
        return np.zeros(M, dtype=np.float64)

    is_big = signal.byte_order == "big_endian"
    # 起始位换算为「网络位序」起点（与 cantools.database.utils.start_bit 一致）：
    #  - little_endian：start 本身就是网络位序起点；
    #  - big_endian：DBC 中 Motorola 起始位是锯齿形编号（同一字节内 7=MSB → 0=LSB，
    #    即字节内位号向下计数），换算后 seq0 = 8*(start//8) + (7 - start%8)，恒 >= start。
    # ⚠️ 越界判定必须用 seq0，不能用 start：占用范围恰好贴合数据末尾的 Motorola 信号
    # （如 64 字节报文里 start=503/len=16，网络位 496~511）用 start 会把起点右移
    # (7 - start%8) 位而误判为「矩阵宽度不足」，导致整条曲线恒为 0。
    seq0 = (8 * (start // 8)) + (7 - (start % 8)) if is_big else start
    if raw_mat.shape[1] * 8 < seq0 + length:
        # 字节宽度不足，无法解出该信号（数据异常），按 0 处理。
        return np.zeros(M, dtype=np.float64)

    val = np.zeros(M, dtype=np.uint64)
    if is_big:
        one = np.uint64(1)
        for i in range(length):
            p = seq0 + i
            bi = p // 8
            bj = 7 - (p % 8)
            bit = (raw_mat[:, bi].astype(np.uint64) >> np.uint64(bj)) & one
            val = (val << one) | bit
    else:
        for i in range(length):
            p = seq0 + i
            bi = p // 8
            bj = p % 8
            bit = (raw_mat[:, bi].astype(np.uint64) >> np.uint64(bj)) & np.uint64(1)
            val = val | (bit << np.uint64(i))

    if signal.is_signed:
        # 必须在 float64 域做符号扩展：uint64 减法会回绕成 ~2^64 的极大值。
        val = val.astype(np.float64)
        sign_bit = float(1 << (length - 1))
        full = float(1 << length)
        val = np.where(val >= sign_bit, val - full, val)
    else:
        val = val.astype(np.float64)

    return val * float(signal.scale) + float(signal.offset)
