import numpy as np
from utils.lttb import lttb_downsample

def test_lttb_no_downsample():
    ts = np.array([0.0, 1.0, 2.0])
    vals = np.array([10.0, 20.0, 30.0])
    out_ts, out_vals = lttb_downsample(ts, vals, threshold=10)
    np.testing.assert_array_equal(out_ts, ts)
    np.testing.assert_array_equal(out_vals, vals)

def test_lttb_reduces_points():
    ts = np.linspace(0, 100, 1000)
    vals = np.sin(ts)
    out_ts, out_vals = lttb_downsample(ts, vals, threshold=100)
    assert len(out_ts) == 100
    assert len(out_vals) == 100

def test_lttb_preserves_endpoints():
    ts = np.linspace(0, 10, 500)
    vals = np.random.randn(500)
    out_ts, out_vals = lttb_downsample(ts, vals, threshold=50)
    assert out_ts[0] == ts[0]
    assert out_ts[-1] == ts[-1]

def test_lttb_preserves_peaks():
    ts = np.linspace(0, 10, 1000)
    vals = np.zeros(1000)
    vals[500] = 100.0
    out_ts, out_vals = lttb_downsample(ts, vals, threshold=50)
    assert 100.0 in out_vals
