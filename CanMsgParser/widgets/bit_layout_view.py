# widgets/bit_layout_view.py
"""DBC 位图可视化组件：显示 CAN 帧的位布局，支持 Intel/Motorola 字节序

功能特性：
- 8列 x N行 位网格可视化（Byte0~ByteN, Bit7~Bit0）
- 信号色块填充 + Tooltip 详细信息
- 搜索报文（按 ID / 报文名 / 信号名）
- 点击信号列表高亮对应色块
- Intel / Motorola 字节序正确渲染
- 专业深色主题样式
"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QTreeWidget,
    QTreeWidgetItem, QGraphicsView, QGraphicsScene, QGraphicsRectItem,
    QGraphicsTextItem, QLabel, QListWidget, QListWidgetItem,
    QHeaderView,
)
from PyQt5.QtCore import Qt, QRectF, QSize
from PyQt5.QtGui import QColor, QBrush, QPen, QFont, QPainter
from core.can_data import MessageDef, SignalDef
from utils.bit_utils import get_bit_positions

# 高对比度暖色调色板 — 避免黑色/深色，全部中高亮度
SIGNAL_COLORS = [
    "#E64B35",  # 红 (ColorBrewer Set1)
    "#4DBBD5",  # 蓝绿
    "#00A087",  # 青绿
    "#3C5488",  # 钢蓝
    "#F39B7F",  # 浅橙
    "#8491B4",  # 灰蓝
    "#91D1C2",  # 薄荷
    "#E0C46C",  # 暖金
    "#B09C85",  # 暖棕
    "#DC0000",  # 亮红
    "#F2A900",  # 橙黄
    "#5B8DB8",  # 天蓝
    "#8E44AD",  # 紫色
    "#27AE60",  # 翠绿
    "#E67E22",  # 深橙
]

# 基础单元格尺寸（缩放计算基准）
_BASE_CELL_W = 150
_BASE_CELL_H = 85


def _generate_color(index: int) -> str:
    """基于 HSL 色轮生成高区分度颜色（黄金角均匀分布，亮度至少 60%）"""
    hue = (index * 137.508) % 360
    return f"hsl({hue:.0f}, 70%, 60%)"


class BitLayoutView(QWidget):
    """位图可视化组件，带搜索和高亮功能"""

    # ─── QSS 样式表（与设计系统一致） ───
    _QSS = """
        QWidget {
            background-color: #1e1e2e;
            color: #e0e0e0;
            font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
            font-size: 13px;
        }
        QLabel {
            color: #9090a0;
            font-weight: bold;
            padding: 0 4px;
        }
        QLineEdit {
            background-color: #2a2a3e;
            color: #e0e0e0;
            border: 1px solid #3a3a4e;
            border-radius: 4px;
            padding: 8px 12px;
            min-height: 30px;
            font-size: 13px;
            selection-background-color: #1e3a5a;
        }
        QLineEdit:focus {
            border-color: #4fc3f7;
        }
        QLineEdit::placeholder {
            color: #666680;
        }
        QListWidget {
            background-color: #1e1e2e;
            alternate-background-color: #252535;
            color: #e0e0e0;
            border: 1px solid #3a3a4e;
            border-radius: 4px;
            padding: 4px;
            outline: none;
        }
        QListWidget::item {
            padding: 5px 8px;
        }
        QListWidget::item:selected {
            background-color: #1e3a5a;
            color: #4fc3f7;
        }
        QListWidget::item:hover {
            background-color: #2a2a4e;
        }
        QGraphicsView {
            background-color: #1e1e2e;
            border: 1px solid #3a3a4e;
            border-radius: 4px;
        }
        QTreeWidget {
            background-color: #1e1e2e;
            alternate-background-color: #252535;
            color: #e0e0e0;
            border: 1px solid #3a3a4e;
            border-radius: 4px;
            outline: none;
        }
        QTreeWidget::item {
            padding: 4px 6px;
        }
        QTreeWidget::item:selected {
            background-color: #1e3a5a;
            color: #4fc3f7;
        }
        QTreeWidget::item:hover {
            background-color: #2a2a4e;
        }
        QHeaderView::section {
            background-color: #2a2a3e;
            color: #4fc3f7;
            border: none;
            border-right: 1px solid #3a3a4e;
            border-bottom: 2px solid #4fc3f7;
            padding: 6px 6px;
            font-weight: bold;
            font-size: 12px;
        }
        QHeaderView::section:hover {
            background-color: #3a3a4e;
        }
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._messages: list[MessageDef] = []
        self._current_msg: MessageDef | None = None
        self._scale_factor = 1.0       # 动态缩放因子
        self._excel_descriptions = {}   # Excel 值描述 {sig_name: {0: "OFF", 1: "ON"}}

        self.setStyleSheet(self._QSS)
        self._setup_ui()

    def _setup_ui(self):
        """构建左右分栏布局：左侧搜索+候选列表，右侧位图网格+信号详情"""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ─── 左侧面板：搜索 + 候选列表 ───
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        # 搜索栏
        search_layout = QHBoxLayout()
        search_layout.setSpacing(8)

        lbl = QLabel("🔍 搜索:")
        search_layout.addWidget(lbl)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("ID 或 报文名/信号名...")
        self._search_input.textChanged.connect(self._on_search)
        search_layout.addWidget(self._search_input)
        left_layout.addLayout(search_layout)

        # 候选列表（占据左侧剩余空间）
        self._candidate_list = QListWidget()
        self._candidate_list.setAlternatingRowColors(True)
        self._candidate_list.itemClicked.connect(self._on_candidate_selected)
        left_layout.addWidget(self._candidate_list, stretch=1)

        # 限制左侧面板宽度
        left_panel.setMaximumWidth(300)
        left_panel.setMinimumWidth(200)
        layout.addWidget(left_panel)

        # ─── 右侧面板：位图网格 + 信号详情 ───
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)

        # 位图网格
        self._scene = QGraphicsScene()
        self._graphics_view = QGraphicsView(self._scene)
        self._graphics_view.setRenderHint(QPainter.Antialiasing)
        self._graphics_view.setDragMode(QGraphicsView.ScrollHandDrag)
        self._graphics_view.setBackgroundBrush(QBrush(QColor("#1e1e2e")))
        # 启用滚动条以支持大尺寸网格
        self._graphics_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._graphics_view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        right_layout.addWidget(self._graphics_view, stretch=3)

        # 信号信息列表
        sig_label = QLabel("📋 信号详情:")
        right_layout.addWidget(sig_label)

        self._sig_list = QTreeWidget()
        self._sig_list.setHeaderLabels([
            "信号名", "起始位", "长度", "字节序",
            "Scale", "Offset", "最小值", "最大值", "单位", "值描述"
        ])
        self._sig_list.setAlternatingRowColors(True)
        self._sig_list.itemClicked.connect(self._on_sig_highlight)

        # 列宽调整：信号名列设最小宽度，其余自动适配内容
        header = self._sig_list.header()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.Interactive)  # 信号名列手动调整
        self._sig_list.setColumnWidth(0, 200)
        header.setStretchLastSection(True)

        right_layout.addWidget(self._sig_list, stretch=1)

        layout.addWidget(right_panel, stretch=3)

    # ────────────────────── 公共接口 ──────────────────────

    def load_messages(self, messages: list[MessageDef]):
        """加载报文定义"""
        self._messages = messages

    def set_value_descriptions(self, descriptions: dict):
        """设置值描述（来自 Excel）"""
        self._excel_descriptions = descriptions  # {sig_name: {0: "OFF", 1: "ON"}}

    # ────────────────────── 窗口缩放 ──────────────────────

    def resizeEvent(self, event):
        """窗口大小变化时重新计算缩放因子并刷新位图"""
        super().resizeEvent(event)
        if self._current_msg:
            available_w = self._graphics_view.viewport().width()
            # 8 列（每字节 8 位）+ 0.5 列（Byte 标签窄列）
            ideal_w = (8 + 0.5) * _BASE_CELL_W
            self._scale_factor = max(0.5, min(2.0, available_w / ideal_w))
            self._render_layout(self._current_msg)

    # ────────────────────── 搜索逻辑 ──────────────────────

    def _on_search(self, text: str):
        """搜索过滤报文"""
        self._candidate_list.clear()
        text = text.strip()
        if not text:
            return

        # 判断是 hex ID 还是文本
        is_hex = False
        search_id = 0
        try:
            hex_str = text.replace("0x", "").replace("0X", "")
            search_id = int(hex_str, 16)
            is_hex = True
        except ValueError:
            pass

        for msg in self._messages:
            matched_sigs = []
            text_lower = text.lower()

            if is_hex and msg.frame_id == search_id:
                matched_sigs = [s.name for s in msg.signals]
            elif text_lower in msg.name.lower():
                matched_sigs = [s.name for s in msg.signals]
            else:
                for sig in msg.signals:
                    if text_lower in sig.name.lower():
                        matched_sigs.append(sig.name)

            if matched_sigs:
                display = f"0x{msg.frame_id:03X} — {msg.name}  ({len(matched_sigs)} 信号)"
                item = QListWidgetItem(display)
                item.setData(Qt.UserRole, msg)
                self._candidate_list.addItem(item)

    def _on_candidate_selected(self, item: QListWidgetItem):
        """选中候选后渲染位图"""
        msg = item.data(Qt.UserRole)
        if msg:
            self._render_layout(msg)

    # ────────────────────── 辅助方法 ──────────────────────

    def _add_badge(self, text: str, x: float, y: float, text_color="#FFFFFF"):
        """添加带半透明深色背景的标签文字（pill/badge），确保在任何背景色上可读"""
        sf = self._scale_factor
        badge_w = int(30 * sf)
        badge_h = int(14 * sf)
        # 半透明黑色背景矩形
        bg = QGraphicsRectItem(x, y, badge_w, badge_h)
        bg.setBrush(QBrush(QColor(0, 0, 0, 180)))  # 半透明黑色
        bg.setPen(QPen(Qt.NoPen))
        self._scene.addItem(bg)
        # 白色文字
        label = self._scene.addText(text)
        label.setDefaultTextColor(QColor(text_color))
        label.setPos(x + int(2 * sf), y - int(2 * sf))
        font = label.font()
        font.setPointSize(max(7, int(7 * sf)))
        font.setBold(True)
        label.setFont(font)
        return label

    # ────────────────────── 位图渲染 ──────────────────────

    def _assign_signal_colors(self, signals) -> dict:
        """为每个信号分配唯一颜色，超出调色板时使用黄金角生成"""
        sig_colors = {}
        for i, sig in enumerate(signals):
            if i < len(SIGNAL_COLORS):
                sig_colors[sig.name] = SIGNAL_COLORS[i]
            else:
                sig_colors[sig.name] = _generate_color(i)
        return sig_colors

    def _render_layout(self, msg: MessageDef):
        """渲染报文的位布局（含信号名、MSB/LSB 标签、绝对位号），支持动态缩放"""
        self._current_msg = msg
        self._scene.clear()

        num_bytes = msg.dlc
        sig_colors = self._assign_signal_colors(msg.signals)

        # ─── 动态缩放计算 ───
        cell_w = int(_BASE_CELL_W * self._scale_factor)
        cell_h = int(_BASE_CELL_H * self._scale_factor)
        # Byte 标签列宽度缩减为 cell_w 的 0.5，使标签更紧贴网格
        label_col_w = int(cell_w * 0.5)
        font_scale = self._scale_factor

        # ─── 字体定义（按缩放因子调整）───
        title_font = QFont("Segoe UI", max(8, int(12 * font_scale)), QFont.Bold)
        cell_font = QFont("Consolas", max(8, int(11 * font_scale)))            # 位号字体 11pt
        label_font = QFont("Segoe UI", max(8, int(12 * font_scale)))
        sig_name_font = QFont("Segoe UI", max(7, int(9 * font_scale)))         # 信号名字体 9pt
        msb_lsb_font = QFont("Segoe UI", max(6, int(8 * font_scale)), QFont.Bold)  # MSB/LSB 标签字体 8pt

        # ─── 绘制标题行（Bit7 ~ Bit0）— 紧贴网格上方 ───
        header_y = -int(cell_h * 0.6)  # 标题行紧贴在网格上方
        for bit_idx in range(8):
            x = bit_idx * cell_w + label_col_w  # 使用窄标签列偏移
            text = self._scene.addText(f"Bit{7 - bit_idx}")
            text.setFont(title_font)
            text.setDefaultTextColor(QColor("#9090a0"))
            text.setPos(x + cell_w / 2 - int(15 * font_scale), header_y)

        # ─── 预计算每个信号的位坐标集合及 MSB/LSB 位置 ───
        sig_positions = {}   # sig_name -> set of (byte_idx, bit_in_byte)
        sig_msb_pos = {}     # sig_name -> (byte_idx, bit_in_byte) MSB 位置
        sig_lsb_pos = {}     # sig_name -> (byte_idx, bit_in_byte) LSB 位置
        for sig in msg.signals:
            positions = get_bit_positions(sig.start_bit, sig.length, sig.byte_order)
            sig_positions[sig.name] = set(positions)
            if sig.byte_order == "intel":
                # Intel: start_bit 是 LSB，列表末尾是 MSB
                sig_lsb_pos[sig.name] = positions[0]
                sig_msb_pos[sig.name] = positions[-1]
            else:
                # Motorola: start_bit 是 MSB，列表末尾是 LSB
                sig_msb_pos[sig.name] = positions[0]
                sig_lsb_pos[sig.name] = positions[-1]

        # ─── 绘制网格 ───
        for byte_idx in range(num_bytes):
            y = byte_idx * cell_h

            # Byte 标签 — 紧贴网格左侧，垂直居中
            label = self._scene.addText(f"Byte{byte_idx}")
            label.setFont(label_font)
            label.setDefaultTextColor(QColor("#9090a0"))
            label.setPos(2, y + cell_h / 2 - int(8 * font_scale))

            for bit_pos in range(8):
                x = bit_pos * cell_w + label_col_w
                rect = QGraphicsRectItem(x, y, cell_w, cell_h)
                rect.setPen(QPen(QColor("#3a3a4e"), 1))

                # DBC 绝对位号：byte_idx * 8 + (7 - bit_pos)
                dbc_bit = byte_idx * 8 + (7 - bit_pos)
                bit_in_byte = 7 - bit_pos  # 字节内位号（用于匹配坐标）

                # 检查是否有信号占用此位
                occupied_by = None
                for sig in msg.signals:
                    if (byte_idx, bit_in_byte) in sig_positions[sig.name]:
                        occupied_by = sig
                        break

                if occupied_by:
                    color = sig_colors.get(occupied_by.name, "#CCCCCC")
                    # 使用半透明填充，保持可读性
                    fill_color = QColor(color)
                    fill_color.setAlpha(180)
                    rect.setBrush(QBrush(fill_color))
                    rect.setToolTip(
                        f"信号: {occupied_by.name}\n"
                        f"起始位: {occupied_by.start_bit}\n"
                        f"长度: {occupied_by.length}\n"
                        f"字节序: {occupied_by.byte_order}\n"
                        f"Scale: {occupied_by.scale}\n"
                        f"Offset: {occupied_by.offset}\n"
                        f"单位: {occupied_by.unit}\n"
                        f"范围: [{occupied_by.min_val}, {occupied_by.max_val}]"
                    )
                    rect.setData(0, occupied_by.name)
                else:
                    # 未使用的位 — 深色背景（与设计系统一致）
                    rect.setBrush(QBrush(QColor("#2a2a3e")))
                    rect.setToolTip(f"Byte{byte_idx} Bit{bit_in_byte}\n(Unused/Padding)")

                self._scene.addItem(rect)

                # 位号 — 居中显示
                bit_num_text = self._scene.addText(str(dbc_bit))
                bit_num_text.setFont(cell_font)
                bit_num_text.setDefaultTextColor(QColor("#e0e0e0"))
                bit_num_text.setPos(x + cell_w / 2 - 8, y + cell_h / 2 - 8)

        # ─── 在每个信号的起始显示位上绘制信号名 ───
        for sig in msg.signals:
            positions = get_bit_positions(sig.start_bit, sig.length, sig.byte_order)
            # 确定显示顺序的"第一位"：最小 byte_idx，同字节内最大 bit_in_byte（即最左上角）
            first_pos = min(positions, key=lambda p: (p[0], -p[1]))
            fb, fbit = first_pos
            # bit_in_byte 就是字节内位号(0-7)，显示列 bit_pos = 7 - bit_in_byte
            display_col = 7 - fbit
            fx = display_col * cell_w + label_col_w
            fy = fb * cell_h

            # 截断过长的信号名（最多18字符，字体更小可容纳更多）
            display_name = sig.name if len(sig.name) <= 18 else sig.name[:17] + "…"
            name_text = self._scene.addText(display_name)
            name_text.setFont(sig_name_font)
            # 使用白色文字确保在彩色背景上可读
            name_text.setDefaultTextColor(QColor("#ffffff"))
            name_text.setPos(fx + 4, fy + 4)  # 左上角

        # ─── 绘制 MSB / LSB 标签（使用 badge 确保在任何背景色上可读）───
        for sig in msg.signals:
            # MSB 标签 — 左下角
            msb_byte, msb_bit = sig_msb_pos[sig.name]
            msb_col = 7 - msb_bit
            msb_x = msb_col * cell_w + label_col_w
            msb_y = msb_byte * cell_h
            msb_badge_x = msb_x + int(4 * font_scale)
            msb_badge_y = msb_y + cell_h - int(18 * font_scale)
            self._add_badge("MSB", msb_badge_x, msb_badge_y, text_color="#FFFFFF")

            # LSB 标签 — 右下角
            lsb_byte, lsb_bit = sig_lsb_pos[sig.name]
            lsb_col = 7 - lsb_bit
            lsb_x = lsb_col * cell_w + label_col_w
            lsb_y = lsb_byte * cell_h
            lsb_badge_x = lsb_x + cell_w - int(32 * font_scale)
            lsb_badge_y = lsb_y + cell_h - int(18 * font_scale)
            self._add_badge("LSB", lsb_badge_x, lsb_badge_y, text_color="#FFFFFF")

        # ─── 更新信号列表 ───
        self._sig_list.clear()
        for sig in msg.signals:
            item = QTreeWidgetItem(self._sig_list)
            color = sig_colors.get(sig.name, "#CCCCCC")
            item.setText(0, f"■ {sig.name}")
            item.setForeground(0, QBrush(QColor(color)))
            item.setText(1, str(sig.start_bit))
            item.setText(2, str(sig.length))
            item.setText(3, "Intel (小端)" if sig.byte_order == "intel" else "Motorola (大端)")
            item.setText(4, str(sig.scale))
            item.setText(5, str(sig.offset))
            item.setText(6, str(sig.min_val))
            item.setText(7, str(sig.max_val))
            item.setText(8, sig.unit)
            # 值描述摘要 — DBC choices 优先，其次查找 Excel 描述
            desc_text = ""
            if sig.choices:
                desc_text = ", ".join(f"{k}={v}" for k, v in list(sig.choices.items())[:5])
                if len(sig.choices) > 5:
                    desc_text += "..."
            elif self._excel_descriptions.get(sig.name):
                excel_desc = self._excel_descriptions[sig.name]
                desc_text = ", ".join(f"{k}={v}" for k, v in list(excel_desc.items())[:5])
                if len(excel_desc) > 5:
                    desc_text += "..."
            item.setText(9, desc_text)
            item.setData(0, Qt.UserRole, sig.name)

        # 填充数据后自动调整所有列宽
        for i in range(self._sig_list.columnCount()):
            self._sig_list.resizeColumnToContents(i)
        # 信号名列保证最小宽度 250px
        self._sig_list.setColumnWidth(0, 250)

    # ────────────────────── 高亮交互 ──────────────────────

    def _on_sig_highlight(self, item: QTreeWidgetItem, column: int):
        """点击信号列表项，高亮对应色块"""
        sig_name = item.data(0, Qt.UserRole)
        if not sig_name:
            return

        for graphics_item in self._scene.items():
            if isinstance(graphics_item, QGraphicsRectItem):
                item_sig = graphics_item.data(0)
                if item_sig == sig_name:
                    # 高亮选中信号的色块
                    graphics_item.setPen(QPen(QColor("#ffffff"), 3))
                elif item_sig is not None:
                    # 其他信号色块恢复正常边框
                    graphics_item.setPen(QPen(QColor("#444444"), 1))
                # 空白位保持不变
