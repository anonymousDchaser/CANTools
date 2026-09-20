# tests/test_mark_groups.py
"""曲线图时间差标记「三组」改造的回归测试。

覆盖：最多 3 组 / 每组 2 根线、组内同色组间异色、每组带范围色带、Δt 提示框位于
绘图区**内部**且层级高于曲线（原先挂在绘图区外、被上层子图压住导致显示不全）、
拖动时色带与 Δt 文本实时跟随、放满自动退出标记模式、中途退出保留已放标记、
重绘（切模式/切显隐）后标记恢复、清除彻底。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PyQt5.QtWidgets import QApplication

from core.can_data import DecodedSignal
from widgets.plot_widget import (
    PlotWidget, MARK_GROUP_COLORS, MARK_MAX, MARK_LABEL_ZORDER,
)

_app = None


def get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


def _signal(msg, sig, t0, period, n=40):
    ts = np.array([t0 + i * period for i in range(n)], dtype=float)
    vals = np.array([np.sin(i * 0.3) * 10 + 5 for i in range(n)], dtype=float)
    return DecodedSignal(msg, sig, ts, vals)


@pytest.fixture
def pw():
    get_app()
    w = PlotWidget()
    w.resize(900, 620)
    w.plot_signals([_signal("MsgA", "S1", 0.0, 0.05), _signal("MsgB", "S2", 0.0, 0.02)])
    QApplication.processEvents()
    yield w
    w.deleteLater()


@pytest.fixture
def pw3(pw):
    """已放满三组（6 根线）的图。"""
    for t in (0.20, 0.35, 0.55, 0.68, 0.80, 1.05):
        pw._add_mark(t)
    QApplication.processEvents()
    return pw


class TestGroups:
    def test_three_groups_from_six_marks(self, pw3):
        assert len(pw3._mark_points) == MARK_MAX == 6
        assert len(pw3._mark_groups()) == 3
        assert len(pw3._mark_delta_anns) == 3

    def test_band_per_group_per_axes(self, pw3):
        assert len(pw3._mark_spans) == 3
        n_axes = len(pw3._fig.axes)
        assert all(len(spans) == n_axes for spans in pw3._mark_spans)

    def test_delta_text_has_group_index(self, pw3):
        texts = [a.get_text() for a in pw3._mark_delta_anns]
        assert texts[0].startswith("Δt1 = 0.1500")
        assert texts[1].startswith("Δt2 = 0.1300")
        assert texts[2].startswith("Δt3 = 0.2500")

    def test_same_color_in_group_diff_between_groups(self, pw3):
        colors = [pw3._mark_artists[k][0].get_color() for k in range(MARK_MAX)]
        assert colors[0] == colors[1] and colors[2] == colors[3] and colors[4] == colors[5]
        assert len({colors[0], colors[2], colors[4]}) == 3
        assert [colors[0], colors[2], colors[4]] == list(MARK_GROUP_COLORS)

    def test_incomplete_group_has_no_band(self, pw):
        pw._add_mark(0.20)
        assert not pw._mark_spans and not pw._mark_delta_anns
        assert len(pw._mark_artists) == 1
        pw._add_mark(0.40)
        assert len(pw._mark_spans) == 1 and len(pw._mark_delta_anns) == 1


class TestDeltaLabelPlacement:
    """Δt 提示框必须完整落在绘图区内、且层级高于曲线（回归：曾显示不全）。"""

    def test_label_inside_axes(self, pw3):
        pw3._fig.canvas.draw()
        renderer = pw3._canvas.get_renderer()
        ax_box = pw3._fig.axes[0].get_window_extent()
        for ann in pw3._mark_delta_anns:
            box = ann.get_window_extent(renderer)
            assert box.x0 >= ax_box.x0 - 1 and box.x1 <= ax_box.x1 + 1
            assert box.y0 >= ax_box.y0 - 1 and box.y1 <= ax_box.y1 + 1

    def test_labels_staggered_by_group(self, pw3):
        pw3._fig.canvas.draw()
        renderer = pw3._canvas.get_renderer()
        ax_box = pw3._fig.axes[0].get_window_extent()
        tops = [(a.get_window_extent(renderer).y1 - ax_box.y0) / ax_box.height
                for a in pw3._mark_delta_anns]
        assert tops[0] > tops[1] > tops[2], "三组提示框应向下错开，避免互相压住"

    def test_label_zorder_above_curves(self, pw3):
        assert all(a.get_zorder() >= MARK_LABEL_ZORDER for a in pw3._mark_delta_anns)
        curve_z = [ln.get_zorder() for ax in pw3._fig.axes for ln in ax.get_lines()
                   if not getattr(ln, "_is_time_mark", False)]
        assert MARK_LABEL_ZORDER > max(curve_z)


    def test_label_inside_axes_when_canvas_is_short(self):
        """绘图区很矮时，三组提示框仍要完整落在区内（间距自适应压缩）。"""
        get_app()
        w = PlotWidget()
        w.resize(700, 260)
        w.show()                          # offscreen 下需 show 后 resize 才真正生效
        QApplication.processEvents()
        w.plot_signals([_signal("MsgA", "S1", 0.0, 0.05),
                        _signal("MsgB", "S2", 0.0, 0.02)])
        w.resize(700, 260)
        QApplication.processEvents()
        for t in (0.20, 0.35, 0.55, 0.68, 0.80, 1.05):
            w._add_mark(t)
        QApplication.processEvents()

        w._fig.canvas.draw()
        renderer = w._canvas.get_renderer()
        ax_box = w._fig.axes[0].get_window_extent()
        assert ax_box.height < 120, "本用例需要足够矮的绘图区，实际 %.1f px" % ax_box.height

        boxes = [a.get_window_extent(renderer) for a in w._mark_delta_anns]
        for i, bb in enumerate(boxes):
            assert bb.y0 >= ax_box.y0 - 1 and bb.y1 <= ax_box.y1 + 1, \
                "第 %d 组提示框超出绘图区上下边界" % (i + 1)
            assert bb.x0 >= ax_box.x0 - 1 and bb.x1 <= ax_box.x1 + 1, \
                "第 %d 组提示框超出绘图区左右边界" % (i + 1)
        assert boxes[0].y0 >= boxes[1].y1 - 1, "第 1、2 组提示框重叠"
        assert boxes[1].y0 >= boxes[2].y1 - 1, "第 2、3 组提示框重叠"
        w.deleteLater()


class TestDragFollow:
    def test_band_and_text_follow_drag(self, pw3):
        before = (pw3._mark_spans[0][0].get_x(), pw3._mark_spans[0][0].get_width())
        pw3._move_mark(0, 0.10)
        QApplication.processEvents()
        span = pw3._mark_spans[0][0]
        assert (span.get_x(), span.get_width()) != before
        assert abs(span.get_x() - 0.10) < 1e-9
        assert pw3._mark_delta_anns[0].get_text().startswith("Δt1 = 0.2500")
        assert abs(float(pw3._mark_artists[0][0].get_xdata()[0]) - 0.10) < 1e-9

    def test_drag_only_affects_its_own_group(self, pw3):
        pw3._move_mark(0, 0.10)
        QApplication.processEvents()
        g0 = (pw3._mark_spans[0][0].get_x(), pw3._mark_spans[0][0].get_width())
        g1 = (pw3._mark_spans[1][0].get_x(), pw3._mark_spans[1][0].get_width())
        pw3._move_mark(3, 0.60)          # 第 2 组的第二根线
        QApplication.processEvents()
        assert (pw3._mark_spans[0][0].get_x(), pw3._mark_spans[0][0].get_width()) == g0
        assert (pw3._mark_spans[1][0].get_x(), pw3._mark_spans[1][0].get_width()) != g1
        assert pw3._mark_delta_anns[1].get_text().startswith("Δt2 = 0.0500")
        assert pw3._mark_delta_anns[0].get_text().startswith("Δt1 = 0.2500")


class TestModeAndLifecycle:
    def test_mode_keeps_marks_and_fills(self, pw):
        pw._mark_btn.setChecked(True)
        pw._toggle_mark_mode()
        assert pw._mark_mode is True
        pw._add_mark(0.10)
        pw._add_mark(0.20)
        pw._mark_btn.setChecked(False)
        pw._toggle_mark_mode()            # 中途退出
        assert len(pw._mark_points) == 2, "中途退出不应清空已放标记"
        assert not pw._preview_artists
        pw._mark_btn.setChecked(True)
        pw._toggle_mark_mode()            # 再次进入应能接着标
        assert len(pw._mark_points) == 2

    def test_redraw_restores_marks(self, pw3):
        pw3._redraw()
        QApplication.processEvents()
        assert len(pw3._mark_artists) == 6
        assert len(pw3._mark_spans) == 3
        assert len(pw3._mark_delta_anns) == 3

    def test_clear_removes_everything(self, pw3):
        pw3._clear_marks()
        QApplication.processEvents()
        assert not pw3._mark_artists
        assert not pw3._mark_spans
        assert not pw3._mark_delta_anns
        leftover = [c for ax in pw3._fig.axes for c in ax.get_children()
                    if getattr(c, "_is_time_mark", False)]
        assert not leftover, "坐标轴上不应残留标记 artist"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
