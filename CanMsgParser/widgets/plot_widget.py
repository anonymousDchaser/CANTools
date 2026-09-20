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
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QApplication
from PyQt5.QtCore import Qt
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
        self._annotation = None      # 悬停注释框
        self._highlighted_line = None
        self._original_linewidth = 1.8
        # Issue 5: DBC 值描述表 {sig_name: {int_val: "描述", ...}}
        self._value_descriptions: dict = {}
        # Issue 7: 点击固定高亮的曲线集合及其持久注释
        self._pinned_lines: set = set()
        self._pinned_annotations: dict = {}
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

        layout.addWidget(self._toolbar)
        layout.addWidget(self._canvas, stretch=1)

        # ─── 绑定交互事件 ───
        self._canvas.mpl_connect("motion_notify_event", self._on_mouse_move)
        self._canvas.mpl_connect("scroll_event", self._on_scroll)
        self._canvas.mpl_connect("button_press_event", self._on_click)
        self._canvas.mpl_connect("button_release_event", self._on_release)

        self._drag_start = None

    # ────────────────────── 公共接口 ──────────────────────

    def plot_signals(self, signals: list[DecodedSignal]):
        """绘制信号曲线"""
        self._signals = signals
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
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

        line = self._rt_lines.get(key)
        if line is not None:
            line.set_data(buf["t"], buf["v"])
            # 自动缩放（基于当前缓冲边界，即滚动窗口）
            ax = self._rt_axes.get(key)
            if ax is not None:
                ax.relim()
                ax.autoscale_view(scalex=True, scaley=True)
            self._canvas.draw_idle()

    def stop_realtime(self):
        """退出实时模式，保留最后一次画面。

        仅停止接收新采样（_rt_running=False），但保留 _realtime 状态、
        _rt_meta 与 _rt_buffers，使「停止监控」后点击「切换共享Y轴/独立子图」
        时仍能用已缓冲的数据重绘曲线，而不至于回退到「请勾选信号」占位图。
        """
        self._rt_running = False
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
                legend = ax.legend(loc="upper right", draggable=True, framealpha=0.85)
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
            legend = ax.legend(loc="upper right", draggable=True, framealpha=0.85)
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

        legend = ax.legend(loc="upper right", draggable=True, framealpha=0.85)
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

            legend = ax.legend(loc="upper right", draggable=True, framealpha=0.85)
            legend.get_frame().set_edgecolor("#3a3a4e")

        axes[-1].set_xlabel("时间 (s)", fontsize=11)

    def _downsample_if_needed(self, timestamps, values):
        """可视区域数据点超过阈值时降采样"""
        if len(timestamps) > DOWNSAMPLE_THRESHOLD:
            return lttb_downsample(timestamps, values, DOWNSAMPLE_THRESHOLD)
        return timestamps, values

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
        self._canvas.draw_idle()

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
        self._canvas.draw_idle()

    def _remove_mark_preview(self):
        for art in self._preview_artists:
            try:
                art.remove()
            except Exception:  # noqa: BLE001
                pass
        self._preview_artists = []

    # ────────────────────── 鼠标交互 ──────────────────────

    def _on_mouse_move(self, event):
        """鼠标移动：曲线悬停高亮 + 实时坐标显示 + 标记吸附预览 / 拖动"""
        if event.inaxes is None:
            self._remove_highlight()
            # 移出绘图区：收起吸附预览，避免残留一条误导性的线
            if self._mark_mode and self._dragging_mark < 0:
                self._remove_mark_preview()
            return

        # Issue 2: 更新当前 axes 的坐标显示
        ax_id = id(event.inaxes)
        if ax_id in self._coord_texts and event.xdata is not None and event.ydata is not None:
            self._coord_texts[ax_id].set_text(f"x={event.xdata:.4f}  y={event.ydata:.4f}")
            self._canvas.draw_idle()

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

        # 只在鼠标所在的 axes 中查找最近曲线，避免子图模式下跨 axes 误匹配
        ax = event.inaxes
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()
        x_range = max(xlim[1] - xlim[0], 1e-10)
        y_range = max(ylim[1] - ylim[0], 1e-10)

        best_line = None
        best_dist = float("inf")
        best_point = None

        for line in ax.get_lines():
            # 跳过时间差标记线与吸附预览线（它们不是数据曲线，y 只有 0~1）
            if (getattr(line, "_is_time_mark", False)
                    or getattr(line, "_is_mark_preview", False)):
                continue
            # Issue 7: 跳过已固定的曲线（它们有自己的持久注释）
            if line in self._pinned_lines:
                continue

            xdata = line.get_xdata()
            ydata = line.get_ydata()
            if len(xdata) == 0:
                continue

            # 找最近的数据点
            idx = np.searchsorted(xdata, event.xdata)
            idx = np.clip(idx, 0, len(xdata) - 1)

            # 检查前后两个点取最近的
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
                    best_point = (xdata[ci], ydata[ci])

        # 高亮阈值（归一化距离）
        if best_dist < 0.001 and best_line is not None:
            self._apply_highlight(best_line, best_point, event)
        else:
            self._remove_highlight()

    def _apply_highlight(self, line, point, event):
        """高亮曲线并显示注释（含 DBC 值描述）"""
        # 恢复之前的线宽（仅对非固定曲线）
        if (self._highlighted_line is not None
                and self._highlighted_line is not line
                and self._highlighted_line not in self._pinned_lines):
            self._highlighted_line.set_linewidth(self._original_linewidth)

        line.set_linewidth(4)
        self._highlighted_line = line

        # 更新或创建注释
        ax = line.axes
        label = line.get_label()
        x, y = point

        # 获取曲线自身的颜色，确保注释框和箭头颜色与曲线一致
        color = line.get_color()
        # matplotlib 的 get_color() 可能返回元组/数组而非字符串，
        # 需要转换为 hex 格式以供 bbox/arrowprops 使用
        if hasattr(color, '__iter__') and not isinstance(color, str):
            import matplotlib.colors as mcolors
            color = mcolors.to_hex(color)

        # Issue 5: 查找 DBC 值描述（用 line->sig_name 映射精确取信号名，
        #          兼容实时模式 label 带 (0xID) 后缀的情况）
        sig_name = self._line_sig_name.get(line, label.split(".")[-1] if "." in label else label)
        val_int = int(round(y))
        desc = ""
        if sig_name in self._value_descriptions:
            if val_int in self._value_descriptions[sig_name]:
                desc = f" ({self._value_descriptions[sig_name][val_int]})"

        text = f"{label}\n值: {y:.4f}{desc}\n时间: {x:.4f}s"

        if self._annotation is not None:
            self._annotation.remove()

        self._annotation = ax.annotate(
            text, xy=(x, y), xytext=(15, 15),
            textcoords="offset points",
            bbox=dict(boxstyle="round,pad=0.4", facecolor="#252535",
                      edgecolor=color, alpha=0.92),
            fontsize=9, color="#e0e0e0",
            arrowprops=dict(arrowstyle="->", color=color, lw=1.2),
        )
        self._canvas.draw_idle()

    def _remove_highlight(self):
        """移除高亮和注释"""
        if self._highlighted_line is not None:
            self._highlighted_line.set_linewidth(self._original_linewidth)
            self._highlighted_line = None
        if self._annotation is not None:
            self._annotation.remove()
            self._annotation = None
            self._canvas.draw_idle()

    def _on_scroll(self, event):
        """滚轮缩放（Issue 3: 使用 QApplication.keyboardModifiers 检测 Ctrl）"""
        if event.inaxes is None:
            return

        ax = event.inaxes
        # Issue 3: 使用 QApplication.keyboardModifiers() 代替 event.guiEvent.modifiers()
        modifiers = QApplication.keyboardModifiers()
        ctrl_pressed = bool(modifiers & Qt.ControlModifier)

        scale_factor = 0.85 if event.button == "up" else 1.15

        if ctrl_pressed:
            # Ctrl+滚轮：X/Y 同时缩放
            self._zoom_axis(ax, "x", event.xdata, scale_factor)
            self._zoom_axis(ax, "y", event.ydata, scale_factor)
        else:
            # 根据鼠标位置判断缩放轴
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

        self._canvas.draw_idle()

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
        """鼠标点击（含 Issue 7: 固定曲线高亮 / 标记线拖动 / 放置时间差标记）"""
        if event.inaxes is None or event.xdata is None:
            return

        # 左键按在已有标记线附近：进入拖动（不依赖标记模式，放置完也能继续微调）
        if event.button == 1:
            idx = self._mark_index_near(event)
            if idx >= 0:
                self._dragging_mark = idx
                self._remove_highlight()
                self._remove_mark_preview()
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

        # 右键清除标记
        if event.button == 3:
            self._clear_marks()
            return

        # Issue 7: 左键点击 — 检测是否靠近曲线以切换固定状态
        if event.button == 1 and not self._mark_mode:
            nearest_line = self._find_nearest_line(event)
            if nearest_line is not None:
                self._toggle_pin(nearest_line, event)
                return

        # 中键或左键开始拖拽平移
        if event.button == 2 or (event.button == 1 and not self._mark_mode):
            self._drag_start = (event.xdata, event.ydata)

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
        """切换曲线的固定高亮状态（Issue 7）"""
        if line in self._pinned_lines:
            # 取消固定：恢复线宽、移除持久注释
            line.set_linewidth(self._original_linewidth)
            self._pinned_lines.discard(line)
            if line in self._pinned_annotations:
                self._pinned_annotations[line].remove()
                del self._pinned_annotations[line]
        else:
            # 固定：保持粗线宽、创建持久注释
            line.set_linewidth(4)
            self._pinned_lines.add(line)

            ax = line.axes
            label = line.get_label()
            # 获取点击位置附近的数据点用于注释定位
            xdata = line.get_xdata()
            idx = np.searchsorted(xdata, event.xdata)
            idx = np.clip(idx, 0, len(xdata) - 1)
            x = xdata[idx]
            y = line.get_ydata()[idx]

            # Issue 5: 同样支持 DBC 值描述（line->sig_name 映射精确取信号名）
            sig_name = self._line_sig_name.get(line, label.split(".")[-1] if "." in label else label)
            val_int = int(round(y))
            desc = ""
            if sig_name in self._value_descriptions:
                if val_int in self._value_descriptions[sig_name]:
                    desc = f" ({self._value_descriptions[sig_name][val_int]})"

            text = f"{label}\n值: {y:.4f}{desc}\n时间: {x:.4f}s"

            # Bug 1 修复：使用曲线自身颜色而非硬编码颜色
            line_color = line.get_color()
            # matplotlib 的 get_color() 可能返回元组/数组而非字符串，
            # 需要转换为 hex 格式以供 bbox/arrowprops 使用
            if hasattr(line_color, '__iter__') and not isinstance(line_color, str):
                import matplotlib.colors as mcolors
                line_color = mcolors.to_hex(line_color)
            ann = ax.annotate(
                text, xy=(x, y), xytext=(15, -25),
                textcoords="offset points",
                bbox=dict(boxstyle="round,pad=0.4", facecolor="#2b2b2b",
                          edgecolor=line_color, alpha=0.92),
                fontsize=9, color="#e0e0e0",
                arrowprops=dict(arrowstyle="->", color=line_color, lw=1.2),
            )
            self._pinned_annotations[line] = ann

        self._canvas.draw_idle()

    def _on_release(self, event):
        """鼠标释放"""
        # 拖动标记线结束：只结束拖动，不触发画布平移
        if self._dragging_mark >= 0:
            self._dragging_mark = -1
            self._drag_start = None
            self._canvas.draw_idle()
            return

        if self._drag_start is None:
            return

        if event.inaxes is None or self._mark_mode:
            self._drag_start = None
            return

        # 拖拽平移
        dx = self._drag_start[0] - event.xdata
        dy = self._drag_start[1] - event.ydata

        for ax in self._fig.axes:
            xlim = ax.get_xlim()
            ylim = ax.get_ylim()
            ax.set_xlim(xlim[0] + dx, xlim[1] + dx)
            ax.set_ylim(ylim[0] + dy, ylim[1] + dy)

        self._drag_start = None
        self._canvas.draw_idle()
