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
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QApplication
from PyQt5.QtCore import Qt
from core.can_data import DecodedSignal
from utils.lttb import lttb_downsample

# ─── 中文字体支持（Windows 环境下正确渲染中文）───
matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False

# 降采样阈值
DOWNSAMPLE_THRESHOLD = 10000

# 默认颜色列表 — 高对比度、色盲友好
COLORS = [
    "#4fc3f7", "#ff7043", "#66bb6a", "#ef5350", "#ab47bc",
    "#8d6e63", "#ec407a", "#78909c", "#d4e157", "#26c6da",
]

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
            font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
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
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._signals: list[DecodedSignal] = []
        self._subplot_mode = False   # True=独立子图, False=共享Y轴
        self._mark_mode = False      # 时间差标记模式
        self._mark_points = []       # 已放置的标记时间戳
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
        # ─── 实时曲线模式（用于信号实时监控页） ───
        self._realtime: bool = False
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
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ─── 工具栏 ───
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self._mode_btn = QPushButton("切换为独立子图")
        self._mode_btn.setToolTip("在共享Y轴和独立子图模式之间切换")
        self._mode_btn.clicked.connect(self._toggle_mode)
        toolbar.addWidget(self._mode_btn)

        self._mark_btn = QPushButton("标记时间差")
        self._mark_btn.setCheckable(True)
        self._mark_btn.setToolTip("点击后在图上点两个位置，显示时间差")
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

    def set_value_descriptions(self, descriptions: dict):
        """设置 DBC 值描述表，用于悬停提示显示枚举含义

        Args:
            descriptions: {sig_name: {int_val: "描述", ...}, ...}
            例如 {"Gear": {0: "PARK", 1: "REVERSE", 2: "NEUTRAL", 3: "DRIVE"}}
        """
        self._value_descriptions = descriptions

    # ────────────────────── 绘制逻辑 ──────────────────────

    def _redraw(self):
        """根据当前模式和信号列表重绘"""
        self._fig.clear()
        self._fig.patch.set_facecolor("#1e1e2e")

        # 实时模式：根据缓冲数据构建坐标轴与曲线
        if self._realtime and self._rt_meta:
            self._build_realtime()
            self._canvas.draw()
            return

        if not self._signals:
            ax = self._fig.add_subplot(111)
            ax.set_facecolor("#1e1e2e")
            ax.text(0.5, 0.5, "请勾选信号并点击绘图",
                    transform=ax.transAxes, ha="center", va="center",
                    fontsize=16, color="#666680", fontweight="light")
            ax.grid(True, alpha=0.3)
            self._canvas.draw()
            return

        if self._subplot_mode:
            self._draw_subplots()
        else:
            self._draw_shared()

        self._fig.tight_layout(pad=2.0)

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
            meta: [(msg_name, sig_name), ...] 信号的绘制顺序
        """
        self._realtime = True
        self._rt_meta = list(meta)
        self._rt_buffers = {
            (m, s): {"t": [], "v": []} for (m, s) in meta
        }
        self._rt_lines = {}
        self._rt_axes = {}
        self._rt_t0 = 0.0
        self._redraw()

    def push_sample(self, msg_name: str, sig_name: str, t: float, v: float):
        """推送一个实时采样点。

        必须在 GUI 线程调用（由监控页通过信号槽从后台线程转发）。
        """
        if not self._realtime:
            return
        key = (msg_name, sig_name)
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
        """退出实时模式，保留最后一次画面"""
        self._realtime = False
        self._rt_buffers = {}
        self._rt_lines = {}
        self._rt_axes = {}

    def _build_realtime(self):
        """根据实时缓冲构建坐标轴与空曲线（模式切换时复用）"""
        self._rt_lines = {}
        self._rt_axes = {}

        if self._subplot_mode:
            n = len(self._rt_meta)
            axes = self._fig.subplots(n, 1, sharex=True)
            if n == 1:
                axes = [axes]
            for i, (msg_name, sig_name) in enumerate(self._rt_meta):
                ax = axes[i]
                ax.set_facecolor("#1e1e2e")
                color = COLORS[i % len(COLORS)]
                label = f"{msg_name}.{sig_name}"
                line, = ax.plot([], [], color=color, linewidth=self._original_linewidth,
                                marker="o", markersize=2, label=label, alpha=0.9)
                ax.set_title(label, loc="left", fontsize=9, color=color, pad=2)
                ax.grid(True, linestyle="--", alpha=0.4, color="#3a3a4e")
                legend = ax.legend(loc="upper right", draggable=True, framealpha=0.85)
                legend.get_frame().set_edgecolor("#3a3a4e")
                self._rt_lines[(msg_name, sig_name)] = line
                self._rt_axes[(msg_name, sig_name)] = ax
            axes[-1].set_xlabel("时间 (s)", fontsize=11)
        else:
            ax = self._fig.add_subplot(111)
            ax.set_facecolor("#1e1e2e")
            ax.set_xlabel("时间 (s)", fontsize=11)
            ax.set_ylabel("物理值", fontsize=11)
            ax.grid(True, linestyle="--", alpha=0.4, color="#3a3a4e")
            for i, (msg_name, sig_name) in enumerate(self._rt_meta):
                color = COLORS[i % len(COLORS)]
                label = f"{msg_name}.{sig_name}"
                line, = ax.plot([], [], color=color, linewidth=self._original_linewidth,
                                marker="o", markersize=2, label=label, alpha=0.9)
                self._rt_lines[(msg_name, sig_name)] = line
                self._rt_axes[(msg_name, sig_name)] = ax
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
        ax = self._fig.add_subplot(111)
        ax.set_facecolor("#1e1e2e")
        ax.set_xlabel("时间 (s)", fontsize=11)
        ax.set_ylabel("物理值", fontsize=11)
        ax.grid(True, linestyle="--", alpha=0.4, color="#3a3a4e")

        for i, sig in enumerate(self._signals):
            color = COLORS[i % len(COLORS)]
            ts, vals = self._downsample_if_needed(sig.timestamps, sig.values)
            label = f"{sig.msg_name}.{sig.sig_name}"
            ax.plot(ts, vals, color=color, linewidth=self._original_linewidth,
                    marker="o", markersize=2, label=label, alpha=0.9)

        legend = ax.legend(loc="upper right", draggable=True, framealpha=0.85)
        legend.get_frame().set_edgecolor("#3a3a4e")

    def _draw_subplots(self):
        """独立子图模式"""
        n = len(self._signals)
        axes = self._fig.subplots(n, 1, sharex=True)
        if n == 1:
            axes = [axes]

        for i, (ax, sig) in enumerate(zip(axes, self._signals)):
            color = COLORS[i % len(COLORS)]
            ax.set_facecolor("#1e1e2e")
            ts, vals = self._downsample_if_needed(sig.timestamps, sig.values)
            label = f"{sig.msg_name}.{sig.sig_name}"
            ax.plot(ts, vals, color=color, linewidth=self._original_linewidth,
                    marker="o", markersize=2, label=label, alpha=0.9)
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
        """切换时间差标记模式"""
        self._mark_mode = self._mark_btn.isChecked()
        self._mark_points.clear()
        if not self._mark_mode:
            self._clear_marks()

    def _auto_scale(self):
        """自适应复位"""
        for ax in self._fig.axes:
            ax.relim()
            ax.autoscale()
        self._fig.tight_layout(pad=2.0)
        self._canvas.draw()

    def _clear_marks(self):
        """清除所有时间差标记"""
        self._mark_points.clear()
        for ax in self._fig.axes:
            for child in ax.get_children():
                if hasattr(child, "_is_time_mark") and child._is_time_mark:
                    child.remove()
        self._canvas.draw()

    # ────────────────────── 鼠标交互 ──────────────────────

    def _on_mouse_move(self, event):
        """鼠标移动：曲线悬停高亮 + 实时坐标显示"""
        if event.inaxes is None:
            self._remove_highlight()
            return

        # Issue 2: 更新当前 axes 的坐标显示
        ax_id = id(event.inaxes)
        if ax_id in self._coord_texts and event.xdata is not None and event.ydata is not None:
            self._coord_texts[ax_id].set_text(f"x={event.xdata:.4f}  y={event.ydata:.4f}")
            self._canvas.draw_idle()

        if self._mark_mode:
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

        # Issue 5: 查找 DBC 值描述
        sig_name = label.split(".")[-1] if "." in label else label
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
        """鼠标点击（含 Issue 7: 点击固定/取消固定曲线高亮）"""
        if event.inaxes is None:
            return

        # 时间差标记模式
        if self._mark_mode and event.button == 1:
            self._place_mark(event)
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

            # Issue 5: 同样支持 DBC 值描述
            sig_name = label.split(".")[-1] if "." in label else label
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

    def _place_mark(self, event):
        """放置时间差标记"""
        t = event.xdata
        self._mark_points.append(t)

        ax = event.inaxes
        line = ax.axvline(x=t, color="#ef5350", linestyle="--", linewidth=1.5)
        line._is_time_mark = True
        # Bug 3 修复：为时间标记文本设置 _is_time_mark 属性，确保清除时能找到
        time_text = ax.text(t, ax.get_ylim()[1], f"  {t:.4f}s",
                color="#ef5350", fontsize=8, va="bottom", fontweight="bold")
        time_text._is_time_mark = True

        if len(self._mark_points) == 2:
            t1, t2 = self._mark_points
            delta = abs(t2 - t1)
            mid = (t1 + t2) / 2
            delta_ann = ax.annotate(
                f"Δt = {delta:.4f} s",
                xy=(mid, ax.get_ylim()[1]),
                fontsize=11, color="#ef5350", fontweight="bold",
                ha="center", va="bottom",
                bbox=dict(boxstyle="round,pad=0.4", facecolor="#2b2b2b",
                          edgecolor="#ef5350", alpha=0.9),
            )
            # Bug 3 修复：为 delta time 注释设置 _is_time_mark 属性
            delta_ann._is_time_mark = True
            # 自动退出标记模式
            self._mark_mode = False
            self._mark_btn.setChecked(False)

        self._canvas.draw_idle()
