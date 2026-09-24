# tests/test_plot_hover_opt.py
"""曲线图悬停优化（v1.3.x）offscreen 回归测试。

覆盖的用户需求：
1）悬停提示窗只有一个、且不在相邻两点间抖动 → 切换滞后（HOVER_SWITCH_PX）；
2）点得近时两个固定提示窗不重叠 → 候选方位排入「遮挡打分」；
3）左键点到固定提示窗也能关闭（与右键等价）；
4）同一数据点上鼠标微动不重绘提示窗（状态幂等，治「一直闪」）；
5）每次 blit 都必须带上悬停提示窗（否则坐标文本与提示窗互相擦除 → 闪烁）；
6）离开曲线后提示窗含箭头整体隐藏（治「残留小箭头」）；
7）捕获背景位图前必须隐藏临时图层（治「第一个提示窗永远不消失」的鬼影，
   含拖动标记线时的同类缺陷）；
8）点击钉住的数据点必须与悬停窗显示的是同一个点（治「窗在 A、点下去钉在 B」）；
9）同一个点只保留一个提示窗（悬停窗不叠加、重复点击不堆窗）。
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


def _buf_to_arr(buf):
    """Agg 缓冲（memoryview / BytesIO）→ 一维 uint8 ndarray。"""
    if hasattr(buf, "getvalue"):
        buf = buf.getvalue()
    return np.frombuffer(buf, dtype=np.uint8).copy()


def _mk_widget(n=200):
    w = PlotWidget()
    x = np.linspace(0.0, 10.0, n)
    y = np.sin(x)
    sig = DecodedSignal("M", "Sig", x, y)
    w.plot_signals([sig])          # 内部走 _redraw → _draw_shared
    line = next(iter(w._line_sig_name))
    return w, line.axes, line, x, y


def _mk_sparse(n=41):
    """稀疏采样 + 已 resize：点间距大，便于构造「鼠标偏 1px 就跨到下一个点」。"""
    w = PlotWidget()
    x = np.arange(n, dtype=float)
    y = 10.0 * np.sin(x / 6.0)
    w.plot_signals([DecodedSignal("M", "Sig", x, y)])
    line = next(iter(w._line_sig_name))
    ax = line.axes
    w.resize(1200, 700)
    ax.figure.canvas.draw()
    return w, ax, line, x, y


def _ev_at_px(px, py, ax, button=1):
    """按像素位置造事件：xdata/ydata 由像素反算，与真实鼠标事件一致。"""
    xd, yd = ax.transData.inverted().transform((px, py))
    return _Ev(px, py, float(xd), float(yd), ax, button)


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


def test_bg_cache_excludes_hover_artists():
    """捕获背景位图时必须先隐藏临时图层，否则它们会被烙进背景 → 鬼影。

    用户原话：「鼠标在曲线上滑动，遇到第一个点会显示提示窗，然后继续滑动，
    遇到新的点会出现新的提示窗，但是第一个提示窗不会消失」。

    根因：`_apply_highlight` 是**先**写好提示窗文本/位置并置为可见，**之后**才
    走到 `_blit_artists` → `_ensure_bg_cache`。缓存缺失时的 `canvas.draw()`
    就把第一个提示窗一起画进了背景位图；此后每次 `restore_region` 都把它贴回来，
    而同一曲线内换点并不失效缓存 → 它永远不走。
    """
    print("[9] 背景位图不得烙入临时图层（治「第一个提示窗不消失」）...")
    w, ax, line, x, y = _mk_widget(n=1200)
    w.resize(1200, 700)
    ax.figure.canvas.draw()

    # 注意：不能在 copy_from_bbox 处用 _transient_blit_artists() 判断 —— 该函数
    # 按「当前可见」过滤，而修复生效时它们此刻**已经被隐藏**，返回空列表，
    # 无法区分「本来就不存在」与「被隐藏」两种情形。故在 canvas.draw() 时刻
    # 直接检查那几个确定的 artist 对象。
    draws = []
    orig_draw = w._canvas.draw

    def _spy_draw():
        txt = w._coord_texts.get(id(ax))
        draws.append((
            None if w._hover_ann is None else w._hover_ann.get_visible(),
            None if w._hover_point is None else w._hover_point.get_visible(),
            None if txt is None else txt.get_visible(),
        ))
        return orig_draw()

    w._canvas.draw = _spy_draw
    try:
        i = 600
        px, py = ax.transData.transform((float(x[i]), float(y[i])))
        w._on_mouse_move(_Ev(px, py, float(x[i]), float(y[i]), ax))
    finally:
        w._canvas.draw = orig_draw

    real = [d for d in draws if d[0] is not None]
    assert real, "悬停应触发一次背景位图捕获（canvas.draw 未被调用）"
    bad = [d for d in real if d[0] or d[1] or d[2]]
    assert not bad, (
        f"捕获背景时仍有临时图层可见 (悬停框,高亮点,坐标文本)={bad} "
        f"→ 会被烙进背景位图，之后每次 blit 都贴回来")

    # 像素级证明：把临时图层全部隐藏后重绘，应与捕获到的背景逐字节一致。
    # 若提示窗被烙进去，这里必然出现成片差异像素。
    for art in w._transient_blit_artists():
        art.set_visible(False)
    w._canvas.draw()
    ref = _buf_to_arr(w._canvas.copy_from_bbox(w._fig.bbox))
    snap = _buf_to_arr(w._bg_cache)
    assert snap.shape == ref.shape, \
        f"缓冲尺寸不一致: {snap.shape} vs {ref.shape}"
    diff = int(np.count_nonzero(snap != ref))
    assert diff == 0, \
        f"背景位图与「干净重绘」不一致：{diff} 字节被污染（提示窗被烙进背景）"
    print(f"    OK: 捕获时 {len(real)} 次绘制均已隐藏提示窗，"
          f"背景与干净重绘逐字节一致")
    w.close()


def test_bg_cache_excludes_dragged_marks():
    """拖动标记线时，被拖的竖线/色带/Δt 框同样不得烙进背景位图。

    同类缺陷：`_add_mark` 置空缓存后，首次 `_move_mark` 的 blit 会顺手捕获
    背景，若不排除这批 artist，拖动后原地会留下一套不动的鬼影。
    """
    print("[10] 拖动标记线不得留下原地鬼影 ...")
    w, ax, line, x, y = _mk_widget(n=1200)
    w.resize(1200, 700)
    ax.figure.canvas.draw()

    w._add_mark(float(x[300]))
    w._add_mark(float(x[400]))

    watch = list(w._mark_artists[0]) if w._mark_artists else []
    if w._mark_spans and w._mark_spans[0]:
        watch.extend(w._mark_spans[0])
    if w._mark_delta_anns and w._mark_delta_anns[0] is not None:
        watch.append(w._mark_delta_anns[0])
    assert watch, "应已生成标记线（可能还有色带/Δt 框）"

    snap = {}

    def _grab(buf):
        snap["vis"] = [(a, a.get_visible()) for a in watch]
        return buf

    # 复现 _add_mark 之后的状态：缓存为空，本次 blit 必然捕获背景
    w._invalidate_bg_cache()
    orig_cfb = w._canvas.copy_from_bbox
    w._canvas.copy_from_bbox = lambda bbox: _grab(orig_cfb(bbox))
    try:
        w._move_mark(0, float(x[250]))
    finally:
        w._canvas.copy_from_bbox = orig_cfb

    assert snap, "拖动标记应触发一次背景位图捕获"
    leaked = [a for a, v in snap["vis"] if v]
    assert not leaked, \
        f"捕获背景时有 {len(leaked)} 个标记图层仍可见 → 拖动后原地留鬼影"
    print(f"    OK: {len(watch)} 个标记图层在捕获时均已隐藏")
    w.close()


def test_pin_snaps_to_hover_shown_point():
    """点击钉住的数据点必须与悬停窗里显示的是**同一个点**。

    用户原话：「当前曲线未点击过数据点，假设鼠标移动到数据点 A 时显示了提示窗，
    但是点击之后，提示窗会显示到数据点 A 的后一个点 B，只有第一个提示窗有问题」。

    根因：悬停带切换滞后（HOVER_SWITCH_PX），鼠标落在 A 右侧 1~14px 时窗里仍是
    A；而点击用 `searchsorted(event.xdata)` 取点，取到的是「第一个 x >= 鼠标 x
    的采样点」→ 只要鼠标偏到 A 右边就恒为 B。修复前 dx=1..12 全部不一致。
    """
    print("[11] 点击钉住点 == 悬停窗显示点 ...")
    bad = []
    for dx in (1, 2, 3, 4, 6, 8, 10, 12):
        w, ax, line, x, y = _mk_sparse()
        i = 20
        px, py = ax.transData.transform((float(x[i]), float(y[i])))
        ev = _ev_at_px(px + dx, py, ax)
        w._on_mouse_move(ev)
        assert w._hover_ann is not None and w._hover_ann.get_visible(), \
            f"dx={dx}: 悬停窗应可见"
        shown_x = float(w._hover_ann.xy[0])
        assert abs(shown_x - float(x[i])) < 1e-9, \
            f"dx={dx}: 滞后半径内悬停窗应仍显示 A, got={shown_x}"
        w._on_click(ev)
        w._on_release(ev)
        anns = w._pinned_annotations.get(line, [])
        assert anns, f"dx={dx}: 应钉出一个提示窗"
        if abs(float(anns[0].xy[0]) - shown_x) > 1e-9:
            bad.append((dx, shown_x, float(anns[0].xy[0])))
        w.close()
    assert not bad, f"点击钉住点与悬停窗显示点不一致 (dx, 窗显示, 实际钉住): {bad}"
    print("    8 个偏移量（1~12px）全部与悬停窗一致")
    w.close()


def test_same_point_single_annotation():
    """同一个点只应有一个提示窗：悬停窗不得与固定窗叠加，重复点击不叠加。

    用户原话：「同一个点点击应该只显示一个提示窗」。
    修复前实测：第 1 次点击后同时可见 hover + pinned 两个窗，第 2/3 次点击各再 +
    一个（2 → 3 → 4 个）。
    """
    print("[12] 同一点只保留一个提示窗 ...")
    w, ax, line, x, y = _mk_sparse()
    i = 20
    px, py = ax.transData.transform((float(x[i]), float(y[i])))
    for k in (1, 2, 3):
        ev = _ev_at_px(px, py, ax)
        w._on_mouse_move(ev)
        w._on_click(ev)
        w._on_release(ev)
        anns = w._pinned_annotations.get(line, [])
        assert len(anns) == 1, f"第{k}次点击后应有 1 个固定窗, got={len(anns)}"
        hover_visible = (w._hover_ann is not None
                         and w._hover_ann.get_visible())
        assert not hover_visible, f"第{k}次点击后悬停窗应已收起（同一点只留一个窗）"
    pt = (float(anns[0].xy[0]), float(anns[0].xy[1]))
    assert abs(pt[0] - float(x[i])) < 1e-9 and abs(pt[1] - float(y[i])) < 1e-9, \
        f"固定窗应钉在 (x[{i}], y[{i}]), got={pt}"
    print("    3 次点击均只有 1 个窗，且钉在原点上")
    w.close()


def test_pin_follows_hover_curve_with_two_curves():
    """两条曲线靠近时，点击同样应按**悬停窗所属的那条曲线**钉点。

    同类缺陷的另一半：`_find_nearest_line` 只看「点击位置离哪条曲线最近」，而
    悬停带滞后仍锁定在先前那条曲线上 → 会出现「窗在前一条曲线，点下去钉在后一条」。
    """
    print("[13] 双曲线：点击跟随悬停窗所属曲线 ...")
    w = PlotWidget()
    w.set_subplot_mode(False)           # 共享 Y 轴：两条曲线落在同一个 axes 里
    x = np.arange(41, dtype=float)
    y1 = 10.0 * np.sin(x / 6.0)
    y2 = y1 + 0.5                       # 与上一条仅差半个单位
    w.plot_signals([DecodedSignal("M", "S1", x, y1),
                    DecodedSignal("M", "S2", x, y2)])
    by_name = {name: ln for ln, name in w._line_sig_name.items()}
    assert len(by_name) == 2, f"应有 2 条曲线, got={sorted(by_name)}"
    line1, line2 = by_name["S1"], by_name["S2"]
    ax = line1.axes
    w.resize(1200, 700)
    ax.figure.canvas.draw()
    assert line2.axes is ax, "本用例要求两条曲线共用一个 axes（共享 Y 轴模式）"
    assert len([ln for ln in ax.get_lines() if len(ln.get_xdata())]) == 2, \
        "该 axes 内应有 2 条数据曲线（否则用例不构成判别场景）"

    i = 20
    px, py = ax.transData.transform((float(x[i]), float(y1[i])))
    w._on_mouse_move(_ev_at_px(px, py, ax))
    assert abs(float(w._hover_ann.xy[0]) - float(x[i])) < 1e-9, "应悬停到 S1 上"

    # 上移 10px：仍在锁定半径(14px)内 → 窗仍显示 S1 的点
    ev2 = _ev_at_px(px, py + 10, ax)
    w._on_mouse_move(ev2)
    assert w._hover_ann.get_visible(), "滞后半径内悬停窗应保持可见"
    # 判别性前提：此刻「离点击位置最近的曲线」已是 S2，若不沿用悬停窗就会钉错曲线
    assert w._find_nearest_line(ev2) is line2, \
        "构造前提不成立：本用例需要 _find_nearest_line 判成 S2"

    w._on_click(ev2)
    w._on_release(ev2)
    assert line1 in w._pinned_annotations, "应钉在悬停窗所属的 S1 上"
    assert line2 not in w._pinned_annotations, "不应钉到 S2 上"
    print("    OK: 钉到悬停窗所属曲线（而非离鼠标最近的曲线）")
    w.close()


def test_realtime_mode_hover_and_pin():
    """实时报文页（同一 PlotWidget 的实时模式）同样具备悬停 / 点击钉窗能力。

    实时监控页 `realtime_monitor_widget._plot` 与分析页 `main_window._plot_widget`
    是**同一个 PlotWidget 类**：悬停命中、blit 局部刷新、背景位图缓存、钉窗逻辑
    都只有一份实现，因此本文件里的修复对两页同时生效。这里在实时模式下端到端
    确认一次，避免「只在分析页验证过」的盲区。
    """
    print("[14] 实时模式：悬停与点击钉窗 ...")
    w = PlotWidget()
    key = (0x112, "TCU", "Speed")
    w.start_realtime([key])
    ts = np.arange(60, dtype=float) * 0.02
    vs = 10.0 * np.sin(ts * 3.0)
    for t_, v_ in zip(ts, vs):
        w.push_sample(key[0], key[1], key[2], float(t_), float(v_))
    w._rt_tick()

    line = w._rt_lines[key]
    ax = w._rt_axes[key]
    w.resize(1200, 700)
    ax.figure.canvas.draw()

    # 实时模式的左下角坐标文本由 _build_realtime 创建（与 _redraw 尾部的路径不同），
    # 也就是说实时页同样有「坐标文本 + 悬停窗」两个 blit 图层要合并，不是简化版。
    assert len(w._coord_texts) == len(w._fig.axes) == 1, \
        f"实时模式应为每个 axes 建一个坐标文本, got={len(w._coord_texts)}"
    i = 30
    px, py = ax.transData.transform((float(ts[i]), float(vs[i])))
    w._on_mouse_move(_ev_at_px(px, py, ax))
    assert w._coord_texts[id(ax)].get_text() != "", "实时模式悬停应刷新坐标文本"
    assert w._hover_ann is not None and w._hover_ann.get_visible(), \
        "实时模式下悬停应显示提示窗"
    assert w._hover_ann in w._persistent_blit_artists(), \
        "实时模式下提示窗也应参与 blit（否则会被背景位图擦掉）"
    shown = (float(w._hover_ann.xy[0]), float(w._hover_ann.xy[1]))

    w._on_click(_ev_at_px(px, py, ax))
    w._on_release(_ev_at_px(px, py, ax))
    anns = w._pinned_annotations.get(line, [])
    assert len(anns) == 1, f"实时模式下应钉出 1 个提示窗, got={len(anns)}"
    got = (float(anns[0].xy[0]), float(anns[0].xy[1]))
    assert abs(got[0] - shown[0]) < 1e-9 and abs(got[1] - shown[1]) < 1e-9, \
        f"钉住点应与悬停窗显示的一致: {got} vs {shown}"
    assert not w._hover_ann.get_visible(), "钉住后悬停窗应收起（同一点只留一个窗）"
    print("    OK: 实时模式悬停 + 点击钉住同一点 + 只留一个窗")
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
    test_bg_cache_excludes_hover_artists()
    test_bg_cache_excludes_dragged_marks()
    test_pin_snaps_to_hover_shown_point()
    test_same_point_single_annotation()
    test_pin_follows_hover_curve_with_two_curves()
    test_realtime_mode_hover_and_pin()
    print("\nALL PASS")
