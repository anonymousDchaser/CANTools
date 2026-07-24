# widgets/message_table.py
"""原始报文查看器：可展开的树形表格，支持过滤和按需解码

功能特性：
- 可展开行：点击帧行展开显示解码后的信号值
- 多条件过滤：报文ID、信号名模糊搜索、时间范围
- 大数据集优化：限制最大显示行数，批量填充
- DBC 解码集成：使用 cantools 实时解码
- 专业深色主题样式
"""
import numpy as np
import pandas as pd
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QLineEdit, QPushButton, QComboBox, QLabel, QHeaderView,
    QStyledItemDelegate, QStyle,
)
from PyQt5.QtCore import Qt, QRect, QSize
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter
import cantools
from core.can_utils import load_dbc_database
from core.can_data import MessageDef


class HexDataDelegate(QStyledItemDelegate):
    """自定义委托：渲染 Data 列，对变化的字节高亮并渐变消退。

    当同一 arbitration_id 的报文中某个字节值发生变化时，该字节以亮红色高亮显示，
    并在后续约 FADE_FRAMES 帧内线性渐变回正常颜色。前 BOLD_FRAMES 帧内同时加粗显示。
    """

    # 渐变参数（基于筛选后的帧数计数）
    FADE_FRAMES = 500   # 筛选后500帧完全消退至正常色（0帧=高亮，500帧=正常，中点线性插值）
    BOLD_FRAMES = 30    # 前30帧加粗显示
    HIGHLIGHT_COLOR = QColor("#FF6B6B")   # 刚变化时的高亮色（亮红）
    NORMAL_COLOR = QColor("#e0e0e0")       # 正常颜色
    BG_COLOR = QColor("#1e1e2e")           # 单元格背景色（与设计系统一致）
    SELECTED_BG = QColor("#1e3a5a")        # 选中行背景色

    def __init__(self, parent=None, byte_change_info=None, parent_widget=None):
        super().__init__(parent)
        self._byte_change_info = byte_change_info or {}
        self._parent_widget = parent_widget  # MessageTableWidget 引用

    def update_change_info(self, byte_change_info):
        """更新字节变化信息（过滤或数据变更时调用）"""
        self._byte_change_info = byte_change_info

    def _get_byte_color(self, frames_since_change):
        """根据距变化的帧数计算颜色。

        0帧 = 最亮高亮色, FADE_FRAMES帧 = 正常色, 中间线性插值。
        """
        if frames_since_change >= self.FADE_FRAMES:
            return self.NORMAL_COLOR

        # 插值比例: 0=刚变化(全高亮), 1=完全消退(正常色)
        ratio = frames_since_change / self.FADE_FRAMES

        r = int(self.HIGHLIGHT_COLOR.red() +
                (self.NORMAL_COLOR.red() - self.HIGHLIGHT_COLOR.red()) * ratio)
        g = int(self.HIGHLIGHT_COLOR.green() +
                (self.NORMAL_COLOR.green() - self.HIGHLIGHT_COLOR.green()) * ratio)
        b = int(self.HIGHLIGHT_COLOR.blue() +
                (self.NORMAL_COLOR.blue() - self.HIGHLIGHT_COLOR.blue()) * ratio)

        return QColor(r, g, b)

    def _is_bold(self, frames_since_change):
        """判断是否需要加粗显示（刚变化的前 BOLD_FRAMES 帧）"""
        return frames_since_change < self.BOLD_FRAMES

    def paint(self, painter, option, index):
        """绘制 Data 列单元格，逐字节着色。"""
        # 子项（解码后的信号行）使用默认文本绘制，不走 hex 委托逻辑。
        # 原因：topLevelItem(row) 对子行返回的是错误的顶层项，会导致子行
        # 显示成另一帧的 hex 数据。
        if index.parent().isValid():
            super().paint(painter, option, index)
            return

        if self._parent_widget is None:
            super().paint(painter, option, index)
            return

        row = index.row()
        tree = self._parent_widget._tree
        item = tree.topLevelItem(row)
        if item is None:
            super().paint(painter, option, index)
            return

        # 从 UserRole 获取 iloc_pos，再从 _filtered_index 取 frame_id
        iloc_pos = item.data(0, Qt.UserRole)
        if iloc_pos is None:
            super().paint(painter, option, index)
            return

        try:
            fid = int(self._parent_widget._filtered_index.iloc[iloc_pos]["frame_id"])
        except (IndexError, KeyError):
            super().paint(painter, option, index)
            return

        if fid not in self._byte_change_info:
            super().paint(painter, option, index)
            return

        # 获取原始数据和 DLC
        try:
            dlc = int(self._parent_widget._filtered_index.iloc[iloc_pos]["dlc"])
        except (IndexError, KeyError):
            super().paint(painter, option, index)
            return

        frame_data = self._parent_widget._raw_data[fid, :dlc]
        change_info = self._byte_change_info[fid]

        # 绘制背景（选中态 / 普通态）
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        else:
            painter.fillRect(option.rect, self.BG_COLOR)

        # 使用等宽字体绘制每个字节
        font = QFont("Consolas", 9)
        fm = QFontMetrics(font)
        painter.setFont(font)

        # 垂直居中计算 y 坐标
        char_x = option.rect.x() + 4
        char_y = (option.rect.y() +
                  (option.rect.height() - fm.height()) // 2 +
                  fm.ascent())

        # 逐字节绘制：2个十六进制字符 + 1个空格
        for byte_idx, byte_val in enumerate(frame_data):
            frames_since = change_info.get(byte_idx, 999)
            color = self._get_byte_color(frames_since)
            bold = self._is_bold(frames_since)

            font.setBold(bold)
            painter.setFont(font)

            hex_str = f"{byte_val:02X}"
            painter.setPen(color)
            painter.drawText(char_x, char_y, hex_str)
            # "00 " 三个字符宽度（含尾部空格）
            char_x += fm.horizontalAdvance("00 ")

    def sizeHint(self, option, index):
        """返回 Data 列的建议尺寸"""
        return QSize(300, 24)


class MessageTableWidget(QWidget):
    """报文表格组件，带过滤和按需解码功能"""

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
            font-weight: 500;
            padding: 0 2px;
        }
        QLineEdit {
            background-color: #2a2a3e;
            color: #e0e0e0;
            border: 1px solid #3a3a4e;
            border-radius: 4px;
            padding: 6px 10px;
            min-height: 28px;
            selection-background-color: #1e3a5a;
        }
        QLineEdit:focus {
            border-color: #4fc3f7;
        }
        QLineEdit::placeholder {
            color: #666680;
        }
        QComboBox {
            background-color: #2a2a3e;
            color: #e0e0e0;
            border: 1px solid #3a3a4e;
            border-radius: 4px;
            padding: 6px 10px;
            min-height: 28px;
        }
        QComboBox:hover {
            border-color: #4fc3f7;
        }
        QComboBox::drop-down {
            border: none;
            width: 24px;
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
        QTreeWidget {
            background-color: #1e1e2e;
            alternate-background-color: #252535;
            color: #e0e0e0;
            border: 1px solid #3a3a4e;
            border-radius: 4px;
            outline: none;
            gridline-color: #3a3a4e;
            font-family: "Consolas", "Cascadia Code", monospace;
            font-size: 12px;
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
        QTreeWidget::branch {
            background-color: #1e1e2e;
        }
        QTreeWidget::branch:has-children:!has-siblings:closed,
        QTreeWidget::branch:closed:has-children:has-siblings {
            image: none;
            border-image: none;
        }
        QTreeWidget::branch:open:has-children:!has-siblings,
        QTreeWidget::branch:open:has-children:has-siblings {
            image: none;
            border-image: none;
        }
        QHeaderView::section {
            background-color: #2a2a3e;
            color: #4fc3f7;
            border: none;
            border-right: 1px solid #3a3a4e;
            border-bottom: 2px solid #4fc3f7;
            padding: 8px 6px;
            font-weight: bold;
            font-size: 12px;
        }
        QHeaderView::section:hover {
            background-color: #3a3a4e;
        }
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frame_index: pd.DataFrame | None = None
        self._raw_data: np.ndarray | None = None
        self._messages: list[MessageDef] = []
        self._dbc_path: str = ""
        self._db = None  # cantools Database
        self._filtered_index: pd.DataFrame | None = None
        self._byte_change_info: dict = {}  # {frame_id: {byte_idx: frames_since_change}}

        self.setStyleSheet(self._QSS)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # ─── 过滤栏 ───
        filter_layout = QHBoxLayout()
        filter_layout.setSpacing(8)

        lbl_id = QLabel("报文ID:")
        lbl_id.setStyleSheet("font-weight: bold;")
        filter_layout.addWidget(lbl_id)

        self._id_filter = QComboBox()
        self._id_filter.setEditable(True)
        self._id_filter.setFixedWidth(130)
        self._id_filter.setToolTip("输入或选择报文 ID（十六进制）")
        filter_layout.addWidget(self._id_filter)

        lbl_sig = QLabel("信号名:")
        lbl_sig.setStyleSheet("font-weight: bold;")
        filter_layout.addWidget(lbl_sig)

        self._sig_filter = QLineEdit()
        self._sig_filter.setPlaceholderText("模糊搜索...")
        self._sig_filter.setFixedWidth(160)
        self._sig_filter.setToolTip("按信号名模糊过滤")
        filter_layout.addWidget(self._sig_filter)

        lbl_time = QLabel("时间:")
        lbl_time.setStyleSheet("font-weight: bold;")
        filter_layout.addWidget(lbl_time)

        self._time_start = QLineEdit()
        self._time_start.setPlaceholderText("起始(s)")
        self._time_start.setFixedWidth(90)
        filter_layout.addWidget(self._time_start)

        filter_layout.addWidget(QLabel("~"))

        self._time_end = QLineEdit()
        self._time_end.setPlaceholderText("结束(s)")
        self._time_end.setFixedWidth(90)
        filter_layout.addWidget(self._time_end)

        # 主要操作按钮：应用过滤
        self._apply_btn = QPushButton("🔍 应用过滤")
        self._apply_btn.setProperty("class", "primary")
        self._apply_btn.clicked.connect(self._apply_filter)
        filter_layout.addWidget(self._apply_btn)

        # 回车键触发过滤
        self._id_filter.lineEdit().returnPressed.connect(self._apply_filter)
        self._sig_filter.returnPressed.connect(self._apply_filter)
        self._time_start.returnPressed.connect(self._apply_filter)
        self._time_end.returnPressed.connect(self._apply_filter)

        self._reset_btn = QPushButton("重置")
        self._reset_btn.setToolTip("清除所有过滤条件")
        self._reset_btn.clicked.connect(self._reset_filter)
        filter_layout.addWidget(self._reset_btn)

        filter_layout.addStretch()
        layout.addLayout(filter_layout)

        # ─── 树形表格 ───
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["序号", "时间(s)", "ID", "DLC", "Channel", "Data (Hex)"])
        self._tree.setColumnCount(6)
        self._tree.setAlternatingRowColors(True)
        self._tree.setRootIsDecorated(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setSortingEnabled(False)

        # 列宽设置
        header = self._tree.header()
        header.setStretchLastSection(True)
        header.resizeSection(0, 70)
        header.resizeSection(1, 200)  # 时间列 — 加宽以显示完整时间戳
        header.resizeSection(2, 90)
        header.resizeSection(3, 55)
        header.resizeSection(4, 70)

        # 在填充数据前连接展开信号，确保展开时能触发解码
        self._tree.itemExpanded.connect(self._on_item_expanded)

        # 创建自定义委托，用于 Data 列（第5列）的逐字节高亮渲染
        self._hex_delegate = HexDataDelegate(parent=self._tree, parent_widget=self)
        self._tree.setItemDelegateForColumn(5, self._hex_delegate)

        layout.addWidget(self._tree, stretch=1)

    # ────────────────────── 公共接口 ──────────────────────

    def set_data(self, frame_index: pd.DataFrame, raw_data: np.ndarray,
                 messages: list[MessageDef], dbc_path: str = ""):
        """设置数据源"""
        self._frame_index = frame_index
        self._raw_data = raw_data
        self._messages = messages
        self._dbc_path = dbc_path
        self._filtered_index = frame_index

        if dbc_path:
            try:
                self._db = load_dbc_database(dbc_path)
            except Exception:
                self._db = None

        # 更新 ID 过滤下拉框
        self._id_filter.clear()
        unique_ids = sorted(frame_index["arbitration_id"].unique())
        self._id_filter.addItem("全部")
        for aid in unique_ids:
            self._id_filter.addItem(f"0x{aid:03X}", aid)

        self._populate_table()

    def get_filtered_index(self) -> pd.DataFrame | None:
        """返回当前过滤后的帧索引"""
        return self._filtered_index

    def update_dbc(self, dbc_path: str):
        """外部更新 DBC 数据库路径（例如主窗口加载 DBC 后通知）

        当 DBC 在日志加载之后才加载时，需要通过此方法更新数据库，
        否则展开行时 _db 仍为 None，无法解码信号。

        同时清除所有已展开行的子项，以便用户再次展开时能使用新数据库重新解码。
        如果不清除，之前因缺少 DBC 而显示错误信息的行将永远无法刷新。

        清除子项后重新添加占位符，确保项目在 UI 上仍然显示展开箭头。
        """
        self._dbc_path = dbc_path
        if dbc_path:
            try:
                self._db = load_dbc_database(dbc_path)
            except Exception:
                self._db = None

        # 清除所有顶层项的子项，强制下次展开时重新解码
        # 然后重新添加占位符以保持展开箭头可见
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            while item.childCount() > 0:
                item.removeChild(item.child(0))
            # 重新添加占位子项，使项目保持可展开状态
            placeholder = QTreeWidgetItem(item)
            placeholder.setText(1, "(点击展开解码)")
            placeholder.setForeground(1, QColor("#777777"))

    # ────────────────────── 字节变化检测 ──────────────────────

    def _compute_byte_change_info(self):
        """预计算每个字节的变化信息。

        为当前过滤结果中每个帧的每个字节位置计算"距上次变化的帧数"。
        按 arbitration_id 分组，仅在同一 ID 的连续帧之间比较。
        首帧的所有字节标记为"无变化"（值为 999，>= FADE_FRAMES，渲染为正常色），
        仅当字节值真正发生变化时才高亮（值为 0），之后帧数递增直至消退。
        结果存入 self._byte_change_info: {frame_id: {byte_idx: frames_since_change}}
        """
        if self._filtered_index is None or self._raw_data is None:
            self._byte_change_info = {}
            return

        self._byte_change_info = {}

        # 跟踪每个 ID 每个字节的上次值和上次变化时的遍历序号
        last_values = {}      # {arb_id: {byte_idx: last_byte_value}}
        last_change_seq = {}  # {arb_id: {byte_idx: seq_when_changed or None}}

        NO_CHANGE = 999  # 大于 FADE_FRAMES，渲染为正常色

        for seq, (_, row) in enumerate(self._filtered_index.iterrows()):
            fid = int(row["frame_id"])
            arb_id = int(row["arbitration_id"])
            dlc = int(row["dlc"])
            frame_data = self._raw_data[fid, :dlc]

            self._byte_change_info[fid] = {}

            if arb_id not in last_values:
                # 首帧 — 标记为"无变化"（正常色，不高亮）
                last_values[arb_id] = {}
                last_change_seq[arb_id] = {}
                for byte_idx in range(dlc):
                    last_values[arb_id][byte_idx] = frame_data[byte_idx]
                    last_change_seq[arb_id][byte_idx] = None  # 从未变化
                    self._byte_change_info[fid][byte_idx] = NO_CHANGE  # 无变化
            else:
                for byte_idx in range(dlc):
                    current_byte = frame_data[byte_idx]
                    prev_byte = last_values[arb_id].get(byte_idx)

                    if prev_byte != current_byte:
                        # 字节值发生变化
                        last_values[arb_id][byte_idx] = current_byte
                        last_change_seq[arb_id][byte_idx] = seq
                        self._byte_change_info[fid][byte_idx] = 0  # 刚变化
                    else:
                        # 未变化
                        change_seq = last_change_seq[arb_id].get(byte_idx)
                        if change_seq is None:
                            # 从未变化过
                            self._byte_change_info[fid][byte_idx] = NO_CHANGE
                        else:
                            # 距上次变化的帧数
                            self._byte_change_info[fid][byte_idx] = seq - change_seq

    # ────────────────────── 表格填充 ──────────────────────

    def _populate_table(self):
        """填充表格（只显示帧头，不预解码信号）

        关键：为每个顶层项目添加一个占位子项，使其在 UI 上显示展开箭头。
        没有子项的 QTreeWidgetItem 不会显示展开指示器，用户无法点击展开。
        占位子项在 _on_item_expanded 中被移除并替换为真实解码结果。
        """
        self._tree.clear()
        if self._filtered_index is None:
            return

        # 限制显示行数，大数据集时分批加载
        max_display = 10000
        display_df = self._filtered_index.head(max_display)

        self._tree.setUpdatesEnabled(False)
        for iloc_pos, (idx, row) in enumerate(display_df.iterrows()):
            fid = int(row["frame_id"])
            dlc = int(row["dlc"])
            hex_data = " ".join(f"{b:02X}" for b in self._raw_data[fid, :dlc])

            item = QTreeWidgetItem(self._tree)
            item.setText(0, str(fid))
            item.setText(1, f"{row['timestamp']:.6f}")
            item.setText(2, f"0x{row['arbitration_id']:03X}")
            item.setText(3, str(dlc))
            item.setText(4, str(int(row["channel"])))
            item.setText(5, hex_data)

            # 存储行在 _filtered_index 中的位置索引，用于展开时解码
            item.setData(0, Qt.UserRole, iloc_pos)

            # 添加占位子项，使项目在 UI 上显示展开箭头
            # 没有子项的项目不会显示展开指示器，用户无法点击
            placeholder = QTreeWidgetItem(item)
            placeholder.setText(1, "(点击展开解码)")
            placeholder.setForeground(1, QColor("#777777"))

        self._tree.setUpdatesEnabled(True)

        # 预计算字节变化信息并更新委托
        self._compute_byte_change_info()
        self._hex_delegate.update_change_info(self._byte_change_info)

    def _is_placeholder_child(self, item: QTreeWidgetItem) -> bool:
        """检查项目的子项是否为占位符（未解码状态）"""
        if item.childCount() != 1:
            return False
        child = item.child(0)
        text = child.text(1)
        # 占位符文本特征：以 "(" 开头且包含 "点击展开" 或 "加载中"
        return text.startswith("(") and ("点击展开" in text or "加载中" in text)

    def _on_item_expanded(self, item: QTreeWidgetItem):
        """展开某行时解码其信号"""
        # 如果已经有真实解码结果（非占位符），跳过
        if item.childCount() > 0 and not self._is_placeholder_child(item):
            return  # 已经解码过

        # 移除占位子项
        while item.childCount() > 0:
            item.removeChild(item.child(0))

        iloc_pos = item.data(0, Qt.UserRole)
        if iloc_pos is None:
            child = QTreeWidgetItem(item)
            child.setText(1, "(无法解码: 行索引信息缺失)")
            child.setForeground(1, QColor("#ef5350"))
            return

        # 如果 DBC 数据库未加载但路径已设置，尝试重新加载
        if self._db is None and self._dbc_path:
            try:
                self._db = load_dbc_database(self._dbc_path)
            except Exception as e:
                child = QTreeWidgetItem(item)
                child.setText(1, f"(DBC 加载失败: {e})")
                child.setForeground(1, QColor("#ef5350"))
                return

        # 如果 DBC 数据库和路径都未设置，显示明确提示
        if self._db is None:
            child = QTreeWidgetItem(item)
            child.setText(1, "(未加载 DBC 数据库，请先加载 DBC 文件)")
            child.setForeground(1, QColor("#ef5350"))
            return

        # 通过位置索引从 _filtered_index 获取该行数据
        try:
            row = self._filtered_index.iloc[iloc_pos]
        except (IndexError, KeyError) as e:
            child = QTreeWidgetItem(item)
            child.setText(1, f"(行索引无效: {e})")
            child.setForeground(1, QColor("#ef5350"))
            return

        fid = int(row["frame_id"])
        dlc = int(row["dlc"])
        arb_id = int(row["arbitration_id"])
        frame_data = bytes(self._raw_data[fid, :dlc])

        try:
            msg_def = self._db.get_message_by_frame_id(arb_id)
            if msg_def is None:
                child = QTreeWidgetItem(item)
                child.setText(1, f"(未找到 ID=0x{arb_id:X} 的报文定义)")
                child.setForeground(1, QColor("#ef5350"))
                return
            decoded = msg_def.decode(frame_data)

            for sig_name, sig_value in decoded.items():
                sig_def = next((s for s in msg_def.signals if s.name == sig_name), None)
                unit = sig_def.unit if sig_def and sig_def.unit else ""
                child = QTreeWidgetItem(item)
                child.setText(0, "")
                child.setText(1, sig_name)
                child.setText(5, f"{sig_value} {unit}")
                # 子项用不同颜色区分
                child.setForeground(1, QColor("#4fc3f7"))
                child.setForeground(5, QColor("#66bb6a"))
        except Exception as e:
            child = QTreeWidgetItem(item)
            child.setText(1, f"(解码失败: {e})")
            child.setForeground(1, QColor("#ef5350"))

    # ────────────────────── 过滤逻辑 ──────────────────────

    def _apply_filter(self):
        """应用过滤条件"""
        if self._frame_index is None:
            return

        df = self._frame_index

        # 报文 ID 过滤
        id_text = self._id_filter.currentText()
        if id_text and id_text != "全部":
            try:
                aid = int(id_text, 16) if id_text.startswith("0x") else int(id_text, 16)
                df = df[df["arbitration_id"] == aid]
            except ValueError:
                pass

        # 时间范围过滤
        t_start = self._time_start.text().strip()
        t_end = self._time_end.text().strip()
        if t_start:
            try:
                df = df[df["timestamp"] >= float(t_start)]
            except ValueError:
                pass
        if t_end:
            try:
                df = df[df["timestamp"] <= float(t_end)]
            except ValueError:
                pass

        # 信号名过滤 — 需要找到包含该信号的报文 ID
        sig_text = self._sig_filter.text().strip().lower()
        if sig_text and self._messages:
            matching_ids = set()
            for msg in self._messages:
                for sig in msg.signals:
                    if sig_text in sig.name.lower():
                        matching_ids.add(msg.frame_id)
            if matching_ids:
                df = df[df["arbitration_id"].isin(matching_ids)]
            else:
                df = df.iloc[0:0]  # 空

        self._filtered_index = df
        self._populate_table()

    def _reset_filter(self):
        """重置过滤条件"""
        self._id_filter.setCurrentText("全部")
        self._sig_filter.clear()
        self._time_start.clear()
        self._time_end.clear()
        self._filtered_index = self._frame_index
        self._populate_table()
