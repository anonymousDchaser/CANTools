import os
from core.dbc_parser import parse_dbc

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

def test_parse_dbc_messages():
    messages = parse_dbc(os.path.join(FIXTURE_DIR, "test.dbc"))
    assert len(messages) == 2
    engine = next(m for m in messages if m.name == "EngineData")
    assert engine.frame_id == 0x1A0
    assert engine.dlc == 8
    assert len(engine.signals) == 3

def test_parse_dbc_signal_details():
    messages = parse_dbc(os.path.join(FIXTURE_DIR, "test.dbc"))
    engine = next(m for m in messages if m.name == "EngineData")
    rpm = next(s for s in engine.signals if s.name == "EngineRPM")
    assert rpm.start_bit == 0
    assert rpm.length == 16
    assert rpm.byte_order == "intel"
    assert rpm.scale == 0.25
    assert rpm.offset == 0.0
    assert rpm.unit == "rpm"
    assert rpm.min_val == 0.0
    assert rpm.max_val == 16383.75

def test_parse_dbc_motorola_signal():
    messages = parse_dbc(os.path.join(FIXTURE_DIR, "test.dbc"))
    engine = next(m for m in messages if m.name == "EngineData")
    for sig in engine.signals:
        assert sig.byte_order == "intel"
