# widgets/replay_widget.py
"""报文回放页：加载 BLF/ASC/LOG 报文日志，按原始时序回放到 CAN 总线。

支持市面上常见回放设置：
- 回放次数（1 次 / N 次）
- 循环回放（无限循环）
- 回放速度（0.1x 慢放 ~ 10x 快进，保持原帧间隔比例）
- 起始延迟（每轮开始前等待）
- 按报文 ID 勾选要回放的帧（其余跳过）
- 回放过程中把帧主动 fan-out 给监控页/报文页，便于实时观察

发送复用「连接状态」页注入的共享连接管理器（与模拟上报页同一总线），
因此无需在本页配置通道/波特率；未连接时按连接页下发的参数自动连接，
连接失败会弹窗说明（不再只写一行状态栏导致「点了没反应」的错觉）。

回放时钟以单调时间为基准批量推进：一个常驻定时器反复触发，按「已播放
时长」把到期的帧一次补发，因此 1x 严格贴近原始时间轴；逐帧新建 QTimer
的做法实测只有 194 帧/秒（0.33s 的日志要播 3s），已废弃。
"""
import os
import time

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFileDialog,
    QListWidget, QListWidgetItem, QAbstractItemView, QGroupBox, QSpinBox,
    QCheckBox, QComboBox, QLineEdit, QProgressBar, QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal

import can

from core.can_connection import CanConnectionManager
from core.can_utils import (
    DEFAULT_CHANNEL, DEFAULT_BITRATE, DEFAULT_INTERFACE_TYPE,
)
from core.log_loader import AscMessageReader
from utils.ui_scale import dp
from widgets.item_payload import get_payload, set_payload

# 单次定时器回调内最多连续发出的帧数。
# 用于限制「极端高密度日志 + 高倍速」下的单次阻塞时长：超出的帧留到下一次
# 回调继续追，避免一次 tick 几万帧把 UI 冻住。
_MAX_BURST = 2000
# 进度标签刷新间隔（秒）：逐帧 setText 会成为回放速度的瓶颈（实测仅 194 帧/秒）。
_UI_INTERVAL = 0.1
# 本页可接受的日志格式：前两种分析页也支持（拖入时同步加载分析页），
# 后两种只有 python-can 的 LogReader 能读，仅供本页回放。
ANALYZABLE_EXTS = (".blf", ".asc")
SUPPORTED_EXTS = ANALYZABLE_EXTS + (".log", ".csv")


class _BlfLoadThread(QThread):
    """后台线程：读取日志文件全部帧（避免大文件阻塞 UI）。"""
    progress = pyqtSignal(int)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path

    @staticmethod
    def _read_pos(reader) -> int:
        """尽力取读取游标（ASC 支持；其他 Reader 不支持时返回 0，进度显示为 0%）。"""
        fn = getattr(reader, "read_position", None)
        if callable(fn):
            try:
                return int(fn())
            except Exception:  # noqa: BLE001
                return 0
        return 0

    def run(self):
        try:
            msgs = []
            # .asc 走自研解析器：python-can 的 ASCReader 在解析文件头时会吞掉
            # 首帧（详见 core/log_loader.AscMessageReader 说明）。若这里用
            # LogReader，会出现「分析页 133898 帧、回放页 133897 帧」的不一致。
            # 其余格式（.blf / .log / .csv）仍交给 LogReader 自动识别。
            ext = os.path.splitext(self._path)[1].lower()
            reader = (AscMessageReader(self._path) if ext == ".asc"
                      else can.LogReader(self._path))
            # 文件较大时（几十万帧）加载要数秒，没有进度会让用户以为「没反应」
            try:
                total = os.path.getsize(self._path)
            except OSError:
                total = 0
            last_pct = -1
            with reader:
                for m in reader:
                    msgs.append(m)
                    if total > 0 and len(msgs) % 2000 == 0:
                        pct = min(99, self._read_pos(reader) * 100 // total)
                        if pct != last_pct:
                            last_pct = pct
                            self.progress.emit(pct)
            self.progress.emit(100)
            self.finished.emit(msgs)
        except Exception as e:  # noqa: BLE001
            self.error.emit(str(e))


class ReplayWidget(QWidget):
    """报文回放页"""

    # 文件被拖入本页（路径）：主窗口据此同步把同一份日志加载到分析页。
    file_dropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._manager: CanConnectionManager | None = None
        self._dbc = None
        self._frames: list = []        # 全部加载的 can.Message（按时间序）
        self._play_frames: list = []   # 当前勾选 ID 过滤后的回放帧
        self._t: list = []             # play_frames 的相对时间戳（秒）
        self._id_counts: dict = {}     # arbitration_id -> 帧数
        self._bus = None
        self._playing = False
        self._idx = 0                  # 下一帧索引
        self._pass = 0                 # 已完成轮数
        self._total_passes = 1
        self._loop = False
        self._scale = 1.0
        self._start_delay_ms = 0
        self._load_thread: _BlfLoadThread | None = None
        self._loading_path = ""        # 正在加载的文件路径
        self._loaded_path = ""         # 已完成加载的文件路径（去重用）
        # 连接参数：仅在「连接状态」页尚未建立连接时作为自动连接的兜底配置，
        # 与模拟上报/实时监控/实时报文三页同一套机制（set_connection 注入）。
        self._interface_type = DEFAULT_INTERFACE_TYPE
        self._channel = DEFAULT_CHANNEL
        self._bitrate = DEFAULT_BITRATE
        # 回放时钟：以 monotonic 为基准的「本轮播放起点」，发送耗时会被扣除，
        # 保证 1x 时严格按原始时间轴推进（旧实现逐帧 QTimer 只有 194 帧/秒）。
        self._clock_ref = 0.0
        self._failed = 0               # 本轮累计发送失败帧数
        self._last_error = ""          # 最近一次发送错误（完成后汇总展示）
        self._ui_ts = 0.0              # 上次刷新进度标签的时刻
        # 常驻定时器：一个定时器反复触发，不再每帧新建 QTimer
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(1)
        self._timer.timeout.connect(self._on_tick)
        # 接受把日志文件直接拖到本页（与拖到主窗口等价，见 dropEvent）
        self.setAcceptDrops(True)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(dp(8), dp(8), dp(8), dp(8))
        layout.setSpacing(dp(6))

        # ─── 文件选择 ───
        file_bar = QHBoxLayout()
        file_bar.setSpacing(dp(6))
        self._path_edit = QLineEdit()
        self._path_edit.setReadOnly(True)
        self._path_edit.setPlaceholderText("未选择日志文件")
        file_bar.addWidget(self._path_edit, stretch=1)
        self._choose_btn = QPushButton("选择文件")
        self._choose_btn.clicked.connect(self._choose_file)
        file_bar.addWidget(self._choose_btn)
        layout.addLayout(file_bar)

        self._info_label = QLabel("请选择 BLF / ASC / LOG 报文日志文件")
        self._info_label.setStyleSheet("color: #9090a0;")
        layout.addWidget(self._info_label)

        self._load_bar = QProgressBar()
        self._load_bar.setVisible(False)
        layout.addWidget(self._load_bar)

        # ─── 回放设置 ───
        settings = QGroupBox("回放设置")
        s_layout = QHBoxLayout(settings)
        s_layout.setSpacing(dp(8))

        s_layout.addWidget(QLabel("回放次数:"))
        self._passes_spin = QSpinBox()
        self._passes_spin.setRange(1, 999999)
        self._passes_spin.setValue(1)
        self._passes_spin.setMinimumWidth(dp(64))
        s_layout.addWidget(self._passes_spin)

        self._loop_chk = QCheckBox("循环回放")
        s_layout.addWidget(self._loop_chk)

        s_layout.addWidget(QLabel("速度:"))
        self._speed_combo = QComboBox()
        for label, factor in (
            ("0.1x (慢放)", 0.1), ("0.25x", 0.25), ("0.5x", 0.5),
            ("1x (原速)", 1.0), ("2x", 2.0), ("5x", 5.0), ("10x (快进)", 10.0),
        ):
            self._speed_combo.addItem(label, factor)
        self._speed_combo.setCurrentIndex(3)
        self._speed_combo.setMinimumWidth(dp(80))
        s_layout.addWidget(self._speed_combo)

        s_layout.addWidget(QLabel("起始延迟(ms):"))
        self._delay_spin = QSpinBox()
        self._delay_spin.setRange(0, 60000)
        self._delay_spin.setValue(0)
        self._delay_spin.setMinimumWidth(dp(72))
        s_layout.addWidget(self._delay_spin)

        s_layout.addStretch()
        layout.addWidget(settings)

        # ─── 控制按钮 ───
        ctrl = QHBoxLayout()
        ctrl.setSpacing(dp(6))
        self._start_btn = QPushButton("▶ 开始回放")
        self._start_btn.setProperty("class", "primary")
        self._start_btn.clicked.connect(self._start_replay)
        ctrl.addWidget(self._start_btn)
        self._stop_btn = QPushButton("■ 停止回放")
        self._stop_btn.clicked.connect(self._stop_replay)
        self._stop_btn.setEnabled(False)
        ctrl.addWidget(self._stop_btn)
        ctrl.addStretch()
        self._progress_label = QLabel("")
        self._progress_label.setStyleSheet("color: #9090a0;")
        ctrl.addWidget(self._progress_label)
        layout.addLayout(ctrl)

        # ─── 报文 ID 勾选列表（可只回放部分 ID）───
        id_group = QGroupBox("回放报文 ID（勾选要回放的帧）")
        id_layout = QVBoxLayout(id_group)
        id_bar = QHBoxLayout()
        self._id_all_btn = QPushButton("全选")
        self._id_all_btn.clicked.connect(lambda: self._set_all_ids(True))
        id_bar.addWidget(self._id_all_btn)
        self._id_none_btn = QPushButton("全不选")
        self._id_none_btn.clicked.connect(lambda: self._set_all_ids(False))
        id_bar.addWidget(self._id_none_btn)
        id_bar.addStretch()
        id_layout.addLayout(id_bar)
        self._id_list = QListWidget()
        self._id_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._id_list.setAlternatingRowColors(True)
        id_layout.addWidget(self._id_list)
        layout.addWidget(id_group, stretch=1)

        self._status_label = QLabel("就绪")
        self._status_label.setStyleSheet("color: #9090a0;")
        layout.addWidget(self._status_label)

    # ────────────────────── 公共接口 ──────────────────────

    def set_connection_manager(self, manager: CanConnectionManager):
        self._manager = manager

    def set_connection(self, interface_type: str, channel: str, bitrate: int):
        """由连接状态页下发设备参数（与模拟上报页同签名）。

        回放页不强制用户「先去连接页点连接」：未连接时用这里注入的参数
        自动连接，已连接则直接复用共享总线。
        """
        self._interface_type = interface_type
        self._channel = channel
        self._bitrate = bitrate

    def _ensure_bus(self) -> bool:
        """确保共享总线可用：已连接复用，未连接按配置自动连接。

        返回 False 时已弹窗说明原因——旧实现只在底部状态栏写一行灰字，
        用户极易忽略并误判为「点了没反应」。
        """
        if self._manager is None:
            QMessageBox.warning(self, "连接失败", "连接管理器未初始化")
            self._status_label.setText("连接管理器未初始化")
            return False
        cfg = self._manager.get_config()
        if cfg:
            it, ch, br = cfg["interface_type"], cfg["channel"], cfg["bitrate"]
        else:
            it, ch, br = self._interface_type, self._channel, self._bitrate
        bus, err = self._manager.ensure_connected(it, ch, br)
        if bus is None:
            self._status_label.setText(f"连接失败: {err}")
            QMessageBox.warning(
                self, "连接失败",
                f"无法建立 CAN 连接（{it} / {ch} @ {br}）：\n{err}",
            )
            return False
        self._bus = bus
        return True

    def set_dbc_path(self, dbc_path: str):
        """注入 DBC 路径用于回放列表中显示报文名（可选，仅影响展示）。"""
        self._dbc = None
        if dbc_path:
            try:
                import cantools
                self._dbc = cantools.database.load_file(dbc_path)
            except Exception:  # noqa: BLE001
                self._dbc = None
        # DBC 变化后刷新 ID 列表中的报文名
        if self._id_counts:
            self._refresh_id_list()

    # ────────────────────── 文件加载 ──────────────────────

    @staticmethod
    def _norm_path(path: str) -> str:
        """路径归一化，用于「同一文件」判定。

        拖放经 QUrl.toLocalFile() 得到的是正斜杠路径，而菜单/配置里存的
        可能是反斜杠；不做归一化会把同一文件当成两份、重复解析几十万帧。
        normcase 同时抹平 Windows 的大小写差异。
        """
        return os.path.normcase(os.path.abspath(path))

    def load_file(self, path: str) -> bool:
        """外部入口：把指定日志加载为本页的可回放报文。

        供两处调用：主窗口的 dropEvent（拖进应用即同时成为可回放报文）、
        以及本页自身的拖放。返回是否受理；不受理时不改变当前已加载的报文，
        用户仍可用「选择文件」另选一份覆盖。
        """
        if not path or not os.path.isfile(path):
            return False
        if os.path.splitext(path)[1].lower() not in SUPPORTED_EXTS:
            return False
        # 同一份文件已在本页加载过（例如从回放页拖入后主窗口又回调一次）：
        # 直接视为成功，避免重复解析几十万帧。
        if self._norm_path(path) == self._loaded_path and self._frames:
            self._path_edit.setText(path)
            return True
        self._path_edit.setText(path)
        self._load_file(path)
        return True

    def dragEnterEvent(self, event):
        """仅接受本页支持的日志扩展名，其余交给主窗口处理。"""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                p = url.toLocalFile().lower()
                if p.endswith(SUPPORTED_EXTS):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        """拖入日志：本页加载为可回放报文；分析页支持的格式再通知主窗口。"""
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if not self.load_file(path):
                continue
            event.acceptProposedAction()
            # .blf/.asc 分析页也能读 → 让它同时成为「待分析报文」；
            # .log/.csv 分析页不支持，只在本页回放。
            if path.lower().endswith(ANALYZABLE_EXTS):
                self.file_dropped.emit(path)
            return
        event.ignore()

    def _choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择报文日志文件", "",
            "CAN Log Files (*.blf *.asc *.log *.csv);;BLF Files (*.blf);;"
            "ASC Files (*.asc);;All Files (*)",
        )
        if not path:
            return
        self._path_edit.setText(path)
        self._load_file(path)

    def _load_file(self, path: str):
        self._stop_replay("加载新文件")
        self._frames = []
        self._id_counts = {}
        self._loaded_path = ""
        self._loading_path = self._norm_path(path)
        self._id_list.clear()
        self._info_label.setText(f"正在加载 {os.path.basename(path)} … 0%")
        self._load_bar.setVisible(True)
        self._load_bar.setValue(0)
        self._load_thread = _BlfLoadThread(path)
        self._load_thread.progress.connect(self._on_load_progress)
        self._load_thread.finished.connect(self._on_loaded)
        self._load_thread.error.connect(self._on_load_error)
        self._load_thread.start()

    def _on_load_progress(self, pct: int):
        self._load_bar.setValue(pct)
        if pct < 100:
            self._info_label.setText(f"正在加载 … {pct}%")

    def _on_loaded(self, frames: list):
        self._load_bar.setVisible(False)
        self._frames = frames
        self._loaded_path = self._loading_path
        self._id_counts = {}
        for m in frames:
            self._id_counts[m.arbitration_id] = self._id_counts.get(m.arbitration_id, 0) + 1
        if not frames:
            self._info_label.setText("文件为空或无有效帧")
            self._status_label.setText("就绪")
            return
        t0 = frames[0].timestamp
        span = frames[-1].timestamp - t0
        self._info_label.setText(
            f"共 {len(frames)} 帧 / {len(self._id_counts)} 个 ID / "
            f"时长 {span:.2f}s"
        )
        self._refresh_id_list()
        self._status_label.setText("就绪，点击「开始回放」")

    def _on_load_error(self, err: str):
        self._load_bar.setVisible(False)
        self._info_label.setText("加载失败")
        self._status_label.setText(f"加载失败: {err}")
        # 加载失败必须弹窗：只写状态栏时用户会以为「选了文件没反应」
        QMessageBox.warning(self, "加载失败", err)

    def _refresh_id_list(self):
        """刷新 ID 勾选列表（带帧数与 DBC 报文名）。"""
        self._id_list.blockSignals(True)
        self._id_list.clear()
        for fid in sorted(self._id_counts.keys()):
            msg_name = ""
            if self._dbc is not None:
                try:
                    msg_name = self._dbc.get_message_by_frame_id(fid).name
                except Exception:  # noqa: BLE001
                    msg_name = ""
            text = f"0x{fid:03X}  ({self._id_counts[fid]} 帧)"
            if msg_name:
                text += f"  {msg_name}"
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            set_payload(item, fid)
            self._id_list.addItem(item)
        self._id_list.blockSignals(False)

    def _set_all_ids(self, checked: bool):
        for i in range(self._id_list.count()):
            self._id_list.item(i).setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )

    def _checked_ids(self) -> set:
        ids = set()
        for i in range(self._id_list.count()):
            item = self._id_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                ids.add(get_payload(item))
        return ids

    # ────────────────────── 回放控制 ──────────────────────

    def _start_replay(self):
        if self._playing:
            return
        if not self._frames:
            QMessageBox.information(self, "提示", "请先加载报文日志文件")
            self._status_label.setText("请先加载日志文件")
            return
        checked = self._checked_ids()
        if not checked:
            QMessageBox.information(self, "提示", "请至少勾选一个回放 ID")
            self._status_label.setText("请至少勾选一个回放 ID")
            return
        if not self._ensure_bus():
            return

        # 按勾选 ID 过滤，保留相对时序
        play = [m for m in self._frames if m.arbitration_id in checked]
        if not play:
            QMessageBox.information(self, "提示", "勾选的 ID 没有对应帧")
            self._status_label.setText("勾选的 ID 无对应帧")
            return
        self._play_frames = play
        t0 = play[0].timestamp
        self._t = [m.timestamp - t0 for m in play]

        self._total_passes = self._passes_spin.value()
        self._loop = self._loop_chk.isChecked()
        self._scale = float(self._speed_combo.currentData())
        self._start_delay_ms = self._delay_spin.value()
        self._idx = 0
        self._pass = 0
        self._failed = 0
        self._ui_ts = 0.0
        self._playing = True
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._status_label.setText("回放中…")
        self._progress_label.setText(f"帧 0/{len(self._play_frames)}  第 1 轮")
        self._begin_clock()

    def _begin_clock(self):
        """启动本轮播放时钟并开启常驻定时器。

        时钟用 monotonic 绝对时刻而不是「逐帧累加间隔」：逐帧累加会把每次
        定时器的调度误差累积起来，1x 下越播越慢。
        """
        self._clock_ref = time.monotonic() + self._start_delay_ms / 1000.0
        self._timer.start()

    def _stop_replay(self, reason: str = "已停止"):
        self._timer.stop()
        self._playing = False
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        if reason:
            self._status_label.setText(reason)
        self._progress_label.setText("")

    def _on_tick(self):
        """定时器回调：按「已播放时长」把到期的帧一次性补发出去。

        不再为每帧新建 QTimer（旧实现实测只有 194 帧/秒，原时序 0.33s 的
        数据要播 3s，表现为「进度几乎不动」）。
        """
        if not self._playing:
            return
        now = time.monotonic()
        if now < self._clock_ref:
            return                          # 起始延迟未到
        elapsed = now - self._clock_ref

        n = len(self._play_frames)
        if self._idx >= n:
            # 本轮帧已发完：等尾部时间轴走完再进入下一轮，否则长间隔日志
            # 会被「提前循环」压缩掉尾部静默时间。
            # ⚠️ 必须换算到原始时间轴比较：elapsed 是真实秒数，t[-1] 是原始秒数，
            # 直接比较会让 >1x 的回放永远停在「帧已发完但不结束」。
            if self._t and elapsed * self._scale < self._t[-1]:
                return
            self._pass += 1
            if (not self._loop) and (self._pass >= self._total_passes):
                self._finish()
                return
            self._idx = 0
            self._failed = 0
            if self._loop:
                self._status_label.setText(f"循环回放中（第 {self._pass + 1} 轮）")
            else:
                self._status_label.setText(
                    f"回放中（第 {self._pass + 1}/{self._total_passes} 轮）"
                )
            self._begin_clock()
            return

        limit = elapsed * self._scale        # 换算到原始时间轴上的位置
        budget = _MAX_BURST
        while self._idx < n and self._t[self._idx] <= limit and budget > 0:
            self._send_one(self._idx)
            self._idx += 1
            budget -= 1
        # 发送本身耗时不推进播放时钟：高密度日志下若把发送耗时也算进时间轴，
        # 进度会永久落后并越欠越多。
        self._clock_ref += time.monotonic() - now
        self._update_progress()

    def _finish(self):
        """全部轮次结束：报告结果（含发送失败统计）。"""
        total = len(self._play_frames) * self._total_passes
        if self._failed:
            self._stop_replay(
                f"回放完成，但有 {self._failed} 帧发送失败"
                f"（最近错误: {self._last_error}）"
            )
        else:
            self._stop_replay("回放完成")
        self._progress_label.setText(
            f"完成：{self._total_passes} 轮 / {total} 帧"
        )

    def _update_progress(self, force: bool = False):
        """按固定间隔刷新进度标签（逐帧刷新会成为速度瓶颈）。"""
        now = time.monotonic()
        if not force and now - self._ui_ts < _UI_INTERVAL:
            return
        self._ui_ts = now
        text = (f"帧 {self._idx}/{len(self._play_frames)}"
                f"  第 {self._pass + 1} 轮")
        if self._failed:
            text += f"  失败 {self._failed}"
        self._progress_label.setText(text)

    def _send_one(self, i: int):
        msg = self._play_frames[i]
        try:
            clean = can.Message(
                arbitration_id=msg.arbitration_id,
                data=msg.data,
                is_extended_id=msg.is_extended_id,
                timestamp=msg.timestamp,
            )
            self._bus.send(clean)
            if self._manager is not None:
                self._manager.dispatch(clean)
        except Exception as e:  # noqa: BLE001
            # 只在首次失败时写状态栏：逐帧写会被后续失败反复覆盖并拖慢回放。
            # 累计数在进度标签与完成提示中体现。
            self._failed += 1
            self._last_error = str(e)
            if self._failed == 1:
                self._status_label.setText(f"发送失败: {e}")

    def closeEvent(self, event):
        self._stop_replay("")
        super().closeEvent(event)

    def stop(self):
        """供主窗口退出时强制停止回放。"""
        self._stop_replay("")
