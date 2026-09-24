# tests/test_plot_hover_opt.py
"""曲线图悬停优化（v1.3.x）offscreen 回归测试。

覆盖的用户需求：
1）悬停提示窗只有一个、且不在相邻两点间抖动 → 切换滞后（HOVER_SWITCH_PX）；
2）点得近时两个固定提示窗不重叠 → 候选方位排入「遮挡打分」；
3）左键点到固定提示窗也能关闭（与右键等价）；
4）同一数据点上鼠标微动不重绘提示窗（状态幂等，治「一直闪」）；
5）每次 blit 都必须带上悬停提示窗（否则坐标文本与提示窗互相擦除 → 闪烁）；
6）离开曲线后提示窗含箭头整体隐藏（治「残留小箭头」）。
"""
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)

from core.can_data import DecodedSignal
from widgets.plot_widget import PlotWidget, HOVER_SWITCH_PX


class _Ev:
    """伪造 matplotlib MouseEvent。"""

    def __init__(self, x, y, xdata, ydata, inaxes, button=1, name=None):
        self.x = x
        self.y = y
        self.xdata = xdata
        self.ydata = ydata
        self.inaxes = inaxes
        self.button = button
        self.name = name


def _mk_widget(n=200):
    w = PlotWidget()
    x = np.linspace(0.0, 10.0, n)
    y = np.sin(x)
    sig = DecodedSignal("M", "Sig", x, y)
    w.plot_signals([sig])          # 内部走 _redraw → _draw_shared
    line = next(iter(w._line_sig_name))
    return w, line.axes, line, x, y


def test_hover_hysteresis_no_jitter():
    """两相邻点之间微小移动不应切换命中的点。"""
    print("[1] 悬停切换滞后（不抖动）...")
    w, ax, line, x, y = _mk_widget()
    px, py = ax.transData.transform((float(x[10]), float(y[10])))
    ev = _Ev(px, py, float(x[10]), float(y[10]), ax)
    first = w._nearest_point(ev)
    assert first is not None, "初始位移应命中数据点"
    _, point0 = first
    assert point0 == (float(x[10]), float(y[10])), f"首个命中点异常: {point0}"

    # 在锁定点 HOVER_SWITCH_PX 内反复抖动 → 必须始终返回同一个点
    for dx in (-1, 1, -2, 2, -3, 3):
        ev2 = _Ev(px + dx, py + (1 if dx > 0 else -1),
                  float(x[10]), float(y[10]), ax)
        got = w._nearest_point(ev2)
        assert got is not None, "滞后范围内不应丢命中"
        assert got[1] == point0, (
            f"滞后范围内不应换点: dx={dx} got={got[1]} want={point0}")
    print(f"    OK: HOVER_SWITCH_PX={HOVER_SWITCH_PX} 内 6 次抖动均未换点")

    # 明确移出阈值 → 允许切换。
    # 注意：_nearest_point 用 event.xdata 搜索，移动鼠标时 xdata 必须同步更新，
    # 否则仍会命中同一个点（这里改为真的移到 x[60] 去）。
    tx = float(x[60])
    px3, py3 = ax.transData.transform((tx, float(y[60])))
    ev3 = _Ev(px3, py3, tx, float(y[60]), ax)
    got3 = w._nearest_point(ev3)
    assert got3 is not None
    assert got3[1] == (tx, float(y[60])), (
        f"远离后应切到 x[60] 附近的点, got={got3[1]}")
    print("    OK: 远离后正常切换")
    w.close()


def test_pinned_annotations_do_not_overlap():
    """两个很近的数据点固定后，提示窗不应大幅重叠。"""
    print("[2] 相近数据点的提示窗避让 ...")
    w, ax, line, x, y = _mk_widget()
    i = 100
    a = (float(x[i]), float(y[i]))
    bpt = (float(x[i + 1]), float(y[i + 1]))

    for pt in (a, bpt):
        text = f"M.Sig\n值: {pt[1]:.4f}\n时间: {pt[0]:.4f}s"
        ann = w._create_point_annotation(ax, text, pt[0], pt[1], "#4fc3f7",
                                         facecolor="#2b2b2b")
        ann._is_pinned = True
        w._pinned_annotations.setdefault(line, []).append(ann)
    w._pinned_lines.add(line)
    w._canvas.draw()

    renderer = w._canvas.get_renderer()
    boxes = []
    for lst in w._pinned_annotations.values():
        for ann in lst:
            boxes.append(w._ann_box_px(ann, renderer))
    assert len(boxes) == 2, f"应有 2 个提示窗, got={len(boxes)}"

    (ax0, ay0, ax1, ay1), (bx0, by0, bx1, by1) = boxes
    ix = min(ax1, bx1) - max(ax0, bx0)
    iy = min(ay1, by1) - max(ay0, by0)
    inter = max(0.0, ix) * max(0.0, iy)
    a_area = (ax1 - ax0) * (ay1 - ay0)
    ratio = inter / max(a_area, 1e-9)
    print(f"    重叠占单框面积比例 = {ratio:.1%}")
    assert ratio < 0.55, f"提示窗重叠过多: {ratio:.1%}"
    print("    OK: 相近固定提示窗已有效错开")
    w.close()


def test_three_close_annotations_no_overlap():
    """三个相邻采样点都固定时，三窗两两都不得重叠。

    这个用例是回归防线：曾出现「第 3 个窗与第 1 个重叠 89%」——根因是
    避让打分用了含箭头的联合 bbox（相邻点的箭头指向同一片区域，恒判重叠），
    或 bbox_patch 在 figure 未 draw() 时返回垃圾值。
    """
    print("[2b] 三个相邻点的三窗互不重叠 ...")
    w, ax, line, x, y = _mk_widget(n=400)
    w.resize(1200, 700)
    ax.figure.canvas.draw()
    for i in (200, 201, 202):
        pt = (float(x[i]), float(y[i]))
        w._toggle_pin(line, _Ev(0, 0, pt[0], pt[1], ax))
    w._canvas.draw()

    boxes = [a._px_rect for a in w._pinned_annotations[line]]
    assert len(boxes) == 3, f"应有 3 个提示窗, got={len(boxes)}"
    import itertools
    for i, j in itertools.combinations(range(3), 2):
        A, B = boxes[i], boxes[j]
        ix = min(A[2], B[2]) - max(A[0], B[0])
        iy = min(A[3], B[3]) - max(A[1], B[1])
        inter = max(0.0, ix) * max(0.0, iy)
        area = (A[2] - A[0]) * (A[3] - A[1])
        ratio = inter / max(area, 1e-9)
        assert ratio < 0.3, f"窗{i+1} 与 窗{j+1} 重叠 {ratio:.1%}"
    print("    OK: 三窗两两重叠均 < 30%")
    w.close()


def test_left_click_closes_pinned_ann():
    """左键点在固定提示窗上应关闭它，且线宽恢复。"""
    print("[3] 左键点窗关闭 ...")
    w, ax, line, x, y = _mk_widget()
    i = 100
    pt = (float(x[i]), float(y[i]))
    w._toggle_pin(line, _Ev(0, 0, pt[0], pt[1], ax))
    w._canvas.draw()
    assert line in w._pinned_annotations and len(w._pinned_annotations[line]) == 1

    ann = w._pinned_annotations[line][0]
    # ⚠️ 必须取「纯框体」中心，不能用 ann.get_window_extent()——后者是
    #    文本框+箭头的联合 bbox（含箭头扫过的空白），其中心常常落在框外。
    rect = w._ann_box_px(ann, w._canvas.get_renderer())
    cx, cy = (rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0

    w._on_click(_Ev(cx, cy, pt[0], pt[1], ax, button=1))
    w._canvas.draw()

    assert line not in w._pinned_annotations, "左键点窗后该固定窗应被关闭"
    assert line not in w._pinned_lines, "最后一个窗关闭后应恢复非固定态"
    assert abs(line.get_linewidth() - w._original_linewidth) < 1e-9, \
        "线宽应恢复为原始值"
    print("    OK: 左键关闭固定窗 + 线宽恢复")
    w.close()


def test_left_click_empty_still_pins():
    """左键点在空白处仍应走「钉提示窗」，功能未被破坏。"""
    print("[4] 左键空白处仍可钉窗（回归）...")
    w, ax, line, x, y = _mk_widget()
    i = 50
    pt = (float(x[i]), float(y[i]))
    px, py = ax.transData.transform(pt)

    w._on_click(_Ev(px, py, pt[0], pt[1], ax, button=1))
    w._on_release(_Ev(px, py, pt[0], pt[1], ax, button=1))
    w._canvas.draw()
    assert line in w._pinned_annotations, "空白处左键单击应钉出提示窗"
    print("    OK: 常规钉窗路径未受影响")
    w.close()


def test_right_click_close_still_works():
    """右键点窗关闭（原有行为）不应被破坏。"""
    print("[5] 右键点窗关闭（回归）...")
    w, ax, line, x, y = _mk_widget()
    pt = (float(x[80]), float(y[80]))
    w._toggle_pin(line, _Ev(0, 0, pt[0], pt[1], ax))
    w._canvas.draw()
    ann = w._pinned_annotations[line][0]
    rect = w._ann_box_px(ann, w._canvas.get_renderer())
    w._on_click(_Ev((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2,
                    pt[0], pt[1], ax, button=3))
    w._canvas.draw()
    assert line not in w._pinned_annotations, "右键点窗仍应关闭"
    print("    OK: 右键行为未受影响")
    w.close()


def test_hover_idempotent_no_redraw():
    """鼠标微动但未换点时，不得重建/重设提示窗（否则视觉上会"闪"）。

    用户原话：「只要鼠标移动一点点就不断地在鼠标附近位置的点一直闪悬浮窗，
    是不是鼠标没有移动到其他对应点上时，不重新绘制悬浮窗」。
    """
    print("[6] 同一点微动时不重绘提示窗（幂等）...")
    w, ax, line, x, y = _mk_widget(n=1200)
    w.resize(1200, 700)
    ax.figure.canvas.draw()

    calls = {"apply": 0, "choose": 0}

    def _wrap(name):
        orig = getattr(PlotWidget, name)

        def _inner(self, *a, **kw):
            calls["apply" if name == "_apply_highlight" else "choose"] += 1
            return orig(self, *a, **kw)
        return _inner

    # 线宽同样幂等：已加粗的曲线不再重复 set_linewidth（用户原话：
    # 「曲线加粗显示的也是，如果已经处于加粗显示的状态……就不用重新刷新」）
    calls["lw"] = 0
    orig_lw = line.set_linewidth

    def _spy_lw(v):
        calls["lw"] += 1
        return orig_lw(v)

    orig_apply = PlotWidget._apply_highlight
    orig_choose = PlotWidget._choose_annotation_offset
    PlotWidget._apply_highlight = _wrap("_apply_highlight")
    PlotWidget._choose_annotation_offset = _wrap("_choose_annotation_offset")
    line.set_linewidth = _spy_lw
    try:
        i = 600
        px, py = ax.transData.transform((float(x[i]), float(y[i])))
        for k in range(30):                      # 沿法向 ±0.4px 抖动
            dy = 0.4 * ((k % 3) - 1)
            w._on_mouse_move(_Ev(px, py + dy, float(x[i]), float(y[i]), ax))
    finally:
        PlotWidget._apply_highlight = orig_apply
        PlotWidget._choose_annotation_offset = orig_choose
        line.set_linewidth = orig_lw

    assert calls["apply"] == 1, f"同一点微动应只高亮 1 次, got={calls['apply']}"
    assert calls["choose"] == 1, \
        f"同一点微动不应重算避让, got={calls['choose']}"
    assert calls["lw"] == 1, \
        f"同一曲线已加粗后不应重复 set_linewidth, got={calls['lw']}"
    print(f"    30 帧微动 → _apply_highlight={calls['apply']}, "
          f"_choose_annotation_offset={calls['choose']}, "
          f"set_linewidth={calls['lw']}")
    w.close()


def test_blit_always_carries_hover_ann():
    """每次局部刷新都必须把悬停提示窗一并重画，否则会互相擦除（闪烁）。

    根因回顾：blit 是「restore 背景位图 → 只画给定 artist」，坐标文本那次
    blit 会把提示窗抹掉、提示窗那次又把坐标文本抹掉 → 鼠标一动就闪。
    """
    print("[7] blit 不得把提示窗擦掉（防闪烁回归）...")
    w, ax, line, x, y = _mk_widget(n=1200)
    w.resize(1200, 700)
    ax.figure.canvas.draw()

    seen = {"blit": 0, "with_ann": 0}
    orig_blit = PlotWidget._blit_artists

    def _spy(self, artists):
        lst = list(artists)
        merged = lst + [a for a in self._persistent_blit_artists()
                        if a not in lst]
        seen["blit"] += 1
        if self._hover_ann is not None and self._hover_ann in merged:
            seen["with_ann"] += 1
        return orig_blit(self, artists)

    PlotWidget._blit_artists = _spy
    try:
        i = 600
        px, py = ax.transData.transform((float(x[i]), float(y[i])))
        for k in range(20):
            w._on_mouse_move(_Ev(px + (k % 3), py, float(x[i]), float(y[i]), ax))
    finally:
        PlotWidget._blit_artists = orig_blit

    assert seen["blit"] > 0, "应有 blit 发生"
    assert seen["with_ann"] == seen["blit"], (
        f"{seen['blit'] - seen['with_ann']} 次 blit 未带提示窗 → 会被擦掉")
    print(f"    {seen['blit']} 次 blit 全部带上提示窗")
    w.close()


def test_hover_ann_hidden_after_leave():
    """鼠标离开曲线后，提示窗（含箭头）必须整体隐藏，不留残影。

    用户原话：「如果我鼠标已经离开曲线了，曲线点上还残留一个小箭头」。
    只 set_text("") 是不够的：文本为空时 matplotlib 仍会画 arrow_patch。
    """
    print("[8] 离开曲线后不残留箭头 ...")
    w, ax, line, x, y = _mk_widget(n=1200)
    w.resize(1200, 700)
    ax.figure.canvas.draw()

    i = 600
    px, py = ax.transData.transform((float(x[i]), float(y[i])))
    w._on_mouse_move(_Ev(px, py, float(x[i]), float(y[i]), ax))
    assert w._hover_ann.get_visible(), "悬停中提示窗应可见"
    assert w._hover_ann in w._persistent_blit_artists()

    yr = ax.get_ylim()[1] - ax.get_ylim()[0]
    w._on_mouse_move(_Ev(px, py + 250, float(x[i]),
                         float(y[i]) + yr * 0.35, ax))
    assert not w._hover_ann.get_visible(), "离开后提示窗必须整体隐藏（含箭头）"
    assert w._hover_ann not in w._persistent_blit_artists(), \
        "隐藏后不应再参与 blit（否则箭头会被画回来）"
    # set_data([], []) 后 get_xdata() 返回的是 list（不是 ndarray），故用 len()
    assert len(w._hover_point.get_xdata()) == 0, "高亮点数据应清空"

    # 重新悬停回来应能恢复可见
    w._on_mouse_move(_Ev(px, py, float(x[i]), float(y[i]), ax))
    assert w._hover_ann.get_visible(), "重新命中后应恢复可见"
    print("    OK: 离开隐藏 / 回来恢复")
    w.close()


if __name__ == "__main__":
    test_hover_hysteresis_no_jitter()
    test_pinned_annotations_do_not_overlap()
    test_three_close_annotations_no_overlap()
    test_left_click_closes_pinned_ann()
    test_left_click_empty_still_pins()
    test_right_click_close_still_works()
    test_hover_idempotent_no_redraw()
    test_blit_always_carries_hover_ann()
    test_hover_ann_hidden_after_leave()
    print("\nALL PASS")
