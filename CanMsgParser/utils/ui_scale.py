# utils/ui_scale.py
"""UI 等比例缩放 —— 按屏幕逻辑分辨率自动推算全局缩放系数。

为什么需要
----------
QSS 与布局里的 px 全是**逻辑像素**：13px 字体、28px 按钮在 1920x1080 上占比合适，
但在 1366x768 这类小屏上，同一套尺寸会把内容区挤到几乎不可用（信号树只剩一两行）。
反过来，4K 屏在 Windows 100% 缩放下逻辑分辨率高达 3840x2160，控件又会显得过小。

做法
----
1. **基准**：以 1920x1080 逻辑分辨率为设计基准（该分辨率下系数 = 1.0，
   所有 px 数值即为设计稿原值）。
2. **系数**：取屏幕可用区域宽、高分别与基准比较后的较小值，再夹到 [0.85, 1.25]。
   低分辨率整体收紧，超高分辨率整体放大。
3. **不乘 devicePixelRatio**：Qt 的 HighDpiScaling 已按屏幕 DPR 做过物理放大，
   这里再乘一次会造成高 DPI 屏双重放大。
4. **字号下限**：缩放后字号夹到 [11, 20] px，避免小屏中文过小不可读；
   因此小屏下"省出来的空间"主要来自行高、内边距、按钮尺寸，而不是把字压小。

用法
----
::

    from utils.ui_scale import dp, scale_qss, init_ui_scale

    init_ui_scale()                       # 创建 QApplication 之后调用一次
    widget.setStyleSheet(scale_qss(qss))  # QSS 自动等比缩放
    layout.setSpacing(dp(6))              # Python 侧尺寸同样缩放
"""
import re

# ── 设计基准分辨率（逻辑像素）──
BASE_W = 1920
BASE_H = 1080

# ── 缩放系数上下限 ──
MIN_SCALE = 0.85
MAX_SCALE = 1.25

# ── 缩放后字号允许区间（px），防止小屏中文过小 ──
MIN_FONT_PX = 11
MAX_FONT_PX = 20

# 当前生效的全局缩放系数（未初始化时为 1.0，即设计稿原值）
_UI_SCALE = 1.0

# QSS 中所有 "数字px"（含小数），缩放时逐个换算
_PX_RE = re.compile(r"(\d+(?:\.\d+)?)px")
# 单独处理 font-size，便于夹到可读区间
_FONT_RE = re.compile(r"font-size:\s*(\d+(?:\.\d+)?)px")


def compute_scale(screen=None) -> float:
    """按屏幕可用区域推算缩放系数。

    :param screen: ``QScreen``；为 None 时取 QApplication 的主屏
    :return: [0.85, 1.25] 区间内的缩放系数；无法取到屏幕时返回 1.0
    """
    from PyQt5.QtWidgets import QApplication

    if screen is None:
        app = QApplication.instance()
        screen = app.primaryScreen() if app is not None else None
    if screen is None:
        return 1.0
    geo = screen.availableGeometry()
    if geo.width() <= 0 or geo.height() <= 0:
        return 1.0
    k = min(geo.width() / float(BASE_W), geo.height() / float(BASE_H))
    return round(min(MAX_SCALE, max(MIN_SCALE, k)), 3)


def init_ui_scale(screen=None) -> float:
    """初始化全局缩放系数。必须在创建 ``QApplication`` 之后、构建任何 QSS 之前调用。

    :return: 生效的缩放系数（便于启动日志打印）
    """
    global _UI_SCALE
    _UI_SCALE = compute_scale(screen)
    return _UI_SCALE


def set_ui_scale(value: float) -> float:
    """直接指定缩放系数（测试或特殊场景用）。"""
    global _UI_SCALE
    _UI_SCALE = float(value)
    return _UI_SCALE


def scale() -> float:
    """返回当前生效的缩放系数。"""
    return _UI_SCALE


def dp(px: float) -> int:
    """把设计稿 px 换算为当前屏幕下的 px（至少 1）。"""
    return max(1, int(round(px * _UI_SCALE)))


def scale_qss(qss: str) -> str:
    """等比缩放样式表里所有 px 数值，并把字号夹到可读区间。

    系数为 1.0（或未初始化）时原样返回，避免无谓的字符串改写。
    """
    if abs(_UI_SCALE - 1.0) < 1e-6:
        return qss
    out = _PX_RE.sub(_scale_px, qss)
    return _FONT_RE.sub(_clamp_font, out)


def _scale_px(match: "re.Match") -> str:
    value = float(match.group(1))
    if value <= 0:
        return "0px"
    return "%dpx" % max(1, int(round(value * _UI_SCALE)))


def _clamp_font(match: "re.Match") -> str:
    """把字号夹到 [MIN_FONT_PX, MAX_FONT_PX] 可读区间。

    注意：调用前 ``_PX_RE`` 已对所有 "数字px"（含 font-size）统一乘过一次
    ``_UI_SCALE``，故这里拿到的已是缩放后的值，**不能再乘一次** ——
    否则字号会被二次缩放（0.85 档等效 x0.72，1.25 档等效 x1.56）。
    """
    value = min(MAX_FONT_PX, max(MIN_FONT_PX, float(match.group(1))))
    return "font-size: %dpx" % int(round(value))
