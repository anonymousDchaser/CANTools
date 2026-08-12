# tests/test_byte_change_highlight.py
"""字节变化高亮与渐变消退功能的单元测试

验证 core.byte_change.compute_byte_change_array 的向量化语义：
- 同 ID 帧之间字节变化检测
- 跨 ID 独立跟踪
- 渐变帧数计算
- 首帧处理
- HexDataDelegate 颜色插值逻辑

（原 _compute_byte_change_info 的嵌套字典实现已迁移为向量化数组，
 本测试直接验证数组输出，iloc 即筛选后位置，语义与原 dict[frame_id][byte_idx] 一致。）
"""
import sys
import os
import pytest
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.byte_change import compute_byte_change_array, NO_CHANGE
from PyQt5.QtWidgets import QApplication
from widgets.message_table import HexDataDelegate

_app = None


def get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


def _make_frame_index(frame_ids, timestamps, arb_ids, dlcs, channels=None):
    n = len(frame_ids)
    if channels is None:
        channels = [0] * n
    return pd.DataFrame({
        "frame_id": np.array(frame_ids, dtype=np.int64),
        "timestamp": np.array(timestamps, dtype=np.float64),
        "arbitration_id": np.array(arb_ids, dtype=np.uint32),
        "dlc": np.array(dlcs, dtype=np.uint8),
        "channel": np.array(channels, dtype=np.int32),
        "is_fd": np.array([False] * n, dtype=bool),
    })


class TestByteChangeArray:
    def test_first_frame_all_bytes_marked_as_no_change(self):
        fi = _make_frame_index([0], [0.0], [0x112], [8])
        rd = np.zeros((1, 8), dtype=np.uint8)
        rd[0] = [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x80, 0x00]
        arr = compute_byte_change_array(fi, rd)
        assert arr.shape == (1, 8)
        assert (arr[0] == NO_CHANGE).all()

    def test_no_change_increments_frames_since(self):
        n = 5
        fi = _make_frame_index(list(range(n)), [i * 0.001 for i in range(n)], [0x112] * n, [8] * n)
        rd = np.zeros((n, 8), dtype=np.uint8)
        for i in range(n):
            rd[i] = [0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x11, 0x22]
        arr = compute_byte_change_array(fi, rd)
        assert (arr == NO_CHANGE).all()

    def test_byte_change_resets_counter(self):
        n = 6
        fi = _make_frame_index(list(range(n)), [i * 0.001 for i in range(n)], [0x112] * n, [8] * n)
        rd = np.zeros((n, 8), dtype=np.uint8)
        for i in range(n):
            rd[i] = [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x80, 0x00]
        rd[4][4] = 0x01
        rd[5][4] = 0x01
        arr = compute_byte_change_array(fi, rd)
        assert (arr[0:4, 4] == NO_CHANGE).all()
        assert arr[4, 4] == 0
        assert arr[5, 4] == 1
        assert (arr[4, [0, 1, 2, 3, 5, 6, 7]] == NO_CHANGE).all()
        assert (arr[5, [0, 1, 2, 3, 5, 6, 7]] == NO_CHANGE).all()

    def test_different_arb_ids_tracked_independently(self):
        fi = _make_frame_index([0, 1, 2, 3], [0, 0.001, 0.002, 0.003],
                               [0x112, 0x220, 0x112, 0x220], [8] * 4)
        rd = np.zeros((4, 8), dtype=np.uint8)
        rd[0][0] = 0xAA
        rd[2][0] = 0xAA
        rd[1][0] = 0x00
        rd[3][0] = 0xFF
        arr = compute_byte_change_array(fi, rd)
        assert arr[0, 0] == NO_CHANGE
        assert arr[2, 0] == NO_CHANGE
        assert arr[1, 0] == NO_CHANGE
        assert arr[3, 0] == 0

    def test_filtered_data_only_tracks_visible_frames(self):
        full = _make_frame_index(list(range(6)), [i * 0.001 for i in range(6)], [0x112] * 6, [8] * 6)
        rd = np.zeros((6, 8), dtype=np.uint8)
        for i in range(6):
            rd[i] = [0x00] * 8
        rd[3][0] = 0xFF
        filt = full.iloc[[0, 3, 5]].reset_index(drop=True)
        arr = compute_byte_change_array(filt, rd)
        # 筛选后 iloc: 0->frame0, 1->frame3, 2->frame5
        assert arr[0, 0] == NO_CHANGE
        assert arr[1, 0] == 0   # frame3 变化
        assert arr[2, 0] == 0   # frame5 再次变化

    def test_empty(self):
        fi = pd.DataFrame(columns=["frame_id", "timestamp", "arbitration_id", "dlc", "channel", "is_fd"])
        arr = compute_byte_change_array(fi, np.empty((0, 8), dtype=np.uint8))
        assert arr.shape == (0, 8)

    def test_multiple_byte_changes_in_same_frame(self):
        n = 3
        fi = _make_frame_index(list(range(n)), [i * 0.001 for i in range(n)], [0x112] * n, [8] * n)
        rd = np.zeros((n, 8), dtype=np.uint8)
        rd[0] = [0x00] * 8
        rd[1] = [0x00] * 8
        rd[2] = [0xFF, 0x00, 0x00, 0xAA, 0x00, 0x00, 0x00, 0xBB]
        arr = compute_byte_change_array(fi, rd)
        assert (arr[0] == NO_CHANGE).all() and (arr[1] == NO_CHANGE).all()
        assert arr[2, 0] == 0 and arr[2, 3] == 0 and arr[2, 7] == 0
        assert (arr[2, [1, 2, 4, 5, 6]] == NO_CHANGE).all()

    def test_scenario_from_spec(self):
        n = 10
        fi = _make_frame_index(list(range(n)), [i * 0.001 for i in range(n)], [0x112] * n, [8] * n)
        rd = np.zeros((n, 8), dtype=np.uint8)
        for i in range(n):
            rd[i] = [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x80, 0x00]
        for i in range(4, n):
            rd[i][4] = 0x01
        arr = compute_byte_change_array(fi, rd)
        assert (arr[0:4, 4] == NO_CHANGE).all()
        assert arr[4, 4] == 0
        for fid in range(5, n):
            assert arr[fid, 4] == fid - 4
        assert (arr[:, 6] == NO_CHANGE).all()


class TestHexDataDelegateColorInterpolation:
    def test_color_at_zero_is_highlight(self):
        d = HexDataDelegate()
        c = d._get_byte_color(0)
        assert c.red() == HexDataDelegate.HIGHLIGHT_COLOR.red()
        assert c.green() == HexDataDelegate.HIGHLIGHT_COLOR.green()
        assert c.blue() == HexDataDelegate.HIGHLIGHT_COLOR.blue()

    def test_color_at_fade_frames_is_normal(self):
        d = HexDataDelegate()
        c = d._get_byte_color(HexDataDelegate.FADE_FRAMES)
        assert c.red() == HexDataDelegate.NORMAL_COLOR.red()
        assert c.blue() == HexDataDelegate.NORMAL_COLOR.blue()
        c2 = d._get_byte_color(HexDataDelegate.FADE_FRAMES + 50)
        assert c2.red() == HexDataDelegate.NORMAL_COLOR.red()

    def test_color_midpoint_is_interpolated(self):
        d = HexDataDelegate()
        mid = HexDataDelegate.FADE_FRAMES // 2
        c = d._get_byte_color(mid)
        hl_r = HexDataDelegate.HIGHLIGHT_COLOR.red()
        nm_r = HexDataDelegate.NORMAL_COLOR.red()
        assert abs(c.red() - int(hl_r + (nm_r - hl_r) * 0.5)) <= 1
        hl_g = HexDataDelegate.HIGHLIGHT_COLOR.green()
        nm_g = HexDataDelegate.NORMAL_COLOR.green()
        assert abs(c.green() - int(hl_g + (nm_g - hl_g) * 0.5)) <= 1

    def test_bold_within_threshold(self):
        d = HexDataDelegate()
        assert d._is_bold(0) is True
        assert d._is_bold(HexDataDelegate.BOLD_FRAMES - 1) is True
        assert d._is_bold(HexDataDelegate.BOLD_FRAMES) is False
        assert d._is_bold(HexDataDelegate.BOLD_FRAMES + 10) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
