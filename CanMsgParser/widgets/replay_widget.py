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
因此无需在本页配置通道/波特率。
"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFileDialog,
    QListWidget, QListWidgetItem, QAbstractItemView, QGroupBox, QSpinBox,
    QCheckBox, QComboBox, QLineEdit, QProgressBar,
)
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal

import can

from core.can_connection import CanConnectionManager


class _BlfLoadThread(QThread):
    """后台线程：读取日志文件全部帧（避免大文件阻塞 UI）。"""
    progress = pyqtSignal(int)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path

    def run(self):
        try:
            msgs = []
            # LogReader 自动识别 .blf / .asc / .log / .csv 等格式
            with can.LogReader(self._path) as reader:
                for m in reader:
                    msgs.append(m)
            self.progress.emit(100)
            self.finished.emit(msgs)
        except Exception as e:  # noqa: BLE001
            self.error.emit(str(e))


class ReplayWidget(QWidget):
    """报文回放页"""

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
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # ─── 文件选择 ───
        file_bar = QHBoxLayout()
        file_bar.setSpacing(8)
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
        s_layout.setSpacing(10)

        s_layout.addWidget(QLabel("回放次数:"))
        self._passes_spin = QSpinBox()
        self._passes_spin.setRange(1, 999999)
        self._passes_spin.setValue(1)
        self._passes_spin.setMinimumWidth(70)
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
        self._speed_combo.setMinimumWidth(90)
        s_layout.addWidget(self._speed_combo)

        s_layout.addWidget(QLabel("起始延迟(ms):"))
        self._delay_spin = QSpinBox()
        self._delay_spin.setRange(0, 60000)
        self._delay_spin.setValue(0)
        self._delay_spin.setMinimumWidth(80)
        s_layout.addWidget(self._delay_spin)

        s_layout.addStretch()
        layout.addWidget(settings)

        # ─── 控制按钮 ───
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)
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
        self._id_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._id_list.setAlternatingRowColors(True)
        id_layout.addWidget(self._id_list)
        layout.addWidget(id_group, stretch=1)

        self._status_label = QLabel("就绪")
        self._status_label.setStyleSheet("color: #9090a0;")
        layout.addWidget(self._status_label)

    # ────────────────────── 公共接口 ──────────────────────

    def set_connection_manager(self, manager: CanConnectionManager):
        self._manager = manager

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
        self._id_list.clear()
        self._info_label.setText(f"正在加载 {path} …")
        self._load_bar.setVisible(True)
        self._load_bar.setValue(0)
        self._load_thread = _BlfLoadThread(path)
        self._load_thread.progress.connect(self._load_bar.setValue)
        self._load_thread.finished.connect(self._on_loaded)
        self._load_thread.error.connect(self._on_load_error)
        self._load_thread.start()

    def _on_loaded(self, frames: list):
        self._load_bar.setVisible(False)
        self._frames = frames
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
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, fid)
            self._id_list.addItem(item)
        self._id_list.blockSignals(False)

    def _set_all_ids(self, checked: bool):
        for i in range(self._id_list.count()):
            self._id_list.item(i).setCheckState(
                Qt.Checked if checked else Qt.Unchecked
            )

    def _checked_ids(self) -> set:
        ids = set()
        for i in range(self._id_list.count()):
            item = self._id_list.item(i)
            if item.checkState() == Qt.Checked:
                ids.add(item.data(Qt.UserRole))
        return ids

    # ────────────────────── 回放控制 ──────────────────────

    def _start_replay(self):
        if self._playing:
            return
        if not self._frames:
            self._status_label.setText("请先加载日志文件")
            return
        checked = self._checked_ids()
        if not checked:
            self._status_label.setText("请至少勾选一个回放 ID")
            return
        cfg = self._manager.get_config() if self._manager else None
        if cfg is None:
            self._status_label.setText("请先在「连接状态」页连接 CAN 设备")
            return
        bus, err = self._manager.ensure_connected(
            cfg["interface_type"], cfg["channel"], cfg["bitrate"]
        )
        if bus is None:
            self._status_label.setText(f"连接失败: {err}")
            return
        self._bus = bus

        # 按勾选 ID 过滤，保留相对时序
        play = [m for m in self._frames if m.arbitration_id in checked]
        if not play:
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
        self._playing = True
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._status_label.setText("回放中…")
        QTimer.singleShot(int(self._start_delay_ms), self._tick)

    def _stop_replay(self, reason: str = "已停止"):
        if not self._playing and reason == "已停止":
            # 即便未播放也允许清理状态（如加载新文件时）
            pass
        self._playing = False
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        if reason:
            self._status_label.setText(reason)
        self._progress_label.setText("")

    def _tick(self):
        if not self._playing:
            return
        if self._idx >= len(self._play_frames):
            # 本轮结束
            self._pass += 1
            last_pass = (not self._loop) and (self._pass >= self._total_passes)
            if last_pass:
                self._stop_replay("回放完成")
                self._progress_label.setText(
                    f"完成：{self._total_passes} 轮 / {len(self._play_frames)} 帧"
                )
                return
            # 循环 or 还有下一轮：重置索引，进入下一轮
            self._idx = 0
            if self._loop:
                self._status_label.setText(f"循环回放中（第 {self._pass + 1} 轮）")
            else:
                self._status_label.setText(f"回放中（第 {self._pass + 1}/{self._total_passes} 轮）")
            QTimer.singleShot(int(self._start_delay_ms), self._tick)
            return

        self._send_one(self._idx)
        self._idx += 1

        if self._idx < len(self._play_frames):
            gap = (self._t[self._idx] - self._t[self._idx - 1]) / self._scale
            delay = max(0, int(gap * 1000))
        else:
            delay = 0  # 本轮最后帧，下一 tick 处理轮次切换
        QTimer.singleShot(delay, self._tick)

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
            self._status_label.setText(f"发送失败: {e}")
        self._progress_label.setText(
            f"帧 {i + 1}/{len(self._play_frames)}  第 {self._pass + 1} 轮"
        )

    def closeEvent(self, event):
        self._stop_replay("")
        super().closeEvent(event)

    def stop(self):
        """供主窗口退出时强制停止回放。"""
        self._stop_replay("")
