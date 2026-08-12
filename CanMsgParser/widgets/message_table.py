# widgets/message_table.py
"""原始报文查看器：可展开树形表格，支持过滤和按需解码（虚拟化重写）

性能设计（对标 CANoe / TSMaster 的大日志策略）：
- 用 QTreeView + QAbstractItemModel 替代 QTreeWidget：帧行**虚拟化**，只有可视区
  的行才会 materialize（data() 按需调用），因此彻底去掉了原先的 10000 行硬上限，
  加载百万级帧也不再为每个行创建 Python 对象，内存可控、UI 不卡。
- 字节变化高亮改用 core.byte_change 的**向量化数组**，由加载线程预计算，
  不再在主线程构建 GB 级嵌套字典（旧实现卡死的根因之二）。
- 解码子项（展开后的信号）**按需懒加载**：canFetchMore/fetchMore 触发，
  只解码用户实际展开的那一行，不预解码全部。

节点标识采用 internalId(整数) + 节点字典的方案（而非 Python 对象作为
internalPointer），避免 C++ 侧持有已回收的 Python 对象导致段错误。
"""
import numpy as np
import pandas as pd
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeView,
    QLineEdit, QPushButton, QComboBox, QLabel, QHeaderView,
    QStyledItemDelegate, QStyle,
)
from PyQt5.QtCore import Qt, QAbstractItemModel, QModelIndex, QSize, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter
import cantools
from core.can_utils import load_dbc_database
from core.can_data import MessageDef
from core.byte_change import compute_byte_change_array, NO_CHANGE


class MessageTableModel(QAbstractItemModel):
    """报文表的虚拟树模型。

    额外采用**根级懒窗口**（动态加载，对标 CANoe/TSMaster 的大日志策略）：
    首绘只装载初始窗口（_WINDOW_INIT 行）而非全部 N 行，因此首绘复杂度从 O(N)
    降为 O(窗口)，切到报文表格页 / 双击展开都不会因总行数巨大而卡顿；用户向下
    滚动时 QTreeView 通过 canFetchMore/fetchMore 自动按块追加后续行。
    """

    _HEADERS = [
        "序号",
        "时间(s) / 信号名",
        "ID / 十六进制值",
        "DLC / 十进制值",
        "Channel / 单位",
        "Data(Hex) / 信号描述",
    ]

    # 根级懒窗口参数（动态加载）
    _WINDOW_INIT = 5000     # 首绘装载行数
    _WINDOW_STEP = 5000     # 滚动到底后每次追加行数

    # 已装载行数变化时通知（供状态栏显示「已加载 X / N 帧」）
    loadedChanged = pyqtSignal(int, int)

    @staticmethod
    def _frame_id(r):
        # bit0 = 1 标记顶层帧；r 存于高位。id 恒为正——
        # internalId 是无符号 64 位，绝不能传负数，否则 round-trip 后会变成极大值
        # 导致节点被误判成别的类型（这是之前 canFetchMore 失效的隐藏原因）。
        return (r << 1) | 1

    @staticmethod
    def _child_id(r, s):
        # bit0 = 0 标记信号子项；s 占 bit1..16，r 占 bit17+。
        # 整体 +2 保证 id 永不为 0（internalId==0 是 QModelIndex 无效哨兵，
        # r=s=0 时必须规避，否则首个帧的首个信号子项会被误判为无效）。
        return (((r & 0x7FFFFFFFFFFF) << 17) | ((s & 0xFFFF) << 1)) + 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frame_index: pd.DataFrame | None = None
        self._raw_data: np.ndarray | None = None
        self._byte_change: np.ndarray | None = None
        self._max_dlc = 8
        self._db = None
        self._value_descriptions: dict = {}
        self._decoded: dict = {}              # r -> [(sig_name, hex, dec, unit, desc), ...]
        self._decode_err: dict = {}           # r -> 错误信息字符串
        self._last_err = ""
        self._total = 0                       # 当前数据集总行数（含未装载的）
        self._loaded = 0                      # 已向视图暴露的行数（懒窗口上界）

    # ───────── 懒窗口辅助 ─────────
    def _reset_window(self):
        self._total = len(self._frame_index) if self._frame_index is not None else 0
        self._loaded = min(self._WINDOW_INIT, self._total)

    # ───────── 数据设置 ─────────
    def set_data(self, frame_index: pd.DataFrame, raw_data: np.ndarray, byte_change: np.ndarray):
        self.beginResetModel()
        self._frame_index = frame_index
        self._raw_data = raw_data
        self._byte_change = byte_change
        self._max_dlc = int(raw_data.shape[1]) if raw_data is not None and raw_data.ndim == 2 else 8
        self._decoded.clear()
        self._decode_err.clear()
        self._reset_window()
        self.endResetModel()
        self.loadedChanged.emit(self._loaded, self._total)

    def set_dbc(self, db):
        self._db = db
        self._decoded.clear()
        self._decode_err.clear()

    def _node(self, index):
        """从 internalId 直接还原节点，不依赖任何 Python 对象（避免段错误）。

        internalId 编码（均为非负整数，规避无符号 round-trip 陷阱）：
          bit0 == 1 : 顶层帧 r            -> ("frame", internalId >> 1)
          bit0 == 0 : 信号子项 (r, s)     -> v = internalId - 2; r = v >> 17, s = (v >> 1) & 0xFFFF
          0         : 无效 (QModelIndex 默认)
        """
        iid = index.internalId()
        if iid == 0:
            return None
        if iid & 1:
            return ("frame", iid >> 1)
        v = iid - 2
        r = v >> 17
        s = (v >> 1) & 0xFFFF
        return ("sig", r, s)

    # ───────── 模型基本接口 ─────────
    def columnCount(self, parent=QModelIndex()):
        return 6

    def rowCount(self, parent=QModelIndex()):
        if not parent.isValid():
            # 根级只返回「已装载窗口」行数，而非全部，避免 O(N) 首绘卡顿
            return self._loaded
        node = self._node(parent)
        if node is None:
            return 0
        if node[0] == "frame":
            r = node[1]
            if r in self._decoded:
                return len(self._decoded[r])
            if r in self._decode_err:
                return 1
            return 0
        return 0

    def index(self, row, column, parent=QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        if not parent.isValid():
            return self.createIndex(row, column, self._frame_id(row))
        node = self._node(parent)
        if node is not None and node[0] == "frame":
            r = node[1]
            cid = self._child_id(r, row)
            return self.createIndex(row, column, cid)
        return QModelIndex()

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        node = self._node(index)
        if node is None or node[0] == "frame":
            return QModelIndex()
        r = node[1]
        return self.createIndex(r, 0, self._frame_id(r))

    def hasChildren(self, parent=QModelIndex()):
        if not parent.isValid():
            # 仅对已装载窗口内的行画展开箭头；视图只会遍历 _loaded 行，故为 O(窗口)
            return self._frame_index is not None and self._loaded > 0
        node = self._node(parent)
        if node is None:
            return False
        if node[0] == "frame":
            return True
        return False

    def headerData(self, section, orientation, role):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self._HEADERS[section]
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    # ───────── 懒加载解码子项 ─────────
    def canFetchMore(self, parent):
        if not parent.isValid():
            # 根级：还有未装载的行则继续懒加载
            return self._loaded < self._total
        node = self._node(parent)
        if node is None or node[0] != "frame":
            return False
        r = node[1]
        return (r not in self._decoded) and (r not in self._decode_err)

    def fetchMore(self, parent):
        if not parent.isValid():
            # 根级懒窗口追加：按块装入后续行
            if self._loaded >= self._total:
                return
            old = self._loaded
            new = min(old + self._WINDOW_STEP, self._total)
            if new <= old:
                return
            self.beginInsertRows(parent, old, new - 1)
            self._loaded = new
            self.endInsertRows()
            self.loadedChanged.emit(self._loaded, self._total)
            return
        node = self._node(parent)
        if node is None or node[0] != "frame":
            return
        r = node[1]
        if r in self._decoded or r in self._decode_err:
            return
        children = self._decode_frame(r)
        if children is None:
            self._decode_err[r] = self._last_err or "(解码失败)"
            self.beginInsertRows(parent, 0, 0)
            self.endInsertRows()
        else:
            self.beginInsertRows(parent, 0, len(children) - 1)
            self._decoded[r] = children
            self.endInsertRows()

    def frame_meta(self, r):
        """返回第 r 帧的 (frame_id, dlc)，供委托绘制高亮使用。"""
        row = self._frame_index.iloc[r]
        return int(row["frame_id"]), int(row["dlc"])

    # ───────── 数据显示 ─────────
    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        node = self._node(index)
        if node is None:
            return None
        col = index.column()
        if node[0] == "frame":
            if role != Qt.DisplayRole:
                return None
            r = node[1]
            row = self._frame_index.iloc[r]
            if col == 0:
                return str(int(row["frame_id"]))
            if col == 1:
                return f"{float(row['timestamp']):.6f}"
            if col == 2:
                return f"0x{int(row['arbitration_id']):03X}"
            if col == 3:
                return str(int(row["dlc"]))
            if col == 4:
                return str(int(row["channel"]))
            if col == 5:
                fid = int(row["frame_id"])
                dlc = int(row["dlc"])
                return " ".join(f"{b:02X}" for b in self._raw_data[fid, :dlc])
            return None
        else:
            if role != Qt.DisplayRole:
                return None
            r, s = node[1], node[2]
            return self._child_text(r, s, col)

    def _child_text(self, r, s, col):
        if r in self._decode_err:
            return self._decode_err[r] if col == 1 else ""
        child = self._decoded[r][s]
        if col == 0:
            return ""
        return child[col - 1]

    # ───────── 解码 ─────────
    def _decode_frame(self, r):
        """解码第 r 帧的信号，返回子项元组列表；失败返回 None 并写入 _last_err。"""
        if self._frame_index is None or self._raw_data is None:
            self._last_err = "(无数据)"
            return None
        row = self._frame_index.iloc[r]
        fid = int(row["frame_id"])
        dlc = int(row["dlc"])
        arb_id = int(row["arbitration_id"])
        frame_data = bytes(self._raw_data[fid, :dlc])

        db = self._db
        if db is None:
            self._last_err = "(未加载 DBC 数据库，请先加载 DBC 文件)"
            return None
        try:
            msg_def = db.get_message_by_frame_id(arb_id)
            if msg_def is None:
                self._last_err = f"(未找到 ID=0x{arb_id:X} 的报文定义)"
                return None
            decoded = msg_def.decode(frame_data)
            raw_decoded = self._decode_raw(db, arb_id, frame_data)
            children = []
            for sig_name, sig_value in decoded.items():
                sig_def = next((s for s in msg_def.signals if s.name == sig_name), None)
                unit = sig_def.unit if sig_def and sig_def.unit else ""
                raw_val = raw_decoded.get(sig_name)
                children.append((
                    sig_name,
                    self._raw_to_hex(raw_val),
                    self._val_to_text(sig_value),
                    unit,
                    self._desc_of(arb_id, sig_name, raw_val),
                ))
            return children
        except Exception as e:
            self._last_err = f"(解码失败: {e})"
            return None

    @staticmethod
    def _val_to_text(val) -> str:
        if val is None:
            return ""
        if hasattr(val, "name") and hasattr(val, "value"):
            return f"{val.value} ({val.name})"
        return str(val)

    @staticmethod
    def _raw_to_hex(val) -> str:
        if val is None:
            return ""
        try:
            return f"0x{int(val):X}"
        except (ValueError, TypeError):
            return ""

    def _decode_raw(self, db, arb_id: int, data: bytes) -> dict:
        try:
            msg = db.get_message_by_frame_id(arb_id)
            decoded = msg.decode(data, decode_choices=False, scaling=False)
            return dict(decoded)
        except Exception:
            return {}

    def _desc_of(self, arb_id: int, sig_name: str, raw_val) -> str:
        if raw_val is None:
            return ""
        try:
            key = int(raw_val)
        except (ValueError, TypeError):
            return ""
        desc = self._value_descriptions.get(sig_name, {}).get(key)
        if desc:
            return str(desc)
        if self._db is not None:
            try:
                msg = self._db.get_message_by_frame_id(arb_id)
                for s in msg.signals:
                    if s.name == sig_name and s.choices:
                        choice = s.choices.get(key)
                        if choice is not None:
                            return str(choice)
            except Exception:
                pass
        return ""


class HexDataDelegate(QStyledItemDelegate):
    """自定义委托：渲染 Data 列，对变化的字节高亮并渐变消退。

    高亮信息直接取自 MessageTableModel 的 byte_change 数组（按帧的 internalId 取行），
    不再持有 GB 级字典。
    """

    FADE_FRAMES = 500
    BOLD_FRAMES = 30
    HIGHLIGHT_COLOR = QColor("#FF6B6B")
    NORMAL_COLOR = QColor("#e0e0e0")
    BG_COLOR = QColor("#1e1e2e")

    def _get_byte_color(self, frames_since_change):
        if frames_since_change >= self.FADE_FRAMES:
            return self.NORMAL_COLOR
        ratio = frames_since_change / self.FADE_FRAMES
        r = int(self.HIGHLIGHT_COLOR.red() +
                (self.NORMAL_COLOR.red() - self.HIGHLIGHT_COLOR.red()) * ratio)
        g = int(self.HIGHLIGHT_COLOR.green() +
                (self.NORMAL_COLOR.green() - self.HIGHLIGHT_COLOR.green()) * ratio)
        b = int(self.HIGHLIGHT_COLOR.blue() +
                (self.NORMAL_COLOR.blue() - self.HIGHLIGHT_COLOR.blue()) * ratio)
        return QColor(r, g, b)

    def _is_bold(self, frames_since_change):
        return frames_since_change < self.BOLD_FRAMES

    def paint(self, painter, option, index):
        model = index.model()
        if not isinstance(model, MessageTableModel):
            super().paint(painter, option, index)
            return
        node = model._node(index)
        if node is None or node[0] != "frame" or index.column() != 5:
            super().paint(painter, option, index)
            return

        bc = model._byte_change
        raw = model._raw_data
        if bc is None or raw is None:
            super().paint(painter, option, index)
            return

        r = node[1]
        fid, dlc = model.frame_meta(r)
        change_row = bc[r]
        frame_data = raw[fid, :dlc]

        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        else:
            painter.fillRect(option.rect, self.BG_COLOR)

        font = QFont("Consolas", 9)
        fm = QFontMetrics(font)
        painter.setFont(font)

        char_x = option.rect.x() + 4
        char_y = (option.rect.y() +
                  (option.rect.height() - fm.height()) // 2 +
                  fm.ascent())

        for byte_idx, byte_val in enumerate(frame_data):
            frames_since = int(change_row[byte_idx])
            color = self._get_byte_color(frames_since)
            bold = self._is_bold(frames_since)
            font.setBold(bold)
            painter.setFont(font)
            hex_str = f"{byte_val:02X}"
            painter.setPen(color)
            painter.drawText(char_x, char_y, hex_str)
            char_x += fm.horizontalAdvance("00 ")

    def sizeHint(self, option, index):
        return QSize(300, 24)


class MessageTableWidget(QWidget):
    """报文表格组件，带过滤和按需解码功能"""

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
        QTreeView {
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
        QTreeView::item {
            padding: 4px 6px;
        }
        QTreeView::item:selected {
            background-color: #1e3a5a;
            color: #4fc3f7;
        }
        QTreeView::item:hover {
            background-color: #2a2a4e;
        }
        QTreeView::branch {
            background-color: #1e1e2e;
        }
        QTreeView::branch:closed:has-children {
            image: none;
            border-image: none;
        }
        QTreeView::branch:open:has-children {
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
        self._db = None
        self._filtered_index: pd.DataFrame | None = None
        self._value_descriptions: dict = {}

        self._model = MessageTableModel()
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

        self._apply_btn = QPushButton("🔍 应用过滤")
        self._apply_btn.setProperty("class", "primary")
        self._apply_btn.clicked.connect(self._apply_filter)
        filter_layout.addWidget(self._apply_btn)

        self._id_filter.lineEdit().returnPressed.connect(self._apply_filter)
        self._sig_filter.returnPressed.connect(self._apply_filter)
        self._time_start.returnPressed.connect(self._apply_filter)
        self._time_end.returnPressed.connect(self._apply_filter)

        self._reset_btn = QPushButton("重置")
        self._reset_btn.setToolTip("清除所有过滤条件")
        self._reset_btn.clicked.connect(self._reset_filter)
        filter_layout.addWidget(self._reset_btn)

        self._load_label = QLabel("")
        self._load_label.setStyleSheet("color:#9090a0; font-size:12px;")
        filter_layout.addWidget(self._load_label)

        filter_layout.addStretch()
        layout.addLayout(filter_layout)

        self._model.loadedChanged.connect(self._on_loaded_changed)

        # ─── 虚拟树形表格 ───
        self._tree = QTreeView()
        self._tree.setAlternatingRowColors(True)
        self._tree.setRootIsDecorated(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setSortingEnabled(False)
        header = self._tree.header()
        header.setStretchLastSection(True)
        header.resizeSection(0, 70)
        header.resizeSection(1, 200)
        header.resizeSection(2, 110)
        header.resizeSection(3, 110)
        header.resizeSection(4, 80)

        self._hex_delegate = HexDataDelegate(parent=self._tree)
        self._tree.setItemDelegateForColumn(5, self._hex_delegate)
        self._tree.setModel(self._model)

        layout.addWidget(self._tree, stretch=1)

    # ────────────────────── 公共接口 ──────────────────────

    def _on_loaded_changed(self, loaded: int, total: int):
        if total > 0 and loaded < total:
            self._load_label.setText(f"已加载 {loaded} / {total} 帧（滚动到底自动加载更多）")
        elif total > 0:
            self._load_label.setText(f"共 {total} 帧")
        else:
            self._load_label.setText("")

    def set_data(self, frame_index: pd.DataFrame, raw_data: np.ndarray,
                 messages: list[MessageDef], dbc_path: str = "",
                 byte_change: np.ndarray | None = None):
        """设置数据源（支持百万级帧，已移除 10000 行上限）。"""
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
        else:
            self._db = None

        if byte_change is None:
            max_dlc = int(raw_data.shape[1]) if raw_data is not None and raw_data.ndim == 2 else 8
            byte_change = compute_byte_change_array(frame_index, raw_data, max_dlc)

        self._model.set_dbc(self._db)
        self._model._value_descriptions = self._value_descriptions
        self._model.set_data(self._filtered_index, self._raw_data, byte_change)

        self._id_filter.clear()
        unique_ids = sorted(frame_index["arbitration_id"].unique())
        self._id_filter.addItem("全部")
        for aid in unique_ids:
            self._id_filter.addItem(f"0x{aid:03X}", aid)

    def get_filtered_index(self) -> pd.DataFrame | None:
        """返回当前过滤后的帧索引"""
        return self._filtered_index

    def update_dbc(self, dbc_path: str):
        """外部更新 DBC 数据库路径（例如主窗口加载 DBC 后通知）

        DBC 变化后旧解码结果失效：折叠全部并重置模型，强制下次展开时重新解码。
        """
        self._dbc_path = dbc_path
        try:
            db = load_dbc_database(dbc_path) if dbc_path else None
        except Exception:
            db = None
        self._db = db
        self._model.set_dbc(db)
        self._tree.collapseAll()
        self._model.beginResetModel()
        self._model._decoded.clear()
        self._model._decode_err.clear()
        self._model.endResetModel()

    def set_value_descriptions(self, descriptions: dict):
        """接收 DBC+Excel 合并的值描述 {sig_name: {int_val: 描述}}"""
        self._value_descriptions = descriptions or {}
        self._model._value_descriptions = self._value_descriptions

    # ────────────────────── 过滤逻辑 ──────────────────────

    def _apply_filter(self):
        if self._frame_index is None:
            return
        df = self._frame_index

        id_text = self._id_filter.currentText()
        if id_text and id_text != "全部":
            try:
                aid = int(id_text, 16)
                df = df[df["arbitration_id"] == aid]
            except ValueError:
                pass

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
                df = df.iloc[0:0]

        self._filtered_index = df
        self._recompute_and_set()

    def _reset_filter(self):
        self._id_filter.setCurrentText("全部")
        self._sig_filter.clear()
        self._time_start.clear()
        self._time_end.clear()
        self._filtered_index = self._frame_index
        self._recompute_and_set()

    def _recompute_and_set(self):
        """按当前过滤结果重算字节变化数组并刷新模型（向量化，主线程也很轻量）。"""
        df = self._filtered_index
        max_dlc = int(self._raw_data.shape[1]) if self._raw_data is not None and self._raw_data.ndim == 2 else 8
        byte_change = compute_byte_change_array(df, self._raw_data, max_dlc)
        self._model.set_data(df, self._raw_data, byte_change)
