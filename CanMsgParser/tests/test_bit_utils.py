from utils.bit_utils import get_bit_positions

def test_intel_single_byte():
    positions = get_bit_positions(start_bit=4, length=4, byte_order="intel")
    assert positions == [(0, 4), (0, 5), (0, 6), (0, 7)]

def test_intel_cross_byte():
    positions = get_bit_positions(start_bit=4, length=8, byte_order="intel")
    assert positions == [(0, 4), (0, 5), (0, 6), (0, 7), (1, 0), (1, 1), (1, 2), (1, 3)]

def test_motorola_single_byte():
    positions = get_bit_positions(start_bit=7, length=4, byte_order="motorola")
    assert positions == [(0, 7), (0, 6), (0, 5), (0, 4)]

def test_motorola_cross_byte():
    positions = get_bit_positions(start_bit=7, length=12, byte_order="motorola")
    assert positions == [(0, 7), (0, 6), (0, 5), (0, 4), (0, 3), (0, 2), (0, 1), (0, 0), (1, 7), (1, 6), (1, 5), (1, 4)]

def test_intel_full_16bit():
    positions = get_bit_positions(start_bit=0, length=16, byte_order="intel")
    assert len(positions) == 16
    assert positions[0] == (0, 0)
    assert positions[7] == (0, 7)
    assert positions[8] == (1, 0)
    assert positions[15] == (1, 7)

def test_motorola_full_16bit():
    positions = get_bit_positions(start_bit=7, length=16, byte_order="motorola")
    assert len(positions) == 16
    assert positions[0] == (0, 7)
    assert positions[7] == (0, 0)
    assert positions[8] == (1, 7)
    assert positions[15] == (1, 0)
