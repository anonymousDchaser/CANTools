import os
import tempfile
import numpy as np
import pandas as pd
from core.log_loader import load_log_file

def _create_test_asc(path: str):
    with open(path, "w") as f:
        f.write("date Mon Jun 15 10:00:00 AM 2026\n")
        f.write("base hex  timestamps absolute\n")
        f.write("no internal events logged\n")
        f.write("   0.001000 1  1A0             Rx   d 8 FF 01 23 45 67 89 AB CD\n")
        f.write("   0.002000 1  1A1             Rx   d 8 00 11 22 33 44 55 66 77\n")
        f.write("   0.003000 1  1A0             Rx   d 8 FF 01 23 45 67 89 AB CE\n")

def test_load_asc_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        asc_path = os.path.join(tmpdir, "test.asc")
        _create_test_asc(asc_path)
        frame_index, raw_data = load_log_file(asc_path)
        assert isinstance(frame_index, pd.DataFrame)
        assert len(frame_index) == 3
        assert list(frame_index.columns) == ["frame_id", "timestamp", "arbitration_id", "dlc", "channel", "is_fd"]
        assert frame_index.iloc[0]["arbitration_id"] == 0x1A0
        assert isinstance(raw_data, np.ndarray)
        assert raw_data.shape[0] == 3
        assert raw_data[0, 0] == 0xFF

def test_frame_id_sequential():
    with tempfile.TemporaryDirectory() as tmpdir:
        asc_path = os.path.join(tmpdir, "test.asc")
        _create_test_asc(asc_path)
        frame_index, _ = load_log_file(asc_path)
        assert list(frame_index["frame_id"]) == [0, 1, 2]
