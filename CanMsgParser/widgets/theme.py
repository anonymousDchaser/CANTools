# widgets/theme.py
"""信号分组 / 信号检索两视图共用的深色主题样式表。

两视图独立为停靠窗后，各自需要自带深色 QSS（不再互相继承），
故集中在此处定义，保证主题完全一致。

尺寸约定
--------
QSS 中的 px 一律按 **1920x1080 设计基准**给出，最后交给
``utils.ui_scale.scale_qss`` 统一等比缩放（小屏收紧 / 超高分放大），
因此这里不要写死多套尺寸。相较初版整体收紧了行高与内边距：
``QTreeWidget::item`` 行高 28px -> 22px、``QListWidget::item`` 内边距
5/8 -> 2/6，目的是在有限的停靠窗高度里显示更多信号行。

列表/树的行文本字号单独处理：基准 12px，下限放宽到 10px（见
``_list_font_px``），使窄停靠窗里一行能多露出几个字符。
"""
from utils.font_helper import UI_FONT_FAMILY
from utils.ui_scale import scale, scale_qss

# ── 列表/树行文本字号 ──
# scale_qss 会把所有 font-size 夹在 [11, 20] px 之间以保可读性，但列表/树里一行
# 文本长、列又窄，用户更希望"多露出几个字符"，故这里把字号下限放宽到 10px。
# 该规则以绝对 px **追加在缩放后的 QSS 之后**：选择器与前面相同，Qt 中后出现者
# 生效，因此它不参与等比缩放（否则又会被夹回 11px）。
_LIST_FONT_BASE_PX = 12      # 1920x1080 设计基准下的列表/树字号
_LIST_FONT_MIN_PX = 10       # 小屏下限（比全局 11px 再松一档）
_LIST_FONT_MAX_PX = 15


def _list_font_px() -> int:
    """返回当前缩放系数下列表/树应使用的行文本字号（px）。"""
    px = int(round(_LIST_FONT_BASE_PX * scale()))
    return max(_LIST_FONT_MIN_PX, min(_LIST_FONT_MAX_PX, px))


DARK_PANEL_QSS = scale_qss("""
    QWidget {
        background-color: #1e1e2e;
        color: #e0e0e0;
        font-family: %s;
        font-size: 13px;
    }
    QLabel {
        color: #9090a0;
        font-weight: 500;
        padding: 0 2px;
    }
    QComboBox {
        background-color: #2a2a3e;
        color: #e0e0e0;
        border: 1px solid #3a3a4e;
        border-radius: 4px;
        padding: 3px 8px;
        min-height: 22px;
    }
    QComboBox:hover {
        border-color: #4fc3f7;
    }
    QComboBox::drop-down {
        border: none;
        width: 20px;
    }
    QComboBox QAbstractItemView {
        background-color: #252535;
        color: #e0e0e0;
        selection-background-color: #1e3a5a;
        selection-color: #4fc3f7;
        border: 1px solid #3a3a4e;
        outline: none;
    }
    QPushButton {
        background-color: #3a3a4e;
        color: #e0e0e0;
        border: 1px solid #4a4a5e;
        border-radius: 4px;
        padding: 3px 8px;
        min-height: 22px;
        font-weight: 500;
    }
    QPushButton:hover {
        background-color: #4a4a5e;
        border-color: #4fc3f7;
    }
    QPushButton:pressed {
        background-color: #2a2a3e;
    }
    QPushButton[class="compact"] {
        padding: 2px 6px;
        min-height: 20px;
        font-size: 12px;
    }
    /* 主操作按钮的紧凑变体：尺寸与 compact 完全一致（并排时等高同宽），
       只保留 primary 的高亮配色。border 必须写成 1px（不能 none）——
       compact 继承基础 QPushButton 的 1px 边框，这里若去掉边框，
       两个并排按钮会差 2px 高度。 */
    QPushButton[class="compact-primary"] {
        background-color: #4fc3f7;
        color: #1e1e2e;
        border: 1px solid #4fc3f7;
        padding: 2px 6px;
        min-height: 20px;
        font-size: 12px;
    }
    QPushButton[class="compact-primary"]:hover {
        background-color: #29b6f6;
    }
    QPushButton[class="compact-primary"]:pressed {
        background-color: #0288d1;
    }
    QListWidget {
        background-color: #1e1e2e;
        alternate-background-color: #252535;
        color: #e0e0e0;
        border: 1px solid #3a3a4e;
        border-radius: 4px;
        padding: 2px;
        outline: none;
    }
    QListWidget::item {
        padding: 2px 6px;
    }
    QTreeWidget::item {
        min-height: 22px;
        padding: 2px 6px;
    }
    QListWidget::item:selected {
        background-color: #1e3a5a;
        color: #4fc3f7;
    }
    QListWidget::item:hover {
        background-color: #2a2a4e;
    }
    QCheckBox {
        background-color: transparent;
        color: #e0e0e0;
        spacing: 5px;
        font-size: 13px;
    }
    QCheckBox::indicator {
        width: 14px;
        height: 14px;
        border: 1px solid #4a4a5e;
        border-radius: 3px;
        background-color: #2a2a3e;
    }
    QCheckBox::indicator:unchecked:hover {
        border-color: #4fc3f7;
    }
    QCheckBox::indicator:checked {
        background-color: #4fc3f7;
        border-color: #4fc3f7;
    }
    QLineEdit {
        background-color: #2a2a3e;
        color: #e0e0e0;
        border: 1px solid #3a3a4e;
        border-radius: 4px;
        padding: 3px 8px;
        min-height: 22px;
        font-size: 12px;
    }
    QLineEdit:focus {
        border-color: #4fc3f7;
    }
    QHeaderView::section {
        background-color: #2a2a3e;
        color: #4fc3f7;
        border: none;
        border-right: 1px solid #3a3a4e;
        border-bottom: 1px solid #4fc3f7;
        padding: 3px 6px;
        font-weight: bold;
        font-size: 12px;
    }
    QSplitter::handle {
        background-color: #3a3a4e;
    }
    QSplitter::handle:hover {
        background-color: #4fc3f7;
    }
""" % UI_FONT_FAMILY) + """
    QTreeWidget, QListWidget { font-size: %dpx; }
""" % _list_font_px()
