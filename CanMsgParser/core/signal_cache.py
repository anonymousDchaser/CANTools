"""解码结果 LRU 缓存"""
from collections import OrderedDict
import numpy as np


class SignalCache:
    def __init__(self, max_entries: int = 100):
        self._max_entries = max_entries
        self._cache: OrderedDict[tuple[str, str], tuple[np.ndarray, np.ndarray]] = OrderedDict()

    def get(self, msg_name: str, sig_name: str) -> tuple[np.ndarray, np.ndarray] | None:
        key = (msg_name, sig_name)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, msg_name: str, sig_name: str, timestamps: np.ndarray, values: np.ndarray):
        key = (msg_name, sig_name)
        if key in self._cache:
            self._cache.move_to_end(key)
            self._cache[key] = (timestamps, values)
        else:
            self._cache[key] = (timestamps, values)
            if len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)

    def clear(self):
        self._cache.clear()

    def __len__(self):
        return len(self._cache)
