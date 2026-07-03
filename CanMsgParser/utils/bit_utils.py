"""Intel/Motorola 字节序位布局计算工具

DBC 中的位编号规则：
  Byte0: bit7 bit6 bit5 bit4 bit3 bit2 bit1 bit0
  Byte1: bit7 bit6 ...                   ... bit0
  ...

Intel（小端）：起始位是 LSB，从起始位向高位排列
  - 同一字节内：bit 编号递增
  - 跨字节时：进入下一字节的 bit0 继续递增

Motorola（大端）：起始位是 MSB，从起始位向低位排列
  - 同一字节内：bit 编号递减
  - 跨字节时：跳到下一字节的 bit7 继续递减
"""


def get_bit_positions(start_bit: int, length: int, byte_order: str) -> list[tuple[int, int]]:
    """计算信号占用的所有位坐标。返回 [(byte_index, bit_index), ...]"""
    if byte_order == "intel":
        return _intel_positions(start_bit, length)
    elif byte_order == "motorola":
        return _motorola_positions(start_bit, length)
    else:
        raise ValueError(f"Unknown byte_order: {byte_order}")


def _intel_positions(start_bit: int, length: int) -> list[tuple[int, int]]:
    positions = []
    current_byte = start_bit // 8
    current_bit = start_bit % 8
    for _ in range(length):
        positions.append((current_byte, current_bit))
        current_bit += 1
        if current_bit > 7:
            current_bit = 0
            current_byte += 1
    return positions


def _motorola_positions(start_bit: int, length: int) -> list[tuple[int, int]]:
    positions = []
    current_byte = start_bit // 8
    current_bit = start_bit % 8
    for _ in range(length):
        positions.append((current_byte, current_bit))
        current_bit -= 1
        if current_bit < 0:
            current_bit = 7
            current_byte += 1
    return positions
