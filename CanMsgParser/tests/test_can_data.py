# tests/test_can_data.py
import numpy as np
from core.can_data import SignalDef, MessageDef, DecodedSignal


def test_signal_def_creation():
    sig = SignalDef(
        name="EngineRPM",
        start_bit=0,
        length=16,
        byte_order="intel",
        scale=0.25,
        offset=0.0,
        unit="rpm",
        min_val=0.0,
        max_val=16383.75,
    )
    assert sig.name == "EngineRPM"
    assert sig.length == 16
    assert sig.byte_order == "intel"


def test_message_def_with_signals():
    sig1 = SignalDef("RPM", 0, 16, "intel", 1.0, 0.0, "rpm", 0.0, 8000.0)
    sig2 = SignalDef("Speed", 16, 16, "intel", 0.1, 0.0, "km/h", 0.0, 300.0)
    msg = MessageDef(
        frame_id=0x1A0,
        name="EngineData",
        dlc=8,
        is_fd=False,
        signals=[sig1, sig2],
    )
    assert msg.frame_id == 0x1A0
    assert len(msg.signals) == 2
    assert msg.signals[0].name == "RPM"


def test_decoded_signal():
    ts = np.array([0.0, 0.1, 0.2])
    vals = np.array([100.0, 200.0, 300.0])
    ds = DecodedSignal(
        msg_name="EngineData",
        sig_name="RPM",
        timestamps=ts,
        values=vals,
    )
    assert ds.msg_name == "EngineData"
    assert len(ds.timestamps) == 3
    assert ds.values[1] == 200.0