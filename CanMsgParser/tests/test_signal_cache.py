import numpy as np
from core.signal_cache import SignalCache

def test_cache_put_and_get():
    cache = SignalCache(max_entries=10)
    ts = np.array([0.0, 0.1, 0.2])
    vals = np.array([1.0, 2.0, 3.0])
    cache.put("EngineData", "RPM", ts, vals)
    result = cache.get("EngineData", "RPM")
    assert result is not None
    np.testing.assert_array_equal(result[0], ts)
    np.testing.assert_array_equal(result[1], vals)

def test_cache_miss():
    cache = SignalCache(max_entries=10)
    assert cache.get("NotExist", "Signal") is None

def test_cache_eviction():
    cache = SignalCache(max_entries=2)
    cache.put("M1", "S1", np.array([0.0]), np.array([1.0]))
    cache.put("M1", "S2", np.array([0.0]), np.array([2.0]))
    cache.put("M1", "S3", np.array([0.0]), np.array([3.0]))
    assert cache.get("M1", "S1") is None
    assert cache.get("M1", "S2") is not None
    assert cache.get("M1", "S3") is not None

def test_cache_clear():
    cache = SignalCache(max_entries=10)
    cache.put("M1", "S1", np.array([0.0]), np.array([1.0]))
    cache.clear()
    assert cache.get("M1", "S1") is None
