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
    if raw_mat.shape[1] * 8 < start + length:
        # 字节宽度不足，无法解出该信号（数据异常），按 0 处理。
        return np.zeros(M, dtype=np.float64)

    if signal.byte_order == "big_endian":
        seq0 = (8 * (start // 8)) + (7 - (start % 8))
        val = np.zeros(M, dtype=np.uint64)
        one = np.uint64(1)
        for i in range(length):
            p = seq0 + i
            bi = p // 8
            bj = 7 - (p % 8)
            bit = (raw_mat[:, bi].astype(np.uint64) >> np.uint64(bj)) & one
            val = (val << one) | bit
    else:
        val = np.zeros(M, dtype=np.uint64)
        for i in range(length):
            p = start + i
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
