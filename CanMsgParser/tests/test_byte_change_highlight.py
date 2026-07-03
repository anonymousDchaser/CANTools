# tests/test_byte_change_highlight.py
"""字节变化高亮与渐变消退功能的单元测试

验证 _compute_byte_change_info 的正确性：
- 同 ID 帧之间字节变化检测
- 跨 ID 独立跟踪
- 渐变帧数计算
- 首帧处理
- HexDataDelegate 颜色插值逻辑
"""
import sys
import os
import pytest
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
from widgets.message_table import MessageTableWidget, HexDataDelegate

# 创建全局 QApplication（PyQt5 要求）
_app = None


def get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


@pytest.fixture
def app():
    """提供 QApplication 实例"""
    return get_app()


@pytest.fixture
def message_table(app):
    """创建 MessageTableWidget 实例"""
    widget = MessageTableWidget()
    yield widget
    widget.deleteLater()


def _make_frame_index(frame_ids, timestamps, arb_ids, dlcs, channels=None):
    """辅助函数：构造帧索引 DataFrame"""
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


class TestByteChangeDetection:
    """测试 _compute_byte_change_info 的核心逻辑"""

    def test_first_frame_all_bytes_marked_as_no_change(self, message_table):
        """首帧的所有字节应标记为 frames_since_change=999（无变化，正常色）"""
        frame_index = _make_frame_index([0], [0.0], [0x112], [8])
        raw_data = np.zeros((1, 8), dtype=np.uint8)
        raw_data[0] = [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x80, 0x00]

        message_table._frame_index = frame_index
        message_table._raw_data = raw_data
        message_table._filtered_index = frame_index
        message_table._compute_byte_change_info()

        info = message_table._byte_change_info
        assert 0 in info, "frame_id=0 应在变化信息中"
        for byte_idx in range(8):
            assert info[0][byte_idx] == 999, (
                f"首帧字节 {byte_idx} 应为 999（无变化，正常色），实际={info[0][byte_idx]}"
            )

    def test_no_change_increments_frames_since(self, message_table):
        """连续相同帧，frames_since_change 应递增"""
        # 5帧完全相同的数据
        frame_index = _make_frame_index(
            [0, 1, 2, 3, 4],
            [0.0, 0.001, 0.002, 0.003, 0.004],
            [0x112, 0x112, 0x112, 0x112, 0x112],
            [8, 8, 8, 8, 8],
        )
        raw_data = np.zeros((5, 8), dtype=np.uint8)
        for i in range(5):
            raw_data[i] = [0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x11, 0x22]

        message_table._frame_index = frame_index
        message_table._raw_data = raw_data
        message_table._filtered_index = frame_index
        message_table._compute_byte_change_info()

        info = message_table._byte_change_info
        # 首帧: 所有字节 = 999（无变化）
        for b in range(8):
            assert info[0][b] == 999

        # 后续帧: 从未变化，仍为 999
        for fid in [1, 2, 3, 4]:
            for b in range(8):
                assert info[fid][b] == 999, (
                    f"frame {fid} 字节 {b} 应为 999（从未变化），"
                    f"实际={info[fid][b]}"
                )

    def test_byte_change_resets_counter(self, message_table):
        """字节变化时该字节的计数器应重置为 0"""
        # 模拟场景：前4帧相同，第5帧(index=4) byte[4] 从 0x00 变为 0x01
        n = 6
        frame_index = _make_frame_index(
            list(range(n)),
            [i * 0.001 for i in range(n)],
            [0x112] * n,
            [8] * n,
        )
        raw_data = np.zeros((n, 8), dtype=np.uint8)
        for i in range(n):
            raw_data[i] = [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x80, 0x00]
        # 在第4帧改变 byte[4]
        raw_data[4][4] = 0x01
        # 第5帧保持新值
        raw_data[5][4] = 0x01

        message_table._frame_index = frame_index
        message_table._raw_data = raw_data
        message_table._filtered_index = frame_index
        message_table._compute_byte_change_info()

        info = message_table._byte_change_info

        # 帧0-3: byte[4] 的值一直是 0x00，从未变化 → 999
        assert info[0][4] == 999   # 首帧
        assert info[1][4] == 999
        assert info[2][4] == 999
        assert info[3][4] == 999

        # 帧4: byte[4] 变化 → 重置为 0
        assert info[4][4] == 0, (
            f"byte[4] 在帧4变化，应为 0，实际={info[4][4]}"
        )

        # 帧5: byte[4] 未变 → 距上次变化(帧4) = 1
        assert info[5][4] == 1, (
            f"byte[4] 在帧5未变，应距变化 1 帧，实际={info[5][4]}"
        )

        # 其他字节在帧4和帧5从未变化 → 999
        for b in [0, 1, 2, 3, 5, 6, 7]:
            assert info[4][b] == 999, (
                f"byte[{b}] 在帧4应为 999（从未变化），实际={info[4][b]}"
            )
            assert info[5][b] == 999, (
                f"byte[{b}] 在帧5应为 999（从未变化），实际={info[5][b]}"
            )

    def test_different_arb_ids_tracked_independently(self, message_table):
        """不同 arbitration_id 的字节变化应独立跟踪"""
        # 交替的两种 ID: 0x112 和 0x220
        frame_index = _make_frame_index(
            [0, 1, 2, 3],
            [0.0, 0.001, 0.002, 0.003],
            [0x112, 0x220, 0x112, 0x220],
            [8, 8, 8, 8],
        )
        raw_data = np.zeros((4, 8), dtype=np.uint8)
        # 0x112 的帧: byte[0] 始终 = 0xAA
        raw_data[0][0] = 0xAA
        raw_data[2][0] = 0xAA
        # 0x220 的帧: byte[0] 在帧3变化
        raw_data[1][0] = 0x00
        raw_data[3][0] = 0xFF  # 变化!

        message_table._frame_index = frame_index
        message_table._raw_data = raw_data
        message_table._filtered_index = frame_index
        message_table._compute_byte_change_info()

        info = message_table._byte_change_info

        # 0x112 帧 (fid=0,2): byte[0] 始终 0xAA，从未变化 → 999
        assert info[0][0] == 999   # 首帧
        assert info[2][0] == 999   # 从未变化

        # 0x220 帧 (fid=1): byte[0] 首帧 → 999
        assert info[1][0] == 999

        # 0x220 帧 (fid=3): byte[0] 变化 → 重置为 0
        assert info[3][0] == 0, (
            f"0x220 的 byte[0] 在帧3变化，应为 0，实际={info[3][0]}"
        )

    def test_filtered_data_only_tracks_visible_frames(self, message_table):
        """过滤后只跟踪可见帧的变化"""
        # 全量数据有6帧，但过滤后只看3帧
        full_frame_index = _make_frame_index(
            [0, 1, 2, 3, 4, 5],
            [i * 0.001 for i in range(6)],
            [0x112] * 6,
            [8] * 6,
        )
        raw_data = np.zeros((6, 8), dtype=np.uint8)
        for i in range(6):
            raw_data[i] = [0x00] * 8
        # 帧3的 byte[0] 变化
        raw_data[3][0] = 0xFF

        # 过滤后只看帧 0, 3, 5
        filtered = full_frame_index.iloc[[0, 3, 5]].reset_index(drop=True)

        message_table._frame_index = full_frame_index
        message_table._raw_data = raw_data
        message_table._filtered_index = filtered
        message_table._compute_byte_change_info()

        info = message_table._byte_change_info

        # 帧0: 首帧 byte[0]=999（无变化）
        assert info[0][0] == 999
        # 帧3: byte[0] 变化(0x00→0xFF) → 0
        assert info[3][0] == 0, (
            f"过滤后帧3 byte[0] 变化应为 0，实际={info[3][0]}"
        )
        # 帧5: byte[0] 再次变化(0xFF→0x00) → 0
        assert info[5][0] == 0, (
            f"过滤后帧5 byte[0] 再次变化应为 0，实际={info[5][0]}"
        )

    def test_empty_filtered_index(self, message_table):
        """空的过滤结果应产生空的变化信息"""
        message_table._filtered_index = None
        message_table._raw_data = np.zeros((1, 8), dtype=np.uint8)
        message_table._compute_byte_change_info()
        assert message_table._byte_change_info == {}

    def test_multiple_byte_changes_in_same_frame(self, message_table):
        """同一帧多个字节同时变化"""
        n = 3
        frame_index = _make_frame_index(
            list(range(n)),
            [i * 0.001 for i in range(n)],
            [0x112] * n,
            [8] * n,
        )
        raw_data = np.zeros((n, 8), dtype=np.uint8)
        raw_data[0] = [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
        raw_data[1] = [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
        # 帧2: byte[0], byte[3], byte[7] 同时变化
        raw_data[2] = [0xFF, 0x00, 0x00, 0xAA, 0x00, 0x00, 0x00, 0xBB]

        message_table._frame_index = frame_index
        message_table._raw_data = raw_data
        message_table._filtered_index = frame_index
        message_table._compute_byte_change_info()

        info = message_table._byte_change_info

        # 帧0、帧1所有字节从未变化 → 999
        for fid in [0, 1]:
            for b in range(8):
                assert info[fid][b] == 999, (
                    f"帧{fid} 字节{b} 应为 999（从未变化），实际={info[fid][b]}"
                )

        # 帧2 变化的字节应重置为 0
        assert info[2][0] == 0
        assert info[2][3] == 0
        assert info[2][7] == 0

        # 帧2 未变化的字节从未变化 → 999
        assert info[2][1] == 999
        assert info[2][2] == 999
        assert info[2][4] == 999
        assert info[2][5] == 999
        assert info[2][6] == 999

    def test_scenario_from_spec(self, message_table):
        """验证需求文档中的具体场景

        Frames 0-3964: Data = 00 00 00 00 00 00 80 00
        Frame 3967:    Data = 00 00 00 00 01 00 80 00 (byte[4] changed)
        简化为10帧模拟：前4帧不变，帧4变化，后续帧保持
        """
        n = 10
        frame_index = _make_frame_index(
            list(range(n)),
            [i * 0.001 for i in range(n)],
            [0x112] * n,
            [8] * n,
        )
        raw_data = np.zeros((n, 8), dtype=np.uint8)
        for i in range(n):
            raw_data[i] = [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x80, 0x00]
        # 帧4开始 byte[4] = 0x01
        for i in range(4, n):
            raw_data[i][4] = 0x01

        message_table._frame_index = frame_index
        message_table._raw_data = raw_data
        message_table._filtered_index = frame_index
        message_table._compute_byte_change_info()

        info = message_table._byte_change_info

        # 帧0-3: byte[4] 从未变化 → 999
        for fid in range(4):
            assert info[fid][4] == 999, (
                f"帧{fid} byte[4] 应为 999（从未变化），实际={info[fid][4]}"
            )

        # 帧4: byte[4] 变化 → 0
        assert info[4][4] == 0

        # 帧5-9: byte[4] 距帧4递增
        for fid in range(5, n):
            expected = fid - 4
            assert info[fid][4] == expected, (
                f"帧{fid} byte[4] 应距变化 {expected} 帧，"
                f"实际={info[fid][4]}"
            )

        # byte[6] = 0x80 始终未变 → 999
        for fid in range(n):
            assert info[fid][6] == 999, (
                f"帧{fid} byte[6] 应为 999（从未变化），实际={info[fid][6]}"
            )


class TestHexDataDelegateColorInterpolation:
    """测试 HexDataDelegate 的颜色插值逻辑"""

    def test_color_at_zero_is_highlight(self):
        """frames_since_change=0 时应返回高亮色"""
        delegate = HexDataDelegate()
        color = delegate._get_byte_color(0)
        assert color.red() == HexDataDelegate.HIGHLIGHT_COLOR.red()
        assert color.green() == HexDataDelegate.HIGHLIGHT_COLOR.green()
        assert color.blue() == HexDataDelegate.HIGHLIGHT_COLOR.blue()

    def test_color_at_fade_frames_is_normal(self):
        """frames_since_change >= FADE_FRAMES 时应返回正常色"""
        delegate = HexDataDelegate()
        color = delegate._get_byte_color(HexDataDelegate.FADE_FRAMES)
        assert color.red() == HexDataDelegate.NORMAL_COLOR.red()
        assert color.green() == HexDataDelegate.NORMAL_COLOR.green()
        assert color.blue() == HexDataDelegate.NORMAL_COLOR.blue()

        # 超过 FADE_FRAMES 也应是正常色
        color2 = delegate._get_byte_color(HexDataDelegate.FADE_FRAMES + 50)
        assert color2.red() == HexDataDelegate.NORMAL_COLOR.red()

    def test_color_midpoint_is_interpolated(self):
        """中间帧的颜色应在高亮色和正常色之间"""
        delegate = HexDataDelegate()
        mid = HexDataDelegate.FADE_FRAMES // 2
        color = delegate._get_byte_color(mid)

        # R 分量：HIGHLIGHT=0xFF=255, NORMAL=0xE0=224
        # 中间值应约在 239 附近
        hl_r = HexDataDelegate.HIGHLIGHT_COLOR.red()
        nm_r = HexDataDelegate.NORMAL_COLOR.red()
        expected_r = int(hl_r + (nm_r - hl_r) * 0.5)
        assert abs(color.red() - expected_r) <= 1

        # G 分量：HIGHLIGHT=0x6B=107, NORMAL=0xE0=224
        hl_g = HexDataDelegate.HIGHLIGHT_COLOR.green()
        nm_g = HexDataDelegate.NORMAL_COLOR.green()
        expected_g = int(hl_g + (nm_g - hl_g) * 0.5)
        assert abs(color.green() - expected_g) <= 1

    def test_bold_within_threshold(self):
        """frames_since_change < BOLD_FRAMES 时应加粗"""
        delegate = HexDataDelegate()
        assert delegate._is_bold(0) is True
        assert delegate._is_bold(HexDataDelegate.BOLD_FRAMES - 1) is True
        assert delegate._is_bold(HexDataDelegate.BOLD_FRAMES) is False
        assert delegate._is_bold(HexDataDelegate.BOLD_FRAMES + 10) is False

    def test_update_change_info(self):
        """update_change_info 应正确更新内部状态"""
        delegate = HexDataDelegate()
        assert delegate._byte_change_info == {}

        new_info = {0: {0: 0, 1: 5}, 1: {0: 3}}
        delegate.update_change_info(new_info)
        assert delegate._byte_change_info == new_info


class TestIntegrationWithPopulateTable:
    """验证 _populate_table 调用后委托自动更新"""

    def test_populate_table_updates_delegate(self, message_table):
        """_populate_table 完成后，委托应持有正确的变化信息"""
        n = 5
        frame_index = _make_frame_index(
            list(range(n)),
            [i * 0.001 for i in range(n)],
            [0x112] * n,
            [8] * n,
        )
        raw_data = np.zeros((n, 8), dtype=np.uint8)
        for i in range(n):
            raw_data[i] = [0x00] * 8
        # 帧3 byte[2] 变化
        raw_data[3][2] = 0xAB

        message_table.set_data(frame_index, raw_data, [], "")

        # 验证委托已更新
        delegate_info = message_table._hex_delegate._byte_change_info
        assert 3 in delegate_info, "frame_id=3 应在委托的变化信息中"
        assert delegate_info[3][2] == 0, "byte[2] 在帧3变化应为 0"
        # 帧4 byte[2] 从 0xAB 变回 0x00，再次变化 → 0
        assert delegate_info[4][2] == 0, "byte[2] 在帧4再次变化应为 0"

    def test_filter_recomputes_change_info(self, message_table):
        """过滤后应重新计算变化信息"""
        n = 6
        frame_index = _make_frame_index(
            list(range(n)),
            [i * 0.001 for i in range(n)],
            [0x112, 0x220, 0x112, 0x220, 0x112, 0x220],
            [8] * n,
        )
        raw_data = np.zeros((n, 8), dtype=np.uint8)
        for i in range(n):
            raw_data[i] = [0x00] * 8
        # 0x112 帧2 byte[0] 变化
        raw_data[2][0] = 0xFF
        # 0x220 帧3 byte[0] 变化
        raw_data[3][0] = 0xEE

        message_table.set_data(frame_index, raw_data, [], "")

        # 应用 ID 过滤只看 0x112
        message_table._id_filter.setCurrentText("0x112")
        message_table._apply_filter()

        delegate_info = message_table._hex_delegate._byte_change_info
        # 过滤后只有 0x112 的帧 (fid=0,2,4)
        assert 0 in delegate_info
        assert 2 in delegate_info
        assert 4 in delegate_info
        # 0x220 的帧不应在变化信息中
        assert 1 not in delegate_info
        assert 3 not in delegate_info

        # 0x112 帧0 byte[0] 首帧 → 999（无变化）
        assert delegate_info[0][0] == 999
        # 0x112 帧2 byte[0] 变化 → 0
        assert delegate_info[2][0] == 0
        # 0x112 帧4 byte[0] 再次变化（0xFF→0x00）→ 0
        assert delegate_info[4][0] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
