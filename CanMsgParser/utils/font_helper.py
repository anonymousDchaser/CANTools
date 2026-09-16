# utils/font_helper.py
"""跨平台字体适配 —— 保证 Windows / macOS / Linux 下中文都能正常显示。

背景
----
原先字体族只写了 Windows 字体（Microsoft YaHei / SimHei / Segoe UI / Consolas），
换到 macOS 上会有两类问题：

1. **matplotlib 侧（严重）**：候选里只剩 ``DejaVu Sans`` 可用，而它**不含中文字
   形**，曲线图上的中文标题 / 图例会渲染成方框（tofu）。必须在候选里补上
   macOS 的中文字体（PingFang SC 等）。
2. **Qt 侧（轻微）**：字体不存在时 Qt 会回落到系统默认字体，中文一般仍能显示，
   但 ``QFont("Microsoft YaHei")`` 拿不到预期字形，且不同系统观感不一致。

做法：把三个平台的字体候选**按当前平台排序后全部列出**，这样同一份 QSS / 同一
个候选列表在任一系统上都能落到可用字体，无需为每个平台维护一套样式。

用法
----
::

    from utils.font_helper import UI_FONT_FAMILY, ui_font, apply_matplotlib_fonts

    widget.setStyleSheet("QWidget { font-family: %s; }" % UI_FONT_FAMILY)
    label.setFont(ui_font(12, bold=True))
    apply_matplotlib_fonts()          # 配置 matplotlib 中文字体
"""
import sys

# ── 中文字体候选（按平台分组）──
_FONT_WIN = ["Microsoft YaHei", "SimHei"]                          # Windows
_FONT_MAC = ["PingFang SC", "Heiti SC", "STHeiti",
             "Arial Unicode MS"]                                    # macOS
_FONT_LINUX = ["Noto Sans CJK SC", "WenQuanYi Zen Hei"]             # Linux

# ── 等宽字体候选（位号 / 报文原始字节等需要对齐的文本）──
_MONO_WIN = ["Consolas", "Cascadia Code"]                           # Windows
_MONO_MAC = ["Menlo", "SF Mono", "Monaco"]                          # macOS
_MONO_LINUX = ["DejaVu Sans Mono", "Liberation Mono"]               # Linux

# 无衬线回退（放在候选末尾，保证任何系统都有兜底）
_SANS_FALLBACK = ["Segoe UI", "Helvetica Neue", "sans-serif"]


def _is_mac():
    return sys.platform == "darwin"


def _is_win():
    return sys.platform == "win32"


def _cjk_fonts():
    """中文字体候选：当前平台优先，其余平台兜底。"""
    if _is_mac():
        return _FONT_MAC + _FONT_WIN + _FONT_LINUX
    if _is_win():
        return _FONT_WIN + _FONT_LINUX + _FONT_MAC
    return _FONT_LINUX + _FONT_WIN + _FONT_MAC


def _mono_fonts():
    """等宽字体候选：当前平台优先，其余平台兜底。"""
    if _is_mac():
        return _MONO_MAC + _MONO_WIN + _MONO_LINUX
    if _is_win():
        return _MONO_WIN + _MONO_MAC + _MONO_LINUX
    return _MONO_LINUX + _MONO_WIN + _MONO_MAC


def _qss_family(names):
    """把字体名列表拼成 QSS 的 font-family 值（带引号、逗号分隔）。"""
    return ", ".join('"%s"' % n for n in names)


# ── 可直接用于 QSS 的字体族字符串 ──
# 例：f"QWidget {{ font-family: {UI_FONT_FAMILY}; }}"
UI_FONT_FAMILY = _qss_family(_cjk_fonts() + _SANS_FALLBACK)
MONO_FONT_FAMILY = _qss_family(_mono_fonts() + ["monospace"])


def ui_font(size=13, bold=False, mono=False):
    """构造一个当前平台首选字体的 ``QFont``。

    :param size: 字号（pt）
    :param bold: 是否加粗
    :param mono: True 时取等宽字体（位号、报文字节等），否则取中文界面字体
    :return: ``QFont`` 实例；若首选字体缺失，Qt 会按 styleHint 回落到同类字体，
             不会渲染成方框。
    """
    from PyQt5.QtGui import QFont

    names = _mono_fonts() if mono else _cjk_fonts()
    weight = QFont.Bold if bold else QFont.Normal
    font = QFont(names[0], size, weight)
    # 首选字体在当前系统不存在时交给 Qt 的回落机制
    font.setStyleHint(QFont.Monospace if mono else QFont.SansSerif)
    return font


def apply_matplotlib_fonts():
    """配置 matplotlib 的中文字体，避免曲线图中文显示为方框。

    只需在程序启动 early 阶段调用一次（rcParams 全局生效）。本项目图表均为运行
    期动态创建，调用后新图表立即生效；若对已存在的 Text 对象修改，需显式重设
    ``fontname``。

    :return: 实际写入的中文字体候选列表（便于诊断日志打印）
    """
    import matplotlib

    cjk = _cjk_fonts()
    matplotlib.rcParams["font.family"] = "sans-serif"
    matplotlib.rcParams["font.sans-serif"] = cjk + ["DejaVu Sans"]
    # 必须关掉：否则坐标轴负号用 Unicode 减号，多数中文字体缺该字形会显示方框
    matplotlib.rcParams["axes.unicode_minus"] = False
    return cjk
