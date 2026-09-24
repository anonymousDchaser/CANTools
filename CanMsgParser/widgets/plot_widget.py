# widgets/plot_widget.py
"""曲线图组件：matplotlib 嵌入 PyQt5，支持丰富的交互功能

功能特性：
- 共享Y轴 / 独立子图模式切换
- 滚轮缩放（X轴/Y轴/双轴）
- 拖拽平移
- 鼠标悬停高亮 + 数值注释
- 时间差标记模式
- LTTB 降采样优化大数据集渲染
- 专业深色主题样式
"""
import numpy as np
import matplotlib
import matplotlib.colors as mcolors
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QApplication
from PyQt5.QtCore import Qt, QTimer
from core.can_data import DecodedSignal
from utils.lttb import lttb_downsample
from utils.font_helper import UI_FONT_FAMILY, apply_matplotlib_fonts
from utils.ui_scale import dp

# ─── 中文字体支持（Windows / macOS / Linux 均正确渲染中文）───
# 原实现只列了 Windows 字体，macOS 上会回落到不含中文字形的 DejaVu Sans，
# 导致曲线图标题/图例显示为方框；改由 font_helper 按平台给出候选。
apply_matplotlib_fonts()

# 降采样阈值
DOWNSAMPLE_THRESHOLD = 10000

# 默认颜色列表 — 高对比度、色盲友好
COLORS = [
    "#4fc3f7", "#ff7043", "#66bb6a", "#ef5350", "#ab47bc",
    "#8d6e63", "#ec407a", "#78909c", "#d4e157", "#26c6da",
]

# ─── 时间差标记 ───
# 最多三组、每组两根线：同组两根线颜色一致，组与组之间用不同颜色区分
MARK_GROUP_COLORS = ["#4fc3f7", "#ff7043", "#ab47bc"]
MARK_PER_GROUP = 2        # 每组标记线数量（两根线界定一个时间区间）
MARK_GROUPS_MAX = 3       # 同时保留的组数上限
MARK_MAX = MARK_PER_GROUP * MARK_GROUPS_MAX   # 标记线总数上限
MARK_SNAP_PX = 12         # 自动捕捉：最近的采样点距鼠标不超过该像素数才吸附
MARK_HIT_PX = 8           # 拖动命中：按下位置距标记线不超过该像素数即进入拖动
MARK_BAND_ALPHA = 0.13    # 组内「范围标识」色带的透明度
MARK_LABEL_ZORDER = 12    # Δt 提示框层级：必须高于曲线与网格，否则会被压住显示不全
MARK_LABEL_TOP = 0.97     # 第 1 组 Δt 提示框在绘图区内的 y（axes 坐标，取不到像素时兜底）
MARK_LABEL_STEP = 0.085   # 组间错开距离（axes 坐标，取不到像素时兜底）
MARK_LABEL_TOP_PX = 6     # 第 1 组 Δt 提示框距绘图区顶边的像素距离
MARK_LABEL_STEP_PX = 20   # 相邻两组 Δt 提示框的像素间距（按像素换算，窗口矮时也不会叠压）
MARK_LABEL_BOX_PX = 18    # 单个 Δt 提示框的高度估计（空间不足时用它反推可用间距）

HOVER_ZORDER = 20         # 「数据点说明」框层级：图例默认 zorder=5、Δt 提示框 12，
                          # 必须高于它们，否则会被图例/标记框遮住
HOVER_OFFSET_PTS = 15     # 说明框相对数据点的初始偏移（points）
HOVER_FLIP_RATIO = 0.55   # 数据点在绘图区内的相对位置超过该比例时，说明框反向摆放

# ─── 悬停防抖（同一时刻只保留一个说明框，且不在相邻点间来回跳）───
HOVER_SWITCH_PX = 14      # 切换到相邻数据点所需的最小像素位移（切换滞后阈值）
HOVER_RELEASE_FACTOR = 1.8  # 离开当前点超过「阈值 × 该系数」才判定为「未命中」，
                            # 与切换阈值形成滞回：避免在阈值边界上反复开关

# ─── 提示窗（说明框）───
PIN_ANN_MAX = 3           # 同一条曲线上允许同时存在的「点击固定」提示窗数量
ANN_EDGE_MARGIN = 6       # 避让时框体距绘图区边界的最小留白（像素）
ANN_AVOID_SAMPLES = 240   # 遮挡测试时曲线抽稀到的点数（够用且远快于全量）
ANN_OVERLAP_PAD = 4       # 判定「压到已有提示窗」时向外扩的像素（避免紧贴）

# ─── Ctrl+滚轮缩放 ───
CTRL_ZOOM_Y_PAD = 0.05    # Ctrl 缩放时 Y 轴上下各留的余量比例，避免曲线紧贴上下边

# ─── 平移拖动 ───
PAN_CLICK_PX = 4          # 按下后累计位移小于该像素数视为「单击」而非拖动

# ─── 实时刷新合并频率 ───
RT_REFRESH_MS = 33        # 实时曲线合并刷新周期（约 30 FPS）

# ─── 降采样缓存 ───
_DS_MISS = object()       # 缓存未命中哨兵（区别于缓存值为 None）
_DS_CACHE_MAX = 64        # 缓存条目上限，超出整体清空

# ─── 深色图表主题配置（与设计系统一致） ───
_DARK_THEME_RC = {
    "figure.facecolor": "#1e1e2e",
    "axes.facecolor": "#1e1e2e",
    "axes.edgecolor": "#3a3a4e",
    "axes.labelcolor": "#e0e0e0",
    "text.color": "#e0e0e0",
    "xtick.color": "#9090a0",
    "ytick.color": "#9090a0",
    "grid.color": "#3a3a4e",
    "grid.linestyle": "--",
    "grid.alpha": 0.7,
    "lines.linewidth": 1.8,
    "legend.facecolor": "#252535",
    "legend.edgecolor": "#3a3a4e",
    "legend.fontsize": 9,
    "font.size": 10,
}


class PlotWidget(QWidget):
    """信号曲线图组件，带专业深色主题和丰富交互"""

    # ─── QSS 样式表（与设计系统一致） ───
    _QSS = """
        QWidget {
            background-color: #1e1e2e;
            color: #e0e0e0;
            font-family: %s;
            font-size: 13px;
        }
        QPushButton {
            background-color: #3a3a4e;
            color: #e0e0e0;
            border: 1px solid #4a4a5e;
            border-radius: 4px;
            padding: 6px 16px;
            min-height: 28px;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: #4a4a5e;
            border-color: #4fc3f7;
        }
        QPushButton:pressed {
            background-color: #2a2a3e;
        }
        QPushButton:checked {
            background-color: #4fc3f7;
            color: #1e1e2e;
            border-color: #4fc3f7;
        }
        QToolBar {
            background-color: #1e1e2e;
            border: none;
            spacing: 4px;
            padding: 2px;
        }
        QToolButton {
            background-color: #3a3a4e;
            color: #e0e0e0;
            border: 1px solid #4a4a5e;
            border-radius: 3px;
            padding: 4px 8px;
        }
        QToolButton:hover {
            background-color: #4a4a5e;
            border-color: #4fc3f7;
        }
    """ % UI_FONT_FAMILY

    def __init__(self, parent=None):
        super().__init__(parent)
        self._signals: list[DecodedSignal] = []
        self._subplot_mode = True   # True=独立子图, False=共享Y轴（Task #6：曲线图默认独立子图）
        self._mark_mode = False      # 时间差标记模式
        # 已放置的标记时间戳（扁平存放：每 MARK_PER_GROUP 个为一组）
        self._mark_points = []
        # 每根线的 artists：贯穿各子图的竖线 + 顶部时间标签
        self._mark_artists: list = []
        # 每组一条「范围标识」色带（每组在每个子图上各画一条，索引对齐组号）
        self._mark_spans: list = []
        # 每组的 Δt 提示框（索引对齐组号；组未放满时为空）
        self._mark_delta_anns: list = []
        self._dragging_mark: int = -1  # 正在拖动的标记序号（-1 = 未拖动，扁平索引）
        self._preview_artists: list = []  # 标记模式下的吸附预览线
        self._annotation = None      # 悬停注释框（复用同一 artist，只改文本/位置）
        self._highlighted_line = None
        self._original_linewidth = 1.8
        # Issue 5: DBC 值描述表 {sig_name: {int_val: "描述", ...}}
        self._value_descriptions: dict = {}
        # Issue 7: 点击固定高亮的曲线集合及其持久注释。
        # _pinned_annotations 为 {line: [ann, ...]}：同一条曲线最多 PIN_ANN_MAX 个
        # 提示窗（FIFO 淘汰最早的），因为同一条曲线上可能有多个想对比的时刻点。
        self._pinned_lines: set = set()
        self._pinned_annotations: dict = {}
        # blit 局部刷新：整幅背景的位图缓存。非 None 时表示可用，
        # 任何改变「背景内容」（轴范围 / 布局 / 曲线数据 / 标记）的操作都必须
        # 调用 _invalidate_bg_cache() 使其失效，否则 blit 会贴出过期画面。
        self._bg_cache = None
        self._blit_ann_pad = 0       # 预留：blit 区域外扩（当前整幅 blit，无需局部外扩）
        # 悬停高亮使用的可复用 artist（惰性创建，避免每帧 destroy+create）
        self._hover_point = None
        self._hover_ann = None
        self._hover_ax = None
        # 悬停「切换滞后」状态：锁定当前命中的数据点，鼠标未移出 HOVER_SWITCH_PX
        # 时不重新搜索，彻底消除相邻采样点之间的来回抖动。
        self._hover_locked = None      # (line, (x, y))，当前锁定的点
        self._hover_locked_axes = None  # 该点所属 axes（换子图时立即失效）
        # 悬停「已绘制状态」：(id(line), x, y)，表示屏幕上当前正显示的悬停点。
        # 与它相同则整帧跳过（不重算避让、不重设文本、不重绘）——既省开销，也
        # 杜绝「鼠标微动 → 提示窗被反复擦除/重画」造成的可见抖动。
        self._hover_state = None
        # LTTB 降采样结果缓存：(id(ts), id(vals), n) -> (ts_ds, vals_ds)
        self._ds_cache: dict = {}
        # 悬停路径缓存：避免逐帧重测文本尺寸 / 重算曲线屏幕坐标
        self._ann_size_cache: dict = {}   # (行数, 最长行字符数) -> (w_px, h_px)
        self._curve_px_cache: dict = {}   # axes+范围指纹 -> (N,2) 屏幕坐标
        # Issue 2: 实时坐标显示文本对象（每个 axes 一个）
        self._coord_texts: dict = {}
        # Issue 1: line -> sig_name 映射，悬停注释用它精确取信号名（避免实时模式
        #          的 label 带 (0xID) 后缀导致 split(".")[-1] 取错匹配不到值描述）
        self._line_sig_name: dict = {}
        # 「眼睛」显隐：被隐藏（眼睛闭合）的信号集合 {(msg_name, sig_name)}
        # 仅控制是否绘制，不删除 _signals 中的数据，恢复显示无需重新解码
        self._hidden: set = set()
        # ─── 实时曲线模式（用于信号实时监控页） ───
        self._realtime: bool = False            # 是否处于实时模式（停止后保留以保留画面）
        self._rt_running: bool = False           # 是否正在接收实时采样（停止后为 False 不再收数）
        self._rt_meta: list = []                 # [(msg_name, sig_name), ...] 有序
        self._rt_buffers: dict = {}              # key -> {"t": list, "v": list}
        self._rt_lines: dict = {}                # key -> matplotlib Line2D
        self._rt_axes: dict = {}                 # key -> 所属 axes
        self._rt_max_points: int = 5000          # 滚动窗口最大点数
        self._rt_t0: float = 0.0                 # 实时监控起始时间（用于相对时间轴）
        # 实时刷新合并：push_sample 只 append + 打脏标记，由定时器统一 set_data +
        # 重绘。原先每个采样点都做一次 set_data + relim + autoscale + draw_idle，
        # 高频报文下会堆积大量 125ms 级全量重绘（实测 draw 单次约 125ms）。
        self._rt_dirty: set = set()              # 待刷新的 key 集合
        self._rt_timer = None                    # QTimer，惰性创建
        # 平移拖动：记录上一帧的鼠标位置（像素），拖动中实时跟随
        self._panning = False
        self._pan_last = None                    # (x_px, y_px)
        self._pan_moved_px = 0.0                 # 累计位移，用于区分单击与拖动
        self._pan_pending = False                # 左键已按下、尚未判定单击/拖动
        self._pan_origin = None                  # (xdata, ydata, ax, x_px, y_px)
        self._pan_xlim: dict = {}
        self._pan_ylim: dict = {}

        self.setStyleSheet(self._QSS)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(dp(6), dp(6), dp(6), dp(6))
        layout.setSpacing(dp(5))

        # ─── 工具栏 ───
        toolbar = QHBoxLayout()
        toolbar.setSpacing(dp(6))

        self._mode_btn = QPushButton("切换为共享Y轴")
        self._mode_btn.setToolTip("在共享Y轴和独立子图模式之间切换")
        self._mode_btn.clicked.connect(self._toggle_mode)
        toolbar.addWidget(self._mode_btn)

        self._mark_btn = QPushButton("标记时间差")
        self._mark_btn.setCheckable(True)
        self._mark_btn.setToolTip(
            "点击放置标记线显示时间差（最多 3 组，每组 2 根）：\n"
            "· 落点自动捕捉到最近的 CAN 帧采样点（放大 X 轴时便于对齐到指定帧）\n"
            "· 标记线贯穿所有波形，可拖动调整，拖动时 Δt 实时更新\n"
            "· 同组两根线同色，组间异色；两线之间的色带标出该组的时间范围，\n"
            "  Δt 提示框显示在该色带上方\n"
            "· 放满 3 组自动退出标记模式；再点本按钮可提前退出（已放的标记保留）\n"
            "· 右键或「清除标记」按钮清空全部标记"
        )
        self._mark_btn.clicked.connect(self._toggle_mark_mode)
        toolbar.addWidget(self._mark_btn)

        self._reset_btn = QPushButton("自适应复位")
        self._reset_btn.setToolTip("重置缩放到数据范围")
        self._reset_btn.clicked.connect(self._auto_scale)
        toolbar.addWidget(self._reset_btn)

        self._clear_mark_btn = QPushButton("清除标记")
        self._clear_mark_btn.setToolTip("清除所有时间差标记线")
        self._clear_mark_btn.clicked.connect(self._clear_marks)
        toolbar.addWidget(self._clear_mark_btn)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # ─── Matplotlib 画布（深色主题）───
        import matplotlib
        matplotlib.rcParams.update(_DARK_THEME_RC)

        self._fig = Figure(figsize=(10, 6))
        self._fig.patch.set_facecolor("#1e1e2e")
        self._canvas = FigureCanvas(self._fig)
        self._canvas.setStyleSheet("background-color: #1e1e2e; border: 1px solid #3a3a4e; border-radius: 4px;")

        # 绘制中半透明加载层（覆盖在画布上，解码/绘图期间提示，避免误以为卡死）
        self._overlay = QLabel(self._canvas)
        self._overlay.setAlignment(Qt.AlignCenter)
        self._overlay.setStyleSheet(
            "background-color: rgba(30,30,46,210); color:#4fc3f7; "
            "font-size:15px; font-weight:bold;"
        )
        self._overlay.hide()

        self._toolbar = NavigationToolbar(self._canvas, self)
        self._toolbar.setStyleSheet("background-color: #1e1e2e; border: none;")
        # 移除 matplotlib 自带的 Pan / Zoom 工具：它们的交互与本组件自实现的
        # 「左键拖动实时平移 / 滚轮缩放」重复，且一旦激活会抢占
        # button_press_event，导致自实现的联动失效、拖动表现异常。
        # Home（自适应复位）/ Back / Forward / Save 保留，功能不受影响。
        self._strip_toolbar_conflicts()

        layout.addWidget(self._toolbar)
        layout.addWidget(self._canvas, stretch=1)

        # ─── 绑定交互事件 ───
        self._canvas.mpl_connect("motion_notify_event", self._on_mouse_move)
        self._canvas.mpl_connect("scroll_event", self._on_scroll)
        self._canvas.mpl_connect("button_press_event", self._on_click)
        self._canvas.mpl_connect("button_release_event", self._on_release)


    def _strip_toolbar_conflicts(self):
        """移除 matplotlib 工具栏里与本组件自实现交互冲突的 Pan / Zoom 按钮。

        工具栏若停留在 Pan 或 Zoom 模式，会拦截鼠标事件（自己处理拖拽/框选），
        使本组件的 _on_click/_on_release 收不到事件，表现为「拖动没反应/很卡」。
        这里直接移除这两个 action，避免用户误触进入冲突模式。
        """
        try:
            tb = self._toolbar
            for action in list(tb.actions()):
                text = action.text().replace("&", "").strip()
                if text in ("Pan", "Zoom"):
                    tb.removeAction(action)
        except Exception:  # noqa: BLE001
            pass

    # ────────────────────── 公共接口 ──────────────────────

    def plot_signals(self, signals: list[DecodedSignal]):
        """绘制信号曲线"""
        self._signals = signals
        # 数据源整体更换：降采样缓存必须失效，避免 id 复用导致取到旧结果
        self._ds_cache.clear()
        self._redraw()

    def get_figure(self) -> Figure:
        """返回 matplotlib Figure 对象，用于导出"""
        return self._fig

    # ────────────────────── 绘制中加载层 ──────────────────────
    def show_loading(self, text: str = "正在绘制曲线…"):
        """显示半透明「绘制中」覆盖层（先 processEvents 让层立即渲染出来）。"""
        self._overlay.setText(text)
        self._position_overlay()
        self._overlay.raise_()
        self._overlay.show()
        QApplication.processEvents()

    def hide_loading(self):
        """隐藏「绘制中」覆盖层。"""
        self._overlay.hide()
        QApplication.processEvents()

    def _position_overlay(self):
        self._overlay.setGeometry(0, 0, self._canvas.width(), self._canvas.height())

    # ────────────────────── blit 局部刷新 ──────────────────────
    #
    # 性能背景（实测，4 信号 × 10 万点、1200×800、DPI=100）：
    #   canvas.draw() 全量重绘 = 125 ms（≈8 FPS）
    #   命中检测 _find_nearest_line = 0.03 ms
    #   copy_from_bbox(整幅) = 1.0 ms
    #   blit 单帧（背景 + 高亮点 + 注释框）= 4.24 ms（≈236 FPS）
    # 即瓶颈是「每次鼠标移动都整幅光栅化」，而非命中算法。故交互期改用
    # 「缓存背景位图 → 只重绘变化的少量 artist → blit 贴图」。

    def _invalidate_bg_cache(self):
        """使背景位图缓存失效（任何改变背景内容的操作后必须调用）。

        一并清掉曲线屏幕坐标缓存：轴范围/曲线增删都会让旧坐标失效，
        而遮挡测试的正确性完全依赖这份坐标是最新的。
        """
        self._bg_cache = None
        self._curve_px_cache.clear()

    def _transient_blit_artists(self) -> list:
        """返回「靠 blit 临时绘制、**绝不能被烙进背景位图**」的 artist。

        包含常驻 blit 图层（坐标文本 / 悬停高亮点与提示窗）与标记吸附预览线。
        拖拽中的标记线由调用方通过 `_blit_artists(..., transient=...)` 追加。
        """
        arts = list(self._persistent_blit_artists())
        for art in self._preview_artists:
            if art is not None and art not in arts:
                arts.append(art)
        return arts

    def _ensure_bg_cache(self, transient=None):
        """确保背景位图缓存可用；缺失时重新整幅绘制并缓存。

        ⚠️ 捕获背景前**必须**先隐藏那批临时图层。它们此刻大多已经可见
        （`_apply_highlight` 是先写好提示窗文本/位置、把它置为可见，之后才走到
        `_blit_artists` 的），直接 `canvas.draw()` 会把它们一起画进背景位图；
        此后每次 `restore_region` 都会把这批像素原样贴回，而同一曲线内换点
        又不会让缓存失效 —— 表现为「第一个悬停提示窗永远留在原地不消失」的鬼影
        （2026-09-24 现象：沿曲线滑动，新点弹出新提示窗，第一个提示窗一直不走）。

        同理可防止：坐标文本改短后残留下一位数字、标记预览线残留。
        注意钉住的固定提示窗（`_pinned_annotations`）与时间差标记**属于背景**，
        不在隐藏之列。

        Args:
            transient: 额外需要临时隐藏的 artist（如 `_move_mark` 正在拖动的
                标记线/色带/Δt 框 —— 它们随后会被本次 blit 重画，故不能进背景）。
        """
        if self._bg_cache is not None:
            return
        hide = self._transient_blit_artists()
        for art in (transient or []):
            if art is not None and art not in hide:
                hide.append(art)
        hidden = []
        for art in hide:
            try:
                if art.get_visible():
                    art.set_visible(False)
                    hidden.append(art)
            except Exception:  # noqa: BLE001
                continue
        try:
            self._canvas.draw()
            self._bg_cache = self._canvas.copy_from_bbox(self._fig.bbox)
        except Exception:  # noqa: BLE001
            self._bg_cache = None
        finally:
            # 无论缓存成功与否都要恢复可见，否则这些图层会「消失」
            for art in hidden:
                try:
                    art.set_visible(True)
                except Exception:  # noqa: BLE001
                    pass

    def _blit_artists(self, artists, transient=None):
        """只重绘给定 artist 并贴回画面（背景取自缓存）。

        Args:
            artists: 需要重绘的 artist 可迭代表；None 项会被跳过。
            transient: 同样需要重绘、但**不得烙进背景位图**的额外 artist
                （见 `_ensure_bg_cache`）。
        """
        if not artists:
            return
        try:
            self._ensure_bg_cache(transient)
            if self._bg_cache is None:
                self._canvas.draw_idle()
                return
            # restore_region 会把「上一次 blit 画上去的内容」整块抹掉，所以本次
            # 必须把**所有常驻 blit 图层**（坐标文本 / 悬停高亮点 / 提示窗）一并
            # 画回。否则不同调用路径会互相擦除：坐标文本那一次把提示窗擦掉、
            # 提示窗那一次又把坐标文本擦掉 → 表现为「悬停框每帧闪一下」。
            merged = list(artists)
            for art in self._persistent_blit_artists():
                if art not in merged:
                    merged.append(art)
            self._canvas.restore_region(self._bg_cache)
            for art in merged:
                if art is None:
                    continue
                ax = getattr(art, "axes", None)
                if ax is None:
                    continue
                try:
                    ax.draw_artist(art)
                except Exception:  # noqa: BLE001
                    continue
            self._canvas.blit(self._fig.bbox)
        except Exception:  # noqa: BLE001
            # 任何 blit 异常都退化为常规重绘，保证功能不坏
            self._invalidate_bg_cache()
            self._canvas.draw_idle()

    def _persistent_blit_artists(self) -> list:
        """返回「必须常驻屏幕」的 blit artist：坐标文本 + 悬停高亮点 / 提示窗。

        blit 的机制是「恢复整块背景位图 → 只重画给定 artist → 贴回」，所以
        每一次局部刷新都必须把它们带上重画，否则会被背景位图抹掉 —— 这正是
        「坐标文本与提示窗互相擦除、鼠标一动提示窗就闪」的根因。
        """
        arts = []
        fig = getattr(self, "_fig", None)
        if fig is None:
            return arts
        for ax in fig.axes:
            txt = self._coord_texts.get(id(ax))
            if txt is not None and txt.get_visible():
                arts.append(txt)
        if self._hover_ann is not None and self._hover_ann.get_visible():
            if self._hover_point is not None:
                arts.append(self._hover_point)
            arts.append(self._hover_ann)
        return arts

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 尺寸变化会让缓存的背景位图与新画布不匹配，必须失效
        self._invalidate_bg_cache()
        if self._overlay.isVisible():
            self._position_overlay()

    def set_value_descriptions(self, descriptions: dict):
        """设置 DBC 值描述表，用于悬停提示显示枚举含义

        Args:
            descriptions: {sig_name: {int_val: "描述", ...}, ...}
            例如 {"Gear": {0: "PARK", 1: "REVERSE", 2: "NEUTRAL", 3: "DRIVE"}}
        """
        self._value_descriptions = descriptions

    def set_hidden_signals(self, hidden):
        """设置需要隐藏（眼睛闭合）的信号集合，并立即重绘。

        Args:
            hidden: {(msg_name, sig_name), ...}，按 (msg_name, sig_name) 匹配
                    _signals 中的信号；可传空集合以全部恢复显示。

        被隐藏的信号不参与绘制：独立子图模式下整张子图不再出现，共享 Y 轴
        模式下不画线也不进图例。数据仍保留在 _signals 中，恢复显示无需重新
        解码，因此本方法只重绘、不触发解码。
        """
        new_hidden = {tuple(k) for k in (hidden or ())}
        if new_hidden == self._hidden:
            return
        self._hidden = new_hidden
        if not self._signals:
            return  # 尚无数据：只记录状态，等下次 plot_signals 时生效
        self._redraw()

    def is_hidden(self, msg_name: str, sig_name: str) -> bool:
        """该信号当前是否被隐藏（眼睛闭合）"""
        return (msg_name, sig_name) in self._hidden

    # ────────────────────── 绘制逻辑 ──────────────────────

    def _visible_signals(self) -> list:
        """返回当前需要绘制的 [(原始索引, DecodedSignal), ...]。

        被「眼睛」隐藏的信号不参与绘制。保留原始索引用于配色：若改用
        enumerate(过滤后的列表)，隐藏中间某个信号会让其后的曲线整体换色。
        """
        return [(i, sig) for i, sig in enumerate(self._signals)
                if (sig.msg_name, sig.sig_name) not in self._hidden]

    def _redraw(self):
        """根据当前模式和信号列表重绘"""
        self._fig.clear()
        self._fig.patch.set_facecolor("#1e1e2e")
        # fig.clear() 会把标记线一并销毁：这里丢弃失效引用，稍后按 _mark_points 重建
        self._mark_artists = []
        self._mark_spans = []
        self._mark_delta_anns = []
        self._preview_artists = []
        # fig.clear() 同样会销毁曲线 / 注释 / 坐标文本 artist，但下列引用原先未清理，
        # 导致：① _line_sig_name 与 _pinned_* 每次重绘只增不减（悬垂引用 + 内存泄漏）；
        # ② 悬停时 `line in self._pinned_lines` 对新 artist 恒为 False，
        #    「点击固定」在重绘后静默失效；③ 再次点击旧引用时对已 remove 的对象
        #    调 set_linewidth/remove 可能抛异常。这里一并清空。
        # 按用户确认：固定状态不需要跨重绘保留，故直接丢弃即可。
        self._pinned_lines.clear()
        self._pinned_annotations.clear()
        self._highlighted_line = None
        self._annotation = None
        self._coord_texts.clear()
        # fig.clear() 同样销毁了悬停高亮点 / 提示窗 artist：必须一并丢弃引用，
        # 否则 _persistent_blit_artists 会拿旧 artist 去 draw（画在游离 axes 上），
        # 且 _hover_state / 锁定点残留会让重绘后鼠标不动时不再重新显示提示窗。
        self._discard_hover_artists()
        self._release_hover_lock()
        self._hover_state = None
        self._invalidate_bg_cache()
        # line -> sig_name 映射：各 _draw_* / _build_realtime 内还会再 clear 一次
        # （它们可能被单独调用），这里统一先清，保证任何重绘分支都不残留旧 artist 键。
        self._line_sig_name.clear()

        # 实时模式：根据缓冲数据构建坐标轴与曲线
        if self._realtime and self._rt_meta:
            self._build_realtime()
            self._restore_marks()
            self._canvas.draw()
            return

        if not self._visible_signals():
            ax = self._fig.add_subplot(111)
            ax.set_facecolor("#1e1e2e")
            # 区分「一个信号都没选」与「已选但被眼睛全部隐藏」，后者给出恢复提示
            hint = ("请勾选信号并点击绘图" if not self._signals
                    else "所有曲线已隐藏（点击左侧眼睛图标恢复显示）")
            ax.text(0.5, 0.5, hint,
                    transform=ax.transAxes, ha="center", va="center",
                    fontsize=14, color="#666680", fontweight="light")
            ax.grid(True, alpha=0.3)
            self._canvas.draw()
            return

        if self._subplot_mode:
            self._draw_subplots()
        else:
            self._draw_shared()

        self._fig.tight_layout(pad=2.0)
        # 重绘后恢复时间差标记线（切换显隐/模式、重绘都不应丢掉已放置的标记）
        self._restore_marks()

        # Issue 2: 为每个 axes 创建实时坐标显示文本（左下角）
        self._coord_texts.clear()
        for ax in self._fig.axes:
            txt = ax.text(0.01, 0.01, "", transform=ax.transAxes,
                          fontsize=8, color="#aaaaaa", va="bottom", ha="left",
                          bbox=dict(boxstyle="round,pad=0.2", facecolor="#1e1e2e",
                                    edgecolor="none", alpha=0.7))
            self._coord_texts[id(ax)] = txt

        self._canvas.draw()

    # ────────────────────── 实时曲线模式 ──────────────────────

    def start_realtime(self, meta: list):
        """进入实时监控模式，准备绘制给定信号列表。

        Args:
            meta: [(frame_id, msg_name, sig_name), ...]，frame_id 为报文 ID
                  （int，可为 None），用于图例展示如 "TCU_3.TCU_Drivemode(0x112)"
        """
        self._realtime = True
        self._rt_running = True
        self._rt_meta = list(meta)
        self._rt_buffers = {
            (f, m, s): {"t": [], "v": []} for (f, m, s) in meta
        }
        self._rt_lines = {}
        self._rt_axes = {}
        self._rt_t0 = 0.0
        self._redraw()

    def push_sample(self, frame_id, msg_name: str, sig_name: str, t: float, v: float):
        """推送一个实时采样点。

        必须在 GUI 线程调用（由监控页通过信号槽从后台线程转发）。
        frame_id 用于与 start_realtime 传入的 meta 三元组键匹配。

        性能：旧实现在这里直接 set_data + relim + autoscale + draw_idle，
        每个采样点都触发一次整幅重绘（实测约 125 ms）。高频报文下事件队列
        会被拖垮。现改为只 append + 打脏标记，由 _rt_tick 以 ~30 Hz 合并刷新。
        """
        if not self._rt_running:
            return
        key = (frame_id, msg_name, sig_name)
        buf = self._rt_buffers.get(key)
        if buf is None:
            return

        # 以首个样本时间为时间轴起点，避免数值过大影响显示
        if self._rt_t0 == 0.0 and not buf["t"]:
            self._rt_t0 = t
        rel_t = t - self._rt_t0

        buf["t"].append(rel_t)
        buf["v"].append(v)
        # 滚动窗口截断
        if len(buf["t"]) > self._rt_max_points:
            overflow = len(buf["t"]) - self._rt_max_points
            del buf["t"][:overflow]
            del buf["v"][:overflow]

        self._rt_dirty.add(key)
        self._ensure_rt_timer()

    def _ensure_rt_timer(self):
        """惰性创建并启动实时合并刷新定时器。"""
        if self._rt_timer is None:
            self._rt_timer = QTimer(self)
            self._rt_timer.setInterval(RT_REFRESH_MS)
            self._rt_timer.timeout.connect(self._rt_tick)
        if not self._rt_timer.isActive():
            self._rt_timer.start()

    def _rt_tick(self):
        """合并刷新：把本轮所有脏曲线一次性 set_data，再整幅重绘一次。

        按 axes 去重 relim：共享 Y 轴模式下多条曲线同属一个 axes，
        只需重算一次范围。
        """
        if not self._rt_running and not self._rt_dirty:
            if self._rt_timer is not None and self._rt_timer.isActive():
                self._rt_timer.stop()
            return
        dirty = self._rt_dirty
        if not dirty:
            return
        self._rt_dirty = set()

        touched_axes = []
        seen = set()
        for key in dirty:
            line = self._rt_lines.get(key)
            buf = self._rt_buffers.get(key)
            if line is None or buf is None or not buf["t"]:
                continue
            # 转为 ndarray：matplotlib 对 list 每次都要额外做一次转换
            line.set_data(np.asarray(buf["t"], dtype=float),
                          np.asarray(buf["v"], dtype=float))
            ax = self._rt_axes.get(key)
            if ax is not None and id(ax) not in seen:
                seen.add(id(ax))
                touched_axes.append(ax)

        for ax in touched_axes:
            try:
                ax.relim()
                ax.autoscale_view(scalex=True, scaley=True)
            except Exception:  # noqa: BLE001
                continue
        if touched_axes:
            # 轴范围可能变化 → 背景缓存失效
            self._invalidate_bg_cache()
            self._canvas.draw_idle()

    def stop_realtime(self):
        """退出实时模式，保留最后一次画面。

        仅停止接收新采样（_rt_running=False），但保留 _realtime 状态、
        _rt_meta 与 _rt_buffers，使「停止监控」后点击「切换共享Y轴/独立子图」
        时仍能用已缓冲的数据重绘曲线，而不至于回退到「请勾选信号」占位图。
        """
        self._rt_running = False
        # 停掉合并刷新定时器：停止后不再有新采样，继续跑只是空转
        if self._rt_timer is not None and self._rt_timer.isActive():
            self._rt_timer.stop()
        self._rt_dirty.clear()
        # 不清除 _realtime / _rt_meta / _rt_buffers，保留最后一次绘制结果

    def reset_realtime(self):
        """清空当前实时曲线数据（Issue 3：实时监控页「重置」按钮）。

        仅清空缓冲区与曲线，保留 _realtime / _rt_meta / _rt_running 状态：
        - 若监控仍在进行（_rt_running=True），后续采样会立即继续绘制新曲线；
        - 若已停止监控，画面回到空坐标轴但不丢失「已选信号」配置。
        """
        if not self._realtime:
            return
        for key in list(self._rt_buffers.keys()):
            self._rt_buffers[key]["t"].clear()
            self._rt_buffers[key]["v"].clear()
        self._redraw()

    def remove_realtime_signals(self, selected: set):
        """批量移除不在 selected（set of (msg_name, sig_name)）中的实时信号曲线。

        用于「实时监控页」移除已选信号时，使曲线与已选列表保持一致（一次重绘，
        不影响未被移除的信号曲线）。监控已停止但画面保留时同样可调用以清理曲线。
        """
        if not self._realtime:
            return
        removed = False
        for key in list(self._rt_meta):
            if (key[1], key[2]) not in selected:
                self._rt_meta.remove(key)
                self._rt_buffers.pop(key, None)
                self._rt_lines.pop(key, None)
                self._rt_axes.pop(key, None)
                removed = True
        if removed:
            self._redraw()

    def add_realtime_signal(self, frame_id, msg_name: str, sig_name: str):
        """监控进行中新增一个信号：加入实时缓冲与曲线并重绘（去重）。"""
        if not self._realtime:
            return
        key = (frame_id, msg_name, sig_name)
        if key in self._rt_meta:
            return
        self._rt_meta.append(key)
        self._rt_buffers[key] = {"t": [], "v": []}
        self._redraw()

    def has_realtime_signal(self, msg_name: str, sig_name: str) -> bool:
        """当前实时模式是否已包含该信号曲线（按 (msg_name, sig_name) 匹配）。"""
        return any(k[1] == msg_name and k[2] == sig_name for k in self._rt_meta)

    def reorder_realtime(self, meta: list):
        """按新顺序重排实时信号曲线并重绘。

        Args:
            meta: [(frame_id, msg_name, sig_name), ...]，顺序即「已选信号列表」
                  的新顺序（frame_id 与 start_realtime 传入的保持一致）。

        仅调整 _rt_meta 顺序并重绘（缓冲数据按 key 保留），用于「实时监控页」
        已选信号列表拖拽排序后，使曲线顺序与列表顺序一致。
        """
        if not self._realtime:
            return
        order = {k: i for i, k in enumerate(meta)}
        self._rt_meta.sort(key=lambda k: order.get(k, len(order)))
        self._redraw()

    def set_subplot_mode(self, enabled: bool):
        """设置是否使用独立子图模式（Issue 3：实时监控页默认独立子图）。

        仅更新内部标志与工具栏按钮文案，等下次 redraw 生效。
        """
        self._subplot_mode = bool(enabled)
        self._mode_btn.setText("切换为共享Y轴" if self._subplot_mode else "切换为独立子图")

    def _build_realtime(self):
        """根据实时缓冲构建坐标轴与空曲线（模式切换时复用）"""
        self._rt_lines = {}
        self._rt_axes = {}
        self._line_sig_name.clear()

        if self._subplot_mode:
            n = len(self._rt_meta)
            axes = self._fig.subplots(n, 1, sharex=True)
            if n == 1:
                axes = [axes]
            for i, (frame_id, msg_name, sig_name) in enumerate(self._rt_meta):
                ax = axes[i]
                ax.set_facecolor("#1e1e2e")
                color = COLORS[i % len(COLORS)]
                label = (f"{msg_name}.{sig_name}(0x{frame_id:03X})"
                         if frame_id is not None else f"{msg_name}.{sig_name}")
                line, = ax.plot([], [], color=color, linewidth=self._original_linewidth,
                                marker="o", markersize=2, label=label, alpha=0.9)
                self._line_sig_name[line] = sig_name
                ax.set_title(label, loc="left", fontsize=9, color=color, pad=2)
                ax.grid(True, linestyle="--", alpha=0.4, color="#3a3a4e")
                legend = ax.legend(loc="upper right", framealpha=0.85)
                legend.get_frame().set_edgecolor("#3a3a4e")
                self._rt_lines[(frame_id, msg_name, sig_name)] = line
                self._rt_axes[(frame_id, msg_name, sig_name)] = ax
            axes[-1].set_xlabel("时间 (s)", fontsize=11)
        else:
            ax = self._fig.add_subplot(111)
            ax.set_facecolor("#1e1e2e")
            ax.set_xlabel("时间 (s)", fontsize=11)
            ax.set_ylabel("物理值", fontsize=11)
            ax.grid(True, linestyle="--", alpha=0.4, color="#3a3a4e")
            for i, (frame_id, msg_name, sig_name) in enumerate(self._rt_meta):
                color = COLORS[i % len(COLORS)]
                label = (f"{msg_name}.{sig_name}(0x{frame_id:03X})"
                         if frame_id is not None else f"{msg_name}.{sig_name}")
                line, = ax.plot([], [], color=color, linewidth=self._original_linewidth,
                                marker="o", markersize=2, label=label, alpha=0.9)
                self._line_sig_name[line] = sig_name
                self._rt_lines[(frame_id, msg_name, sig_name)] = line
                self._rt_axes[(frame_id, msg_name, sig_name)] = ax
            legend = ax.legend(loc="upper right", framealpha=0.85)
            legend.get_frame().set_edgecolor("#3a3a4e")

        # 用缓冲数据初始化曲线（模式切换时保留已有数据），再按轴自适应
        _seen_axes = set()
        for key, line in self._rt_lines.items():
            buf = self._rt_buffers.get(key)
            if buf and buf["t"]:
                line.set_data(buf["t"], buf["v"])
        for key, ax in self._rt_axes.items():
            if id(ax) in _seen_axes:
                continue
            ax.relim()
            ax.autoscale_view(scalex=True, scaley=True)
            _seen_axes.add(id(ax))

        self._fig.tight_layout(pad=2.0)

        # Issue 2: 为每个 axes 创建实时坐标显示文本
        self._coord_texts.clear()
        for ax in self._fig.axes:
            txt = ax.text(0.01, 0.01, "", transform=ax.transAxes,
                          fontsize=8, color="#aaaaaa", va="bottom", ha="left",
                          bbox=dict(boxstyle="round,pad=0.2", facecolor="#1e1e2e",
                                    edgecolor="none", alpha=0.7))
            self._coord_texts[id(ax)] = txt

    def _draw_shared(self):
        """共享 Y 轴模式"""
        self._line_sig_name.clear()
        ax = self._fig.add_subplot(111)
        ax.set_facecolor("#1e1e2e")
        ax.set_xlabel("时间 (s)", fontsize=11)
        ax.set_ylabel("物理值", fontsize=11)
        ax.grid(True, linestyle="--", alpha=0.4, color="#3a3a4e")

        # 跳过被眼睛隐藏的信号；i 为信号在完整列表中的原始序号，配色保持稳定
        for i, sig in self._visible_signals():
            color = COLORS[i % len(COLORS)]
            ts, vals = self._downsample_if_needed(sig.timestamps, sig.values)
            label = f"{sig.msg_name}.{sig.sig_name}"
            line, = ax.plot(ts, vals, color=color, linewidth=self._original_linewidth,
                    marker="o", markersize=2, label=label, alpha=0.9)
            self._line_sig_name[line] = sig.sig_name

        legend = ax.legend(loc="upper right", framealpha=0.85)
        legend.get_frame().set_edgecolor("#3a3a4e")

    def _draw_subplots(self):
        """独立子图模式（被眼睛隐藏的信号不生成子图；配色用原始序号，
        隐藏/恢复单个信号时其余子图的颜色保持不变）"""
        items = self._visible_signals()
        n = len(items)
        axes = self._fig.subplots(n, 1, sharex=True)
        if n == 1:
            axes = [axes]

        for (i, sig), ax in zip(items, axes):
            color = COLORS[i % len(COLORS)]
            ax.set_facecolor("#1e1e2e")
            ts, vals = self._downsample_if_needed(sig.timestamps, sig.values)
            label = f"{sig.msg_name}.{sig.sig_name}"
            line, = ax.plot(ts, vals, color=color, linewidth=self._original_linewidth,
                    marker="o", markersize=2, label=label, alpha=0.9)
            self._line_sig_name[line] = sig.sig_name
            # Issue 4: 不使用 set_ylabel 避免长信号名与相邻子图重叠，改用子图内标题
            ax.set_title(f"{sig.msg_name}.{sig.sig_name}", loc='left', fontsize=9,
                         color=color, pad=2)
            ax.grid(True, linestyle="--", alpha=0.4, color="#3a3a4e")

            legend = ax.legend(loc="upper right", framealpha=0.85)
            legend.get_frame().set_edgecolor("#3a3a4e")

        axes[-1].set_xlabel("时间 (s)", fontsize=11)

    def _downsample_if_needed(self, timestamps, values):
        """可视区域数据点超过阈值时降采样（结果按数组身份缓存）。

        _redraw 会在模式切换、眼睛显隐、标记恢复等场景被反复调用；若每次都
        重算 LTTB（50 万点实测约 0.17 s），交互会有可感知的延迟。这里按
        (id(ts), id(vals), n) 缓存——同一份解码结果在多次重绘间是同一个
        ndarray 对象。缓存由 plot_signals 等「数据源真正变化」的时机清空，
        避免对象回收后 id 复用导致的错配。
        """
        if len(timestamps) <= DOWNSAMPLE_THRESHOLD:
            return timestamps, values
        key = (id(timestamps), id(values), len(timestamps))
        hit = self._ds_cache.get(key, _DS_MISS)
        if hit is not _DS_MISS:
            return hit
        result = lttb_downsample(timestamps, values, DOWNSAMPLE_THRESHOLD)
        if len(self._ds_cache) > _DS_CACHE_MAX:
            self._ds_cache.clear()
        self._ds_cache[key] = result
        return result

    # ────────────────────── 模式切换 ──────────────────────

    def _toggle_mode(self):
        """切换共享/独立子图模式"""
        self._subplot_mode = not self._subplot_mode
        self._mode_btn.setText("切换为共享Y轴" if self._subplot_mode else "切换为独立子图")
        self._redraw()

    def _toggle_mark_mode(self):
        """切换时间差标记模式。

        进入时**不清空**已有标记：三组标记通常是连续放置的（放完一组仍留在标记模式
        里接着放下一组），中途退出再进也应能接着标。只有已经放满 MARK_GROUPS_MAX 组
        时才清空重来——否则会出现「图上有旧线、内部状态已清空」的不一致状态，
        旧线既不会被替换也拖不动。
        """
        self._mark_mode = self._mark_btn.isChecked()
        if self._mark_mode:
            if len(self._mark_points) >= MARK_MAX:
                self._clear_marks()
        else:
            self._remove_mark_preview()
        self._update_mark_btn_text()

    def _update_mark_btn_text(self):
        """标记模式且已有落点时，在按钮上显示进度（n/总数）。"""
        n = len(self._mark_points)
        self._mark_btn.setText(
            "标记时间差 %d/%d" % (n, MARK_MAX) if self._mark_mode and n
            else "标记时间差"
        )

    def _auto_scale(self):
        """自适应复位"""
        for ax in self._fig.axes:
            ax.relim()
            ax.autoscale()
        self._fig.tight_layout(pad=2.0)
        self._invalidate_bg_cache()
        self._canvas.draw()

    def _clear_marks(self):
        """清除所有时间差标记（含范围色带、Δt 提示框与吸附预览）"""
        self._mark_points.clear()
        self._dragging_mark = -1
        self._mark_artists = []
        self._mark_spans = []
        self._mark_delta_anns = []
        self._remove_mark_preview()
        # 兜底：清理任何仍挂在坐标轴上的标记 / 预览 artist
        for ax in self._fig.axes:
            for child in ax.get_children():
                if (getattr(child, "_is_time_mark", False)
                        or getattr(child, "_is_mark_preview", False)):
                    try:
                        child.remove()
                    except Exception:  # noqa: BLE001
                        pass
        self._update_mark_btn_text()
        self._invalidate_bg_cache()
        self._canvas.draw_idle()

    # ────────────────────── 时间差标记（吸附 / 贯穿 / 拖动）──────────────────────

    def _snap_candidates(self) -> list:
        """参与自动捕捉的采样时间序列（各可见信号的时间戳；实时模式下取缓冲）。"""
        arrays = []
        for _, sig in self._visible_signals():
            ts = getattr(sig, "timestamps", None)
            if ts is not None and len(ts):
                arrays.append(np.asarray(ts, dtype=float))
        if not arrays and self._rt_buffers:
            for buf in self._rt_buffers.values():
                if buf["t"]:
                    arrays.append(np.asarray(buf["t"], dtype=float))
        return arrays

    def _snap_time(self, t, ax):
        """把时间 t 吸附到最近的 CAN 帧采样点。

        仅在最近的采样点距鼠标不超过 MARK_SNAP_PX 像素时吸附，否则保持原始
        位置——避免 X 轴拉得很宽时把标记线「吸」到很远的帧上。
        """
        if t is None or ax is None:
            return t
        try:
            px_per_unit = abs(
                ax.transData.transform((1.0, 0.0))[0]
                - ax.transData.transform((0.0, 0.0))[0]
            )
        except Exception:  # noqa: BLE001
            return t
        if px_per_unit <= 0:
            return t

        best_t = None
        best_d = None
        for arr in self._snap_candidates():
            idx = int(np.searchsorted(arr, t))
            for j in (idx - 1, idx):
                if 0 <= j < len(arr):
                    d = abs(float(arr[j]) - t)
                    if best_d is None or d < best_d:
                        best_d, best_t = d, float(arr[j])
        if best_t is None:
            return t
        if best_d * px_per_unit > MARK_SNAP_PX:
            return t
        return best_t

    def _render_marks(self):
        """按 _mark_points 重建全部标记线。

        标记线画在**每个**子图上（而非只画点击到的那一个），这样独立子图模式下
        标记线贯穿所有波形，便于比较同一时刻不同信号的取值、以及周期不同的报文。
        每 MARK_PER_GROUP 根线构成一组：组内两线同色，两线之间用半透明色带标出该组
        的时间范围，Δt 提示框显示在该色带之上（见 _make_delta_ann）。
        """
        self._remove_mark_artists()
        axes = self._fig.axes
        if not axes:
            return
        for k, t in enumerate(self._mark_points):
            color = self._mark_color(k)
            artists = []
            for ax in axes:
                line = ax.axvline(x=t, color=color, linestyle="--",
                                  linewidth=1.6, zorder=4)
                line._is_time_mark = True
                artists.append(line)
            # 时间标签固定在绘图区顶部（x 用数据坐标、y 用 axes 坐标的混合变换）
            label = axes[0].annotate(
                f"t{k + 1} = {t:.4f}s",
                xy=(t, 1.0), xycoords=axes[0].get_xaxis_transform(),
                xytext=(0, 3), textcoords="offset points",
                fontsize=8, color=color, fontweight="bold",
                ha="center", va="bottom", zorder=6,
            )
            label._is_time_mark = True
            artists.append(label)
            self._mark_artists.append(artists)

        # 已放满的组：范围色带 + Δt 提示框（索引与组号对齐）
        for g, t1, t2 in self._mark_groups():
            lo, hi = (t1, t2) if t1 <= t2 else (t2, t1)
            color = self._mark_color(g * MARK_PER_GROUP)
            spans = []
            for ax in axes:
                span = ax.axvspan(lo, hi, color=color, alpha=MARK_BAND_ALPHA,
                                  linewidth=0, zorder=1.5)
                span._is_time_mark = True
                spans.append(span)
            self._mark_spans.append(spans)
            self._mark_delta_anns.append(self._make_delta_ann(g, lo, hi, color))

    def _make_delta_ann(self, g: int, lo, hi, color: str):
        """创建第 g 组的 Δt 提示框。

        提示框放在**绘图区内部**的顶部，并按组向下错开：原先用 offset points 挂在
        y=1.0 之上，框体会落到绘图区外，多子图模式下还会被上层子图压住，导致显示
        不全；这里改用混合变换（x 数据坐标 / y axes 坐标）+ 高 zorder 修正。
        """
        ax = self._fig.axes[0]
        mid = (lo + hi) / 2
        ann = ax.annotate(
            self._delta_text(g),
            xy=(mid, self._mark_label_y(g)), xycoords=ax.get_xaxis_transform(),
            ha=self._mark_label_ha(ax, mid), va="top",
            fontsize=10, color=color, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.35", facecolor="#2b2b2b",
                      edgecolor=color, alpha=0.92),
            zorder=MARK_LABEL_ZORDER,
        )
        ann._is_time_mark = True
        return ann

    @staticmethod
    def _mark_color(k: int) -> str:
        """第 k 根标记线的颜色：同组两根线同色，组与组之间换色。"""
        return MARK_GROUP_COLORS[(k // MARK_PER_GROUP) % len(MARK_GROUP_COLORS)]

    def _mark_label_y(self, g: int) -> float:
        """第 g 组 Δt 提示框在绘图区内的 y（axes 坐标）。

        错开距离按**像素**换算成 axes 分数：若直接用固定比例，绘图区较矮时三组提示框
        会重新叠在一起。绘图区矮到按固定间距会顶到底边时，再自动压缩间距并上提首组，
        保证每一组的提示框都完整落在绘图区内——「提示框显示不全」正是要避免的情况。
        """
        h = 0.0
        if self._fig.axes:
            try:
                h = float(self._fig.axes[0].get_window_extent().height)
            except Exception:  # noqa: BLE001
                h = 0.0
        if h <= 0:
            return max(MARK_LABEL_TOP - g * MARK_LABEL_STEP, 0.10)

        n = max(MARK_GROUPS_MAX - 1, 1)
        top, step = MARK_LABEL_TOP_PX, MARK_LABEL_STEP_PX
        if top + step * n + MARK_LABEL_BOX_PX > h:
            top = 2.0
            step = max((h - top - MARK_LABEL_BOX_PX) / n, 1.0)
        return max(1.0 - (top + g * step) / h, 0.0)

    @staticmethod
    def _mark_label_ha(ax, x) -> str:
        """提示框对齐方式：贴近左右边界时改用边缘对齐，避免框体溢出绘图区。"""
        try:
            x0, x1 = ax.get_xlim()
            frac = (x - x0) / (x1 - x0) if x1 > x0 else 0.5
        except Exception:  # noqa: BLE001
            return "center"
        if frac < 0.18:
            return "left"
        if frac > 0.82:
            return "right"
        return "center"

    def _mark_groups(self) -> list:
        """把扁平的 _mark_points 按「每 MARK_PER_GROUP 根一组」切分，只返回完整组。"""
        out = []
        for g in range(len(self._mark_points) // MARK_PER_GROUP):
            i = g * MARK_PER_GROUP
            out.append((g, self._mark_points[i], self._mark_points[i + 1]))
        return out

    def _delta_text(self, g: int = 0) -> str:
        """第 g 组的时间差文本。"""
        i = g * MARK_PER_GROUP
        t1, t2 = self._mark_points[i], self._mark_points[i + 1]
        return f"Δt{g + 1} = {abs(t2 - t1):.4f} s"

    def _remove_mark_artists(self):
        for artists in list(self._mark_artists) + list(self._mark_spans):
            for art in artists:
                try:
                    art.remove()
                except Exception:  # noqa: BLE001
                    pass
        for ann in self._mark_delta_anns:
            try:
                ann.remove()
            except Exception:  # noqa: BLE001
                pass
        self._mark_artists = []
        self._mark_spans = []
        self._mark_delta_anns = []

    def _restore_marks(self):
        """重绘后按 _mark_points 恢复标记线（_fig.clear() 会连标记一起销毁）。"""
        if self._mark_points:
            self._render_marks()

    def _add_mark(self, t):
        """在时间 t 放置一根标记线；每放满一组会自动补上范围色带与 Δt 提示框。"""
        if len(self._mark_points) >= MARK_MAX:
            return
        self._mark_points.append(float(t))
        self._render_marks()
        self._update_mark_btn_text()
        # 新增了 artist → 背景内容已变，缓存必须失效再重绘
        self._invalidate_bg_cache()
        self._canvas.draw_idle()

    def _move_mark(self, idx: int, t):
        """把第 idx 根标记线移到时间 t（拖动中实时调用）。

        这里只做局部更新（竖线 / 顶部时间标签 / 所属组的色带与 Δt 文本），不整幅重建，
        避免拖动过程中反复创建 artist。
        """
        if idx < 0 or idx >= len(self._mark_points):
            return
        self._mark_points[idx] = float(t)
        artists = self._mark_artists[idx] if idx < len(self._mark_artists) else []
        for art in artists:
            if hasattr(art, "set_xdata"):      # 竖线
                art.set_xdata([t, t])
            else:                               # 顶部时间标签
                art.xy = (t, 1.0)
                art.set_text(f"t{idx + 1} = {float(t):.4f}s")
        # 该线所属组的范围色带与 Δt 提示框跟随刷新（需求里的「动态显示距离」）
        g = idx // MARK_PER_GROUP
        if (g + 1) * MARK_PER_GROUP <= len(self._mark_points):
            self._update_mark_group(g)

        # blit 局部刷新：只重绘被拖动的竖线/标签 + 该组色带与 Δt 框。
        # 不再用 draw_idle()——那会触发约 125 ms 的整幅重绘，拖动时明显发涩。
        # 注意：色带（axvspan）宽度在变，旧色带的残留由「先恢复背景位图」抹掉，
        # 因此这里必须走 _blit_artists 而不是直接 draw_artist。
        dirty = [art for art in artists]
        if g < len(self._mark_spans):
            dirty.extend(self._mark_spans[g])
        if g < len(self._mark_delta_anns) and self._mark_delta_anns[g] is not None:
            dirty.append(self._mark_delta_anns[g])
        # 以 transient 传入：`_add_mark` 刚把缓存置空，本次 blit 会顺带捕获背景
        # 位图，若不排除这批 artist，标记线/色带/Δt 框会被烙进背景，拖动后
        # 原地留下一套不动的鬼影（同类缺陷，2026-09-24 一并修）。
        self._blit_artists(dirty, transient=dirty)

    def _update_mark_group(self, g: int) -> None:
        """同步第 g 组的范围色带与 Δt 提示框（就地更新，不重建 artist）。"""
        i = g * MARK_PER_GROUP
        t1, t2 = self._mark_points[i], self._mark_points[i + 1]
        lo, hi = (t1, t2) if t1 <= t2 else (t2, t1)
        for span in (self._mark_spans[g] if g < len(self._mark_spans) else []):
            span.set_x(lo)
            span.set_width(hi - lo)
        if g < len(self._mark_delta_anns) and self._mark_delta_anns[g] is not None:
            ann = self._mark_delta_anns[g]
            mid = (lo + hi) / 2
            ann.xy = (mid, self._mark_label_y(g))
            ann.set_ha(self._mark_label_ha(ann.axes, mid))
            ann.set_text(self._delta_text(g))

    def _mark_index_near(self, event, px: int = MARK_HIT_PX) -> int:
        """鼠标按下位置附近的标记线序号（-1 表示没有）。"""
        if event.inaxes is None or not self._mark_points or event.xdata is None:
            return -1
        try:
            best_i, best_px = -1, float(px)
            for i, t in enumerate(self._mark_points):
                tx = event.inaxes.transData.transform((t, 0.0))[0]
                d = abs(tx - event.x)
                if d <= best_px:
                    best_i, best_px = i, d
            return best_i
        except Exception:  # noqa: BLE001
            return -1

    def _update_mark_preview(self, t):
        """标记模式下绘制吸附预览线（提示即将落在哪个采样点上）。"""
        axes = self._fig.axes
        if not axes:
            return
        if len(self._preview_artists) != len(axes) + 1:
            self._remove_mark_preview()
            for ax in axes:
                line = ax.axvline(x=t, color="#ffffff", linestyle=":",
                                  linewidth=1.0, alpha=0.55)
                line._is_mark_preview = True
                self._preview_artists.append(line)
            label = axes[0].annotate(
                "", xy=(t, 1.0), xycoords=axes[0].get_xaxis_transform(),
                xytext=(0, 3), textcoords="offset points",
                fontsize=8, color="#ffffff", ha="center", va="bottom",
            )
            label._is_mark_preview = True
            self._preview_artists.append(label)
        for art in self._preview_artists:
            if hasattr(art, "set_xdata"):
                art.set_xdata([t, t])
            else:
                art.xy = (t, 1.0)
                art.set_text(f"{t:.4f}s")
        # blit 局部刷新：预览线是临时 artist，用背景位图擦掉上一帧位置
        self._blit_artists(list(self._preview_artists))

    def _remove_mark_preview(self):
        if not self._preview_artists:
            return
        for art in self._preview_artists:
            try:
                art.remove()
            except Exception:  # noqa: BLE001
                pass
        self._preview_artists = []
        # artist 被移除 → 背景内容变了，缓存失效后重绘，否则预览线会残留
        self._invalidate_bg_cache()
        self._canvas.draw_idle()

    # ────────────────────── 鼠标交互 ──────────────────────

    def _on_mouse_move(self, event):
        """鼠标移动：平移拖动 > 标记拖动 > 标记吸附预览 > 曲线悬停高亮。

        交互路径全部走 blit 局部刷新（除平移需整幅重绘，因轴范围在变），
        避免每个 mouse move 事件触发约 125 ms 的整幅光栅化。
        """
        # 平移优先：拖动中实时跟随，不参与悬停判定
        if self._panning:
            self._pan_to(event)
            return

        if event.inaxes is None:
            self._remove_highlight()
            # 移出绘图区：收起吸附预览，避免残留一条误导性的线
            if self._mark_mode and self._dragging_mark < 0:
                self._remove_mark_preview()
            return

        # 拖动标记线：实时移动（同样自动吸附到采样点）并刷新 Δt
        if self._dragging_mark >= 0:
            if event.xdata is not None:
                self._move_mark(self._dragging_mark,
                                self._snap_time(event.xdata, event.inaxes))
            return

        # 标记模式：显示吸附预览线，提示即将落在哪个 CAN 帧上
        if self._mark_mode:
            if event.xdata is not None:
                self._update_mark_preview(
                    self._snap_time(event.xdata, event.inaxes))
            return

        # 悬停命中 + 坐标文本：**合并为一次 blit** 输出。
        # 原先两者各自 blit：坐标文本那次 restore 会把提示窗擦掉、提示窗那次
        # 又把坐标文本擦掉，且各自触发一次 widget 重绘 → 鼠标一动提示窗就闪。
        artists = self._update_coord_text(event)
        artists.extend(self._hover_probe(event))
        if artists:
            self._blit_artists(artists)

    def _update_coord_text(self, event) -> list:
        """更新当前 axes 左下角的实时坐标文本；返回待重绘 artist（不自行 blit）。

        这里**不能 blit**：blit 会 restore 整块背景位图，把上一次画上去的悬停
        提示窗一并抹掉；反过来悬停那次 blit 又会抹掉坐标文本。统一交给
        _on_mouse_move 合并成一次输出，才能既不闪烁又不重复重绘。
        """
        ax_id = id(event.inaxes)
        txt = self._coord_texts.get(ax_id)
        if txt is None or event.xdata is None or event.ydata is None:
            return []
        txt.set_text(f"x={event.xdata:.4f}  y={event.ydata:.4f}")
        return [txt]

    def _hover_probe(self, event) -> list:
        """在鼠标所在 axes 中找最近曲线点，命中则高亮 + 提示窗。

        **状态幂等**：命中的仍是同一个数据点时直接返回，不重算避让、不重设
        文本、不重绘 —— 用户视角就是「鼠标微动但没换点，提示窗纹丝不动」。

        返回待重绘 artist 列表（不自行 blit，由调用方合并输出）。
        """
        nearest = self._nearest_point(event)
        if nearest is None:
            if self._hover_state is None:
                return []                  # 本来就没有提示窗，无需重绘
            return self._remove_highlight(blit=False)
        line, point = nearest
        state = (id(line), round(float(point[0]), 9), round(float(point[1]), 9))
        if state == self._hover_state:
            return []                      # 命中点未变：整帧跳过
        self._hover_state = state
        return self._apply_highlight(line, point, event, blit=False)

    def _point_px(self, event, point):
        """数据点 → 屏幕像素坐标；变换不可用时返回 None。"""
        try:
            return event.inaxes.transData.transform(point)
        except Exception:  # noqa: BLE001
            return None

    def _nearest_point(self, event):
        """返回 (line, (x, y))——鼠标所在 axes 内最近的数据点；无命中返回 None。

        带**切换滞后**（解决「提示窗在两点间快速抖动」）：
        ① 若当前已锁定某点，且鼠标仍在它 HOVER_SWITCH_PX 像素内 → 直接沿用该点，
           完全不重新搜索，鼠标微动不会换点；
        ② 只有移出该范围才重新找最近点；
        ③ 判定「未命中」用更宽的 HOVER_RELEASE_FACTOR 倍阈值，形成滞回，
           避免在阈值边界上反复开关提示窗。
        """
        ax = event.inaxes
        if ax is None or event.xdata is None:
            self._release_hover_lock()
            return None

        # ── ① 滞后：仍停留在已锁定点附近则直接沿用 ──
        locked = self._hover_locked
        if locked is not None and self._hover_locked_axes is ax:
            l_line, l_point = locked
            still_visible = l_line in ax.get_lines()
            if still_visible:
                mpx = self._point_px(event, l_point)
                if mpx is not None:
                    dist_px = ((mpx[0] - event.x) ** 2
                               + (mpx[1] - event.y) ** 2) ** 0.5
                    if dist_px <= HOVER_SWITCH_PX:
                        return l_line, l_point
            else:
                self._release_hover_lock()

        # ── ② 重新搜索最近点 ──
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()
        x_range = max(xlim[1] - xlim[0], 1e-10)
        y_range = max(ylim[1] - ylim[0], 1e-10)

        best_line = None
        best_dist = float("inf")
        best_point = None

        for line in ax.get_lines():
            if (getattr(line, "_is_time_mark", False)
                    or getattr(line, "_is_mark_preview", False)):
                continue
            if line is self._hover_point:
                continue
            if line in self._pinned_lines:
                continue
            xdata = line.get_xdata()
            ydata = line.get_ydata()
            if len(xdata) == 0:
                continue
            idx = int(np.clip(np.searchsorted(xdata, event.xdata), 0, len(xdata) - 1))
            candidates = [idx]
            if idx > 0:
                candidates.append(idx - 1)
            if idx < len(xdata) - 1:
                candidates.append(idx + 1)
            for ci in candidates:
                dx = (xdata[ci] - event.xdata) / x_range
                dy = (ydata[ci] - event.ydata) / y_range
                dist = dx * dx + dy * dy
                if dist < best_dist:
                    best_dist = dist
                    best_line = line
                    best_point = (float(xdata[ci]), float(ydata[ci]))

        if best_line is None or best_point is None:
            self._release_hover_lock()
            return None

        # ── ③ 命中判定：已锁定时用更宽的滞回阈值 ──
        limit = 0.001 * (HOVER_RELEASE_FACTOR if self._hover_locked is not None else 1.0)
        if best_dist >= limit:
            self._release_hover_lock()
            return None

        # 换点后做一次像素级确认：与旧点太近则不切换，保持视觉稳定
        if locked is not None and self._hover_locked_axes is ax:
            _, l_point = locked
            old_px = self._point_px(event, l_point)
            new_px = self._point_px(event, best_point)
            if old_px is not None and new_px is not None:
                gap = ((new_px[0] - old_px[0]) ** 2
                       + (new_px[1] - old_px[1]) ** 2) ** 0.5
                if gap < HOVER_SWITCH_PX:
                    return locked[0], l_point

        self._hover_locked = (best_line, best_point)
        self._hover_locked_axes = ax
        return best_line, best_point

    def _release_hover_lock(self):
        """解除悬停锁定（鼠标离开绘图区 / 未命中 / 重绘时调用）。"""
        self._hover_locked = None
        self._hover_locked_axes = None

    def _apply_highlight(self, line, point, event, blit: bool = True) -> list:
        """高亮曲线并显示注释（含 DBC 值描述）；返回待重绘的 artist 列表。

        性能：不再每帧 remove + annotate 重建 artist，而是「复用同一组 artist +
        blit 局部刷新」。实测每帧由 236 ms 降至约 5 ms。
        blit=False 时只改 artist 状态，由调用方把所有 artist 合并成一次 blit。
        """
        # 恢复之前的线宽（仅对非固定曲线）
        prev = self._highlighted_line
        if (prev is not None and prev is not line
                and prev not in self._pinned_lines):
            prev.set_linewidth(self._original_linewidth)

        # 已经加粗的曲线不重复 set_linewidth：该调用会标脏 artist，而线宽变更
        # 属于「背景变化」（见下方 _invalidate_bg_cache 分支），重复触发纯属浪费。
        if line is not self._highlighted_line:
            line.set_linewidth(4)
            self._highlighted_line = line

        ax = line.axes
        label = line.get_label()
        x, y = point
        text = self._build_point_text(line, x, y, label)

        # 颜色统一转 hex（bbox / arrowprops 不接受元组）
        color = self._line_color_hex(line)

        artists = []
        # 线宽变化属于「背景变化」：必须让缓存失效并整幅重画一次，否则 blit 会把
        # 旧线宽贴回去。仅在「高亮对象切换」时才需要，同一曲线内移动不触发。
        if prev is not line:
            self._invalidate_bg_cache()
        else:
            artists.append(line)

        self._ensure_hover_artists(ax)
        if self._hover_ax is not ax:
            self._invalidate_bg_cache()

        self._hover_point.set_data([x], [y])
        self._hover_point.set_markerfacecolor(color)
        self._hover_point.set_markeredgecolor(color)
        self._update_hover_ann(text, x, y, ax, color)
        artists.extend([self._hover_point, self._hover_ann])

        self._annotation = self._hover_ann
        if blit:
            self._blit_artists(artists)
        return artists

    def _build_point_text(self, line, x, y, label) -> str:
        """组装「数据点说明」框文本（含 DBC 值描述）。"""
        sig_name = self._line_sig_name.get(
            line, label.split(".")[-1] if "." in label else label)
        val_int = int(round(y))
        desc = ""
        if sig_name in self._value_descriptions:
            if val_int in self._value_descriptions[sig_name]:
                desc = f" ({self._value_descriptions[sig_name][val_int]})"
        return f"{label}\n值: {y:.4f}{desc}\n时间: {x:.4f}s"

    @staticmethod
    def _line_color_hex(line) -> str:
        """取曲线颜色并统一转成 hex 字符串。"""
        color = line.get_color()
        if hasattr(color, "__iter__") and not isinstance(color, str):
            color = mcolors.to_hex(color)
        return color

    def _ensure_hover_artists(self, ax):
        """惰性创建/迁移可复用的悬停 artist（高亮点 + 说明框）。

        说明框只用**相对数据点的 offset points** 定位，因此跨 axes 复用是安全的，
        无需因换 axes 而重建。历史上每帧重建是悬停卡顿的主因之一。
        """
        if self._hover_point is not None and self._hover_ann is not None:
            if self._hover_ax is not ax:
                # 迁移到新 axes：先移除旧的，避免残留在旧子图上
                self._discard_hover_artists()
            else:
                return
        self._hover_point, = ax.plot(
            [], [], marker="o", linestyle="none", markersize=6,
            markeredgewidth=1.2, zorder=HOVER_ZORDER - 1,
        )
        self._hover_ann = ax.annotate(
            "", xy=(0, 0), xytext=(HOVER_OFFSET_PTS, HOVER_OFFSET_PTS),
            textcoords="offset points",
            bbox=dict(boxstyle="round,pad=0.4", facecolor="#252535",
                      edgecolor="#4fc3f7", alpha=0.92),
            fontsize=9, color="#e0e0e0",
            arrowprops=dict(arrowstyle="->", color="#4fc3f7", lw=1.2),
            zorder=HOVER_ZORDER,
        )
        self._hover_ax = ax

    def _discard_hover_artists(self):
        """销毁悬停 artist（重绘 / 切换 axes 时调用）。"""
        for art in (self._hover_point, self._hover_ann):
            if art is not None:
                try:
                    art.remove()
                except Exception:  # noqa: BLE001
                    pass
        self._hover_point = None
        self._hover_ann = None
        self._hover_ax = None
        self._hover_state = None
        if self._annotation is not None and self._annotation is not None:
            # _annotation 可能指向已销毁的悬停框或固定框，这里不强清，
            # 由各自的创建/移除路径负责；仅在它等于被销毁的悬停框时置空。
            pass

    def _update_hover_ann(self, text, x, y, ax, color):
        """更新复用中的悬停说明框：文本、颜色、避让位置。"""
        ann = self._hover_ann
        ann.xy = (x, y)
        ann.set_text(text)
        if not ann.get_visible():
            # _remove_highlight 会把整框（含箭头）隐藏，重新命中时要恢复可见
            ann.set_visible(True)
        bb = ann.get_bbox_patch()
        if bb is not None:
            bb.set_edgecolor(color)
        try:
            ann.arrow_patch.set_color(color)
        except Exception:  # noqa: BLE001
            pass
        # 避让时把**已钉住的固定提示窗**算作障碍：这样「悬停框压住已钉住的
        # 框」也会被自动化解。exclude 传自身属于保险（悬停框目前不在
        # _pinned_annotations 内，本就不会被当成障碍）。
        off = self._choose_annotation_offset(ax, x, y, text,
                                             exclude=self._hover_ann)
        ann.xytext = off
        ann.set_ha("left")
        ann.set_va("bottom")

    def _existing_ann_rects(self, ax, exclude=None):
        """返回该子图上**已有固定提示窗**的像素矩形列表，用于避让打分。

        exclude：创建时传入「自身」以便更新已有框位置时排除自己，
        否则框会永远认为自己压着自己，无法移动到最优位置。
        """
        rects = []
        try:
            renderer = self._canvas.get_renderer()
        except Exception:  # noqa: BLE001
            return rects
        for line, anns in self._pinned_annotations.items():
            if anns is None:
                continue
            for ann in anns:
                if ann is exclude:
                    continue
                if getattr(ann, "axes", None) is not ax:
                    continue
                bb = self._ann_box_px(ann, renderer)
                if bb is None:
                    continue
                rects.append(bb)
        return rects

    @staticmethod
    def _ann_box_px(ann, renderer):
        """取说明框的像素矩形 (x0, y0, x1, y1)。

        优先用创建时**自算并存下**的 `ann._px_rect`，理由（均实测）：
        * `ann.get_window_extent()` 是「文本框+箭头」的联合 bbox，
          相邻两点的箭头指向同一片区域 → 任意两框恒判重叠 94.9%，避让失效；
        * `ann.get_bbox_patch().get_window_extent()` 虽为纯框体，但在 figure
          未 draw() 时返回 (-0.4,-0.4,1.8,1.8)；而钉窗走 draw_idle()，
          连续钉窗时上一框尚未绘制 → 读到垃圾值，避让同样失效（重叠 89%）。
        自算矩形与绘制状态无关，也不含箭头。
        渲染器/旧对象缺失 `_px_rect` 时按旧法兜底（至少不会漏掉障碍）。
        """
        rect = getattr(ann, "_px_rect", None)
        if rect is not None:
            return rect
        try:
            bb = ann.get_window_extent(renderer)
        except Exception:  # noqa: BLE001
            return None
        return (bb.x0, bb.y0, bb.x1, bb.y1)

    @staticmethod
    def _overlap_area(rect, others, pad):
        """当前框与一组矩形的重叠面积（各方向外扩 pad 像素）。"""
        if not others:
            return 0.0
        bx0, by0, bx1, by1 = rect
        total = 0.0
        for ox0, oy0, ox1, oy1 in others:
            ix = min(bx1, ox1 + pad) - max(bx0, ox0 - pad)
            iy = min(by1, oy1 + pad) - max(by0, oy0 - pad)
            if ix > 0 and iy > 0:
                total += ix * iy
        return total

    def _choose_annotation_offset(self, ax, x, y, text, exclude=None):
        """在候选方位中选「最不碍事」的说明框位置。

        优先级（字典序）：① 框体尽量不出绘图区；
        ② 尽量不压到**已有的固定提示窗**（解决「点得近时两个框重叠」）；
        ③ 尽量不覆盖任何曲线。
        若所有候选都无法兼顾，则退化为遮挡最少且不出界的那个
        —— 按需求：没办法避开时也允许覆盖曲线。
        """
        try:
            px, py = ax.transData.transform((x, y))
            axb = ax.bbox
        except Exception:  # noqa: BLE001
            return (HOVER_OFFSET_PTS, HOVER_OFFSET_PTS)

        bw, bh = self._measure_text_px(ax, text)
        pad = HOVER_OFFSET_PTS
        # 4 个角落方位 + 2 个「垂直拉开」方位：
        # 数据点密集时，同一角落的框必然重叠，靠上下拉开才能错开。
        cands = [
            (pad, pad), (-bw - pad, pad),
            (pad, -bh - pad), (-bw - pad, -bh - pad),
            (pad, -(2.0 * bh + pad)), (-bw - pad, 2.0 * bh + pad),
            (pad, 2.0 * bh + pad), (-bw - pad, -(2.0 * bh + pad)),
        ]
        # 先做「贴边翻转」，保证首个候选就是历史最自然的位置
        cands[0] = self._flip_offset_at_edge(ax, x, y, cands[0])

        margin = ANN_EDGE_MARGIN
        curves = self._visible_curve_pixels(ax)
        existing = self._existing_ann_rects(ax, exclude=exclude)
        best, best_key = cands[0], None
        for ox, oy in cands:
            bx0, by0 = px + ox, py + oy
            bx1, by1 = bx0 + bw, by0 + bh
            # 出界量（像素）：越靠边越大，用于第一优先级排序
            outside = (max(0.0, axb.x0 + margin - bx0)
                       + max(0.0, bx1 - (axb.x1 - margin))
                       + max(0.0, axb.y0 + margin - by0)
                       + max(0.0, by1 - (axb.y1 - margin)))
            hits = self._count_curve_hits(curves, bx0, by0, bx1, by1)
            ovl = self._overlap_area((bx0, by0, bx1, by1), existing,
                                     ANN_OVERLAP_PAD)
            # 优先级：不出界 > 不压已有框 > 不压曲线。
            # ⚠️ ovl 必须排在 hits **之前**：若把「压曲线」提前，则
            #   (不压框但压 6 个曲线点) 会输给 (压住已有框但压 0 个曲线点)，
            #   两个相近的框会双双跑到同一角落、几乎完全重合（实测 88%）。
            #   用户明确要求「不能重叠」，而「实在避不开时压曲线」可接受。
            key = (round(outside, 1), 1 if ovl > 0.0 else 0, hits)
            if best_key is None or key < best_key:
                best, best_key = (ox, oy), key
            if outside == 0.0 and hits == 0 and ovl == 0.0:
                break                      # 已经完美，无需再试
        return best

    def _measure_text_px(self, ax, text):
        """实测多行文本的像素尺寸（取不到渲染器时按字符数估算）。

        每帧新建/销毁临时 annotate 来测量代价约 0.7 ms，而悬停时文本逐帧变化
        的是数值部分、**行数与最长行几乎不变**。故按「行结构」缓存：只有行数
        或最长行长度变化时才重新测量，逐帧移动时命中缓存（省掉 0.7 ms/帧）。
        """
        lines = text.split("\n")
        shape = (len(lines), max((len(s) for s in lines), default=0))
        cached = self._ann_size_cache.get(shape)
        if cached is not None:
            return cached
        try:
            renderer = self._canvas.get_renderer()
            # ⚠️ 锚点必须取在**绘图区中心**（数据坐标下的中点）：
            # 旧实现锚在数据坐标 (0,0)，该点常紧贴绘图区左/下边缘，
            # 测得的 bbox 会被边界压缩 —— 实测 76.0 x 41.8，而真值 91.1 x 57.6，
            # 低估 17%/27%，使避让打分整体偏小、误判为「不重叠」。
            xm = (ax.get_xlim()[0] + ax.get_xlim()[1]) / 2.0
            ym = (ax.get_ylim()[0] + ax.get_ylim()[1]) / 2.0
            ann = ax.annotate(
                text, xy=(xm, ym), xytext=(0, 0), textcoords="offset points",
                bbox=dict(boxstyle="round,pad=0.4", facecolor="#252535",
                          edgecolor="#4fc3f7"),
                fontsize=9, color="#e0e0e0",
            )
            # 与 _ann_box_px 保持同一口径（get_window_extent），
            # 否则候选框估算位置与已有框实际位置尺度不一致、重叠打分失真。
            bb = ann.get_window_extent(renderer)
            ann.remove()
            size = (float(bb.width), float(bb.height))
        except Exception:  # noqa: BLE001
            size = (shape[1] * 6.2 + 16.0, shape[0] * 15.0 + 14.0)
        if len(self._ann_size_cache) > 32:
            self._ann_size_cache.clear()
        self._ann_size_cache[shape] = size
        return size

    def _visible_curve_pixels(self, ax) -> np.ndarray:
        """该子图上所有数据曲线的屏幕坐标（抽稀后），形如 (N,2) float 数组。

        用于遮挡测试：只取抽稀后的点，够判断「框体压没压到曲线」。
        ⚠️ 结果按「axes 身份 + 轴范围 + 绘图区尺寸 + 曲线条数」缓存：悬停时
        鼠标每移动一像素都要做一次遮挡测试，而坐标变换（transData.transform）
        对 4 条曲线 × 240 点并不便宜——不缓存会成为悬停路径的主要开销。
        轴范围变化（平移/缩放）或曲线增删时指纹改变，自动重算。
        """
        try:
            xlim = ax.get_xlim()
            ylim = ax.get_ylim()
            bb = ax.bbox
            nlines = len(ax.get_lines())
            fp = (id(ax), round(xlim[0], 9), round(xlim[1], 9),
                  round(ylim[0], 9), round(ylim[1], 9),
                  round(bb.width, 3), round(bb.height, 3), nlines)
        except Exception:  # noqa: BLE001
            fp = None
        if fp is not None:
            cached = self._curve_px_cache.get(fp)
            if cached is not None:
                return cached

        chunks = []
        for line in ax.get_lines():
            if (getattr(line, "_is_time_mark", False)
                    or getattr(line, "_is_mark_preview", False)):
                continue
            if line is self._hover_point:
                continue
            try:
                xs = np.asarray(line.get_xdata(), dtype=float)
                ys = np.asarray(line.get_ydata(), dtype=float)
            except Exception:  # noqa: BLE001
                continue
            if xs.size == 0 or ys.size == 0 or xs.size != ys.size:
                continue
            if xs.size > ANN_AVOID_SAMPLES:
                idx = np.linspace(0, xs.size - 1, ANN_AVOID_SAMPLES).astype(int)
                xs, ys = xs[idx], ys[idx]
            chunks.append(np.column_stack((xs, ys)))
        if not chunks:
            result = np.empty((0, 2), dtype=float)
        else:
            pts = np.vstack(chunks)
            try:
                result = ax.transData.transform(pts)
            except Exception:  # noqa: BLE001
                result = np.empty((0, 2), dtype=float)
        if fp is not None:
            if len(self._curve_px_cache) > 32:
                self._curve_px_cache.clear()
            self._curve_px_cache[fp] = result
        return result

    @staticmethod
    def _count_curve_hits(curve_px: np.ndarray, x0, y0, x1, y1) -> int:
        """落在给定像素矩形内的曲线点数（无曲线时返回 0）。"""
        if curve_px.size == 0:
            return 0
        inside = ((curve_px[:, 0] >= x0) & (curve_px[:, 0] <= x1)
                  & (curve_px[:, 1] >= y0) & (curve_px[:, 1] <= y1))
        return int(inside.sum())

    def _ann_index_at(self, event):
        """返回被点击位置命中的固定注释框 (line, 序号)；未命中返回 (None, -1)。

        用于「左键/右键关闭某一个提示窗」——必须能判定点到了哪一个框，
        故需遍历所有固定注释框，按**纯框体**矩形（不含箭头，见 _ann_box_px）
        做命中测试；命中多个时取面积最小的那个。
        """
        if event.x is None or event.y is None:
            return None, -1
        try:
            renderer = self._canvas.get_renderer()
        except Exception:  # noqa: BLE001
            return None, -1
        ex, ey = float(event.x), float(event.y)
        best, best_area = (None, -1), None
        for line, anns in self._pinned_annotations.items():
            if anns is None:
                continue
            for i, ann in enumerate(anns):
                # 用自算矩形：get_window_extent 的联合 bbox 含箭头，
                # 会把「箭头下方一大片空白」也算成命中区，点空白即误关窗。
                rect = self._ann_box_px(ann, renderer)
                if rect is None:
                    continue
                bx0, by0, bx1, by1 = rect
                if (bx0 <= ex <= bx1) and (by0 <= ey <= by1):
                    area = (bx1 - bx0) * (by1 - by0)
                    if best_area is None or area < best_area:
                        best, best_area = (line, i), area
        return best

    def _create_point_annotation(self, ax, text, x, y, color,
                                 facecolor="#252535", offset=None):
        """创建「数据点说明」框（点击固定用；悬停走复用 artist，见 _ensure_hover_artists）。

        统一解决两个历史问题：
        ① 贴边看不全：按 4 候选方位打分选位（先避免出界，再避免遮挡曲线），
           见 _choose_annotation_offset；
        ② 被图例遮挡：annotate 默认 zorder=3，低于图例(5) 与 Δt 提示框(12)，
           故显式抬到 HOVER_ZORDER(20)。
        """
        if offset is None:
            # 先按「不考虑已有框」的方位建出来，拿到 artist 之后再用 exclude=自身
            # 重算一次方位 —— 此时它才能把自己从障碍集合里排除、把其它框算进去。
            off = self._choose_annotation_offset(ax, x, y, text)
        else:
            off = offset
        ann = ax.annotate(
            text, xy=(x, y), xytext=off,
            textcoords="offset points",
            bbox=dict(boxstyle="round,pad=0.4", facecolor=facecolor,
                      edgecolor=color, alpha=0.92),
            fontsize=9, color="#e0e0e0",
            arrowprops=dict(arrowstyle="->", color=color, lw=1.2),
            zorder=HOVER_ZORDER,
        )
        self._ensure_ann_inside(ann, ax)
        if offset is None:
            # 二次定方位：避开已有固定框（避免「点得近时两个框重叠」）。
            off2 = self._choose_annotation_offset(ax, x, y, text, exclude=ann)
            if off2 != off:
                ann.set_position(off2)
                self._ensure_ann_inside(ann, ax)
        # 记下**自算**的框体像素矩形：后续避让打分 / 命中测试都用它，
        # 以免依赖 artist 的绘制状态（详见 _ann_box_px 的说明）。
        self._store_ann_px_rect(ann, ax)
        return ann

    def _store_ann_px_rect(self, ann, ax):
        """按「锚点像素 + offset(points→px) + 文本实测尺寸」自算框体矩形并缓存。

        实测与真实 patch 的误差约 5~8px（annotate 默认 va='baseline' 所致），
        对于「两个框是否压在一起」的打分完全够用。
        """
        try:
            canvas = self._canvas
            k = 72.0 / (float(canvas.figure.dpi) or 100.0)   # points → px
            ox, oy = ann.get_position()
            x, y = ann.xy
            px, py = ax.transData.transform((x, y))
            bw, bh = self._measure_text_px(ax, ann.get_text())
            x0 = px + ox / k
            y0 = py + oy / k
            ann._px_rect = (x0, y0, x0 + bw, y0 + bh)
        except Exception:  # noqa: BLE001
            ann._px_rect = None

    def _ensure_ann_inside(self, ann, ax):
        """按实测框体尺寸把说明框夹回绘图区内（渲染器不可用时跳过）。

        与旧的 _clamp_annotation_to_axes 的区别：不再臆造 offset 修正量
        （offset 是相对**数据点**的，而越界是相对**绘图区**的，两者原点不同，
        旧址改量在数据点贴边时会修正不足甚至来回震荡），而是直接读取框体
        实际像素位置，把需要的位移换算成 offset points 一次性回写。
        """
        canvas = getattr(self, "_canvas", None)
        try:
            renderer = canvas.get_renderer()
            # ⚠️ 这里必须用 ann.get_window_extent()，不能用 bbox_patch：
            # 本方法在 figure draw() **之前**被调用，此时 bbox_patch 尚未
            # 布局，会返回 (-0.4,-0.4,1.8,1.8) 垃圾值，进而算出巨大的位移
            # 把提示框推到绘图区角落（已实测踩坑）。
            bb = ann.get_window_extent(renderer)
        except Exception:  # noqa: BLE001
            return
        ax_bb = ax.bbox
        dx = dy = 0.0
        margin = ANN_EDGE_MARGIN
        if bb.x1 > ax_bb.x1 - margin:
            dx = (ax_bb.x1 - margin) - bb.x1
        if bb.x0 + dx < ax_bb.x0 + margin:
            dx = (ax_bb.x0 + margin) - bb.x0
        if bb.y1 > ax_bb.y1 - margin:
            dy = (ax_bb.y1 - margin) - bb.y1
        if bb.y0 + dy < ax_bb.y0 + margin:
            dy = (ax_bb.y0 + margin) - bb.y0
        if not dx and not dy:
            return
        try:
            k = 72.0 / (float(canvas.figure.dpi) or 100.0)
        except Exception:  # noqa: BLE001
            k = 72.0 / 100.0
        try:
            ox, oy = ann.get_position()
            ann.set_position((ox + dx * k, oy + dy * k))
        except Exception:  # noqa: BLE001
            pass
        else:
            # 位置变了 → 自算矩形同步刷新，否则后续避让仍按旧位置打分。
            if getattr(ann, "_px_rect", None) is not None:
                ax_ = getattr(ann, "axes", None)
                if ax_ is not None:
                    self._store_ann_px_rect(ann, ax_)

    def _clamp_annotation_to_axes(self, ann, ax):
        """向后兼容：等价于 _ensure_ann_inside。"""
        self._ensure_ann_inside(ann, ax)

    @staticmethod
    def _flip_offset_at_edge(ax, x, y, off):
        """数据点靠绘图区右/上边缘时，把朝正方向的偏移反向，避免框体一出场就出界。"""
        ox, oy = off
        try:
            px, py = ax.transData.transform((x, y))
            bx0, by0, bw, bh = ax.bbox.bounds
        except Exception:  # noqa: BLE001
            return off
        if not bw or not bh:
            return off
        if (px - bx0) / bw > HOVER_FLIP_RATIO and ox > 0:
            ox = -ox
        if (py - by0) / bh > HOVER_FLIP_RATIO and oy > 0:
            oy = -oy
        return (ox, oy)

    def _remove_highlight(self, blit: bool = True) -> list:
        """移除高亮和注释；返回待重绘的 artist 列表。

        提示窗必须**整体 set_visible(False)**，不能只把文本置空：文本为空时
        matplotlib 仍会绘制 arrow_patch（箭头依旧指向上次的数据点），于是鼠标
        离开曲线后会在曲线上残留一小截箭头。

        blit=False 时只改状态，由调用方合并成一次 blit（多次 blit 会互相
        restore 擦除）。状态本就没变化时不重绘 —— 幂等。
        """
        had = False
        if self._highlighted_line is not None:
            self._highlighted_line.set_linewidth(self._original_linewidth)
            # 线宽恢复属于背景变化 → 背景缓存失效
            self._invalidate_bg_cache()
            self._highlighted_line = None
            had = True
        artists = []
        if self._hover_ann is not None and self._hover_ann.get_visible():
            self._hover_ann.set_visible(False)
            self._hover_ann.set_text("")
            self._hover_point.set_data([], [])
            self._annotation = None
            artists = [self._hover_point, self._hover_ann]
            had = True
        self._hover_state = None
        if had and blit:
            # 传 [None, None] 也算「有 artist」，可确保 _blit_artists 内部仍会
            # 补画常驻图层，并把恢复后的线宽贴回画面。
            self._blit_artists(artists if artists
                               else [self._hover_point, self._hover_ann])
        return artists

    def _on_scroll(self, event):
        """滚轮缩放。

        - **按住 Ctrl**：以鼠标位置为中心，X/Y 同时等比放大或缩小
          （「放大鼠标位置的曲线图」）。
        - 未按 Ctrl：沿用原有逻辑，按鼠标在绘图区中的相对位置判断缩放轴
          （贴近下边缘缩 X、贴近左边缘缩 Y，否则默认缩 X）。
        """
        if event.inaxes is None:
            return

        ax = event.inaxes
        # Issue 3: 用 QApplication.keyboardModifiers() 而非 event.guiEvent.modifiers()
        # —— 前者在 Qt 各版本/各平台上都可靠。
        modifiers = QApplication.keyboardModifiers()
        ctrl_pressed = bool(modifiers & Qt.ControlModifier)

        # up 是往「放大」的方向（缩小显示范围），down 反之
        scale_factor = 0.85 if event.button == "up" else 1.15

        if ctrl_pressed:
            self._zoom_at_cursor(ax, event, scale_factor)
        else:
            xlim = ax.get_xlim()
            ylim = ax.get_ylim()
            rel_y = (event.ydata - ylim[0]) / max(ylim[1] - ylim[0], 1e-10)
            rel_x = (event.xdata - xlim[0]) / max(xlim[1] - xlim[0], 1e-10)

            if rel_y < 0.1:
                self._zoom_axis(ax, "x", event.xdata, scale_factor)
            elif rel_x < 0.1:
                self._zoom_axis(ax, "y", event.ydata, scale_factor)
            else:
                # 默认 X 轴缩放
                self._zoom_axis(ax, "x", event.xdata, scale_factor)

        self._invalidate_bg_cache()
        self._canvas.draw_idle()

    def _zoom_at_cursor(self, ax, event, scale_factor):
        """以鼠标位置为中心同时缩放 X/Y 两个轴。

        X 轴用鼠标的 xdata、Y 轴用鼠标的 ydata 作为不动点，因此鼠标指向的
        那个数据点会保持在原屏幕位置——即「放大鼠标位置的曲线图」。
        若鼠标在某个方向上超出了数据范围（例如 Y 轴留白处），则退化为以
        当前范围中心为该方向的锚点，避免轴范围被拉向无限远。
        """
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()

        cx = event.xdata if event.xdata is not None else (xlim[0] + xlim[1]) / 2
        cy = event.ydata if event.ydata is not None else (ylim[0] + ylim[1]) / 2
        # 锚点超出当前范围时夹回边界内侧，保证缩放围绕绘图区内的一点
        cx = min(max(cx, min(xlim[0], xlim[1])), max(xlim[0], xlim[1]))
        cy = min(max(cy, min(ylim[0], ylim[1])), max(ylim[0], ylim[1]))

        # 放大方向：上下各留 CTRL_ZOOM_Y_PAD 的比例余量，避免曲线紧贴上下边
        pad = 1.0 + CTRL_ZOOM_Y_PAD * (1.0 - scale_factor) / 0.15

        lo = cy - (cy - ylim[0]) * scale_factor * pad
        hi = cy + (ylim[1] - cy) * scale_factor * pad
        if abs(hi - lo) > 1e-12:
            ax.set_ylim(lo, hi)

        new_lo = cx - (cx - xlim[0]) * scale_factor
        new_hi = cx + (xlim[1] - cx) * scale_factor
        if abs(new_hi - new_lo) > 1e-12:
            ax.set_xlim(new_lo, new_hi)

    def _zoom_axis(self, ax, axis, center, scale_factor):
        """以 center 为中心缩放指定轴"""
        if axis == "x":
            lo, hi = ax.get_xlim()
            new_lo = center - (center - lo) * scale_factor
            new_hi = center + (hi - center) * scale_factor
            ax.set_xlim(new_lo, new_hi)
        else:
            lo, hi = ax.get_ylim()
            new_lo = center - (center - lo) * scale_factor
            new_hi = center + (hi - center) * scale_factor
            ax.set_ylim(new_lo, new_hi)

    def _on_click(self, event):
        """鼠标按下：标记线拖动 / 放置标记 / 右键关闭提示窗 / 左键平移或钉提示窗。

        手势分工（按用户确认）：
        - 左键按住并拖动 → 实时平移（拖动中动态显示）；
        - 左键按下后位移 < PAN_CLICK_PX → 视为单击，钉住该点提示窗；
        - 左键按在已有标记线附近 → 拖动该标记线；
        - **左键点在某个固定提示窗上 → 关闭该提示窗**（与右键等价）；
        - 右键点在某个固定提示窗上 → 关闭该提示窗；否则清空所有标记。
        """
        if event.inaxes is None or event.xdata is None:
            # 右键在绘图区外也允许清标记，保持原行为
            if event.button == 3:
                self._clear_marks()
            return

        # 左键点在已有固定提示窗上：关闭它（与右键等价）。
        # 必须放在**所有左键分支之前** —— 否则会被当作平移起点，
        # release 时位移不足又被当成单击、在框上再钉一个新窗（越点越多）。
        # 悬停窗不在此列：它是复用 artist，鼠标移出即自动收起，无需手动关闭。
        if event.button == 1:
            line, ai = self._ann_index_at(event)
            if line is not None and self._close_pinned_ann(line, ai):
                # 关窗后鼠标仍停在原处，避免立刻又触发悬停判定
                self._remove_highlight()
                return

        # 左键按在已有标记线附近：进入拖动（不依赖标记模式，放置完也能继续微调）
        if event.button == 1:
            idx = self._mark_index_near(event)
            if idx >= 0:
                self._dragging_mark = idx
                self._remove_highlight()
                self._remove_mark_preview()
                return

        # 右键：优先关闭点到的固定提示窗，否则清空标记
        if event.button == 3:
            line, ai = self._ann_index_at(event)
            if line is not None and self._close_pinned_ann(line, ai):
                return
            self._clear_marks()
            return

        # 时间差标记模式：放置标记（落点自动吸附到最近的 CAN 帧采样点）
        if self._mark_mode and event.button == 1:
            self._add_mark(self._snap_time(event.xdata, event.inaxes))
            if len(self._mark_points) >= MARK_MAX:
                # 三组放满后自动退出标记模式；标记线仍可随时拖动调整
                self._mark_mode = False
                self._mark_btn.setChecked(False)
                self._remove_mark_preview()
                self._update_mark_btn_text()
            return

        # 中键：直接进入平移（与左键拖动等价，便于在标记模式下平移）
        if event.button == 2:
            self._begin_pan(event)
            return

        # 左键：先记为「可能是单击也可能是拖动」，在 release 时按位移判定
        if event.button == 1:
            self._pan_pending = True
            self._pan_moved_px = 0.0
            self._pan_origin = (event.xdata, event.ydata, event.inaxes,
                                event.x, event.y)
            self._begin_pan(event, pending=True)

    def _begin_pan(self, event, pending=False):
        """进入平移状态：记录基准点（像素坐标 + 各轴当时的范围）。"""
        self._panning = True
        self._pan_last = (event.x, event.y)
        self._pan_moved_px = 0.0 if not pending else self._pan_moved_px
        try:
            self._pan_xlim = {id(ax): ax.get_xlim() for ax in self._fig.axes}
            self._pan_ylim = {id(ax): ax.get_ylim() for ax in self._fig.axes}
        except Exception:  # noqa: BLE001
            self._pan_xlim = {}
            self._pan_ylim = {}

    def _pan_to(self, event):
        """拖动中：按像素位移实时平移所有子图（动态显示，不整幅重绘）。"""
        if not self._panning or self._pan_last is None:
            return
        lx, ly = self._pan_last
        dx, dy = event.x - lx, event.y - ly
        self._pan_last = (event.x, event.y)
        self._pan_moved_px += (dx * dx + dy * dy) ** 0.5
        if dx == 0 and dy == 0:
            return

        moved = False
        for ax in self._fig.axes:
            try:
                x0, x1 = ax.get_xlim()
                y0, y1 = ax.get_ylim()
                bb = ax.bbox
                w = max(bb.width, 1e-9)
                h = max(bb.height, 1e-9)
                # 像素位移 → 数据位移（拖右则图形右移，即范围左移）
                ddx = -dx * (x1 - x0) / w
                ddy = -dy * (y1 - y0) / h
                ax.set_xlim(x0 + ddx, x1 + ddx)
                ax.set_ylim(y0 + ddy, y1 + ddy)
                moved = True
            except Exception:  # noqa: BLE001
                continue
        if moved:
            # 轴范围变了 → 背景缓存失效，必须整幅重绘（平移期间无法用 blit 省这一步）
            self._invalidate_bg_cache()
            self._canvas.draw_idle()

    def _end_pan(self, event):
        """结束平移；若几乎没动则判定为单击。"""
        moved = self._pan_moved_px
        self._panning = False
        self._pan_last = None
        self._invalidate_bg_cache()
        return moved

    def _find_nearest_line(self, event):
        """查找距离鼠标最近的曲线（仅在鼠标所在 axes 中搜索，归一化距离 < 阈值则返回）"""
        if event.inaxes is None:
            return None

        ax = event.inaxes
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()
        x_range = max(xlim[1] - xlim[0], 1e-10)
        y_range = max(ylim[1] - ylim[0], 1e-10)

        best_line = None
        best_dist = float("inf")

        for line in ax.get_lines():
            # 跳过时间差标记线与吸附预览线（非数据曲线）
            if (getattr(line, "_is_time_mark", False)
                    or getattr(line, "_is_mark_preview", False)):
                continue
            xdata = line.get_xdata()
            ydata = line.get_ydata()
            if len(xdata) == 0:
                continue

            idx = np.searchsorted(xdata, event.xdata)
            idx = np.clip(idx, 0, len(xdata) - 1)

            candidates = [idx]
            if idx > 0:
                candidates.append(idx - 1)
            if idx < len(xdata) - 1:
                candidates.append(idx + 1)

            for ci in candidates:
                dx = (xdata[ci] - event.xdata) / x_range
                dy = (ydata[ci] - event.ydata) / y_range
                dist = dx * dx + dy * dy
                if dist < best_dist:
                    best_dist = dist
                    best_line = line

        # 与悬停相同的阈值
        if best_dist < 0.001:
            return best_line
        return None

    def _toggle_pin(self, line, event):
        """在点击位置钉住一个提示窗（同一曲线最多 PIN_ANN_MAX 个，FIFO 淘汰）。

        与旧实现的区别：旧版每条曲线只能有一个固定注释，再次点击同一曲线会
        「取消固定」；新版每次点击都在该点新增一个提示窗，最多 3 个，超出时
        移除最早的。关闭请用右键点击该提示窗（见 _on_click）。
        """
        ax = line.axes
        label = line.get_label()
        xdata = line.get_xdata()
        if len(xdata) == 0:
            return
        idx = int(np.clip(np.searchsorted(xdata, event.xdata), 0, len(xdata) - 1))
        x = float(xdata[idx])
        y = float(line.get_ydata()[idx])

        text = self._build_point_text(line, x, y, label)
        color = self._line_color_hex(line)

        line.set_linewidth(4)
        self._pinned_lines.add(line)

        ann = self._create_point_annotation(
            ax, text, x, y, color, facecolor="#2b2b2b",
        )
        ann._is_pinned = True
        anns = self._pinned_annotations.setdefault(line, [])
        anns.append(ann)
        # 超出上限：FIFO 淘汰最早的一个
        while len(anns) > PIN_ANN_MAX:
            old = anns.pop(0)
            try:
                old.remove()
            except Exception:  # noqa: BLE001
                pass

        # 线宽变化 + 新增注释 → 背景变化，整幅重绘一次
        self._invalidate_bg_cache()
        self._canvas.draw_idle()

    def _close_pinned_ann(self, line, index) -> bool:
        """关闭指定曲线上的第 index 个固定提示窗；成功返回 True。"""
        anns = self._pinned_annotations.get(line)
        if not anns or index < 0 or index >= len(anns):
            return False
        ann = anns.pop(index)
        try:
            ann.remove()
        except Exception:  # noqa: BLE001
            pass
        if not anns:
            self._pinned_annotations.pop(line, None)
            self._pinned_lines.discard(line)
            # 该曲线已无提示窗：恢复原始线宽
            try:
                line.set_linewidth(self._original_linewidth)
            except Exception:  # noqa: BLE001
                pass
        self._invalidate_bg_cache()
        self._canvas.draw_idle()
        return True

    def _on_release(self, event):
        """鼠标释放：结束标记拖动 / 结束平移 / 位移过小则判定为单击（钉提示窗）。"""
        # 拖动标记线结束：只结束拖动，不触发平移
        if self._dragging_mark >= 0:
            self._dragging_mark = -1
            self._invalidate_bg_cache()
            self._canvas.draw_idle()
            return

        if not self._panning and not getattr(self, "_pan_pending", False):
            return

        moved = self._end_pan(event)
        pending = getattr(self, "_pan_pending", False)
        origin = getattr(self, "_pan_origin", None)
        self._pan_pending = False
        self._pan_origin = None

        if not pending or origin is None:
            return

        # 位移足够小 → 视为单击：在按下的位置钉一个提示窗
        if moved < PAN_CLICK_PX:
            nearest = self._find_nearest_line(event)
            if nearest is not None:
                self._toggle_pin(nearest, event)
