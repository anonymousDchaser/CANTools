# workers/can_capture_worker.py
"""信号实时监控处理器：通过共享总线接收 CAN 帧并解码勾选信号。

设计要点（共享连接架构）：
- 不再自管 can.Bus，也不自起收帧线程。总线由 CanConnectionManager 在 GUI
  主线程创建并保持唯一一条；所有帧由管理器唯一的收帧线程 fan-out 出来，
  本处理器作为监听者被回调 process_message(msg) 处理。
- 解码后的采样点通过 sample_received 信号回传 GUI 线程（控件以
  Qt.QueuedConnection 连接，Qt 自动排队到 GUI 线程执行）。
- 本类为普通 QObject（非线程），重活（解码）在管理器的收帧线程内同步完成，
  信号发射以队列方式回到 GUI，避免跨线程直接操作界面。
"""
import time
import threading

from PyQt5.QtCore import QObject, pyqtSignal

from core.can_utils import load_dbc, build_signal_slots, decode_frame


class CanCaptureWorker(QObject):
    """实时接收并解码选中信号的处理器（由连接管理器驱动）"""

    # (frame_id, msg_name, sig_name, 相对时间秒, 物理值)
    sample_received = pyqtSignal(int, str, str, float, float)
    status_changed = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, dbc_path: str, signals: list):
        super().__init__()
        self._dbc_path = dbc_path
        self._signals = signals
        self._running = False
        self._start_ts = 0.0
        self._db = None
        self._frame_map: dict = {}
        self._lock = threading.Lock()  # 保护 _frame_map（收帧线程与 GUI 线程并发读写）
        self._init_error: str | None = None
        # 预加载 DBC 与信号槽（原 run() 开头部分），失败仅为后续上报错误
        try:
            db, err = load_dbc(self._dbc_path)
            if db is None:
                self._init_error = err
            else:
                slots, err = build_signal_slots(db, self._signals)
                if slots is None:
                    self._init_error = err
                else:
                    self._db = db
                    for slot in slots:
                        self._frame_map.setdefault(slot.frame_id, []).append(
                            (slot.msg_name, slot.sig_name)
                        )
        except Exception as e:  # noqa: BLE001
            self._init_error = f"监控初始化失败: {e}"

    def start_monitoring(self) -> bool:
        """由控件在 ensure_connected 之后调用：就绪并返回是否成功。"""
        if self._init_error is not None:
            self.error_occurred.emit(self._init_error)
            return False
        self._running = True
        self._start_ts = time.time()
        self.status_changed.emit(
            f"监控中，监控 {len(self._signals)} 个信号"
        )
        return True

    def stop(self):
        """请求停止监控（仅置标志，收帧线程由管理器统一管控）。"""
        self._running = False

    def add_signal(self, msg_name: str, sig_name: str):
        """运行时新增一个采集信号（监控进行中加入已选信号时调用）。"""
        if (msg_name, sig_name) in self._signals:
            return
        self._signals.append((msg_name, sig_name))
        self._rebuild_frame_map()

    def remove_signal(self, msg_name: str, sig_name: str):
        """运行时移除一个采集信号（监控进行中移除已选信号时调用）。"""
        self._signals = [
            (m, s) for (m, s) in self._signals
            if not (m == msg_name and s == sig_name)
        ]
        self._rebuild_frame_map()

    def sync_signals(self, selected: set):
        """使采集信号集合与 selected 一致：移除不在 selected 中的，加入缺失的。

        供「实时监控页」在已选信号增删后调用，使采集范围与实际选择同步。
        """
        cur = set(self._signals)
        need_add = [s for s in selected if s not in cur]
        self._signals = [s for s in self._signals if s in selected]
        self._signals.extend(need_add)
        self._rebuild_frame_map()

    def _rebuild_frame_map(self):
        """依据当前 _signals 重建 frame_id -> [(msg_name, sig_name)] 映射（线程安全）。"""
        with self._lock:
            self._frame_map.clear()
            if self._db is not None:
                try:
                    slots, _err = build_signal_slots(self._db, self._signals)
                    if slots is not None:
                        for slot in slots:
                            self._frame_map.setdefault(
                                slot.frame_id, []
                            ).append((slot.msg_name, slot.sig_name))
                except Exception:  # noqa: BLE001
                    pass

    def process_message(self, msg):
        """由连接管理器收帧线程在收到每帧时回调：解码并回传勾选信号。"""
        if not self._running or self._db is None:
            return
        # 在锁内拷贝目标列表，避免与 GUI 线程的 sync/add/remove 并发改 _frame_map
        with self._lock:
            targets = self._frame_map.get(msg.arbitration_id)
            if not targets:
                return
            targets = list(targets)
        decoded = decode_frame(self._db, msg.arbitration_id, bytes(msg.data))
        if not decoded:
            return
        t = time.time() - self._start_ts
        for (m, s) in targets:
            if s in decoded:
                val = decoded[s]
                # cantools 新版本对枚举信号返回 NamedSignalValue，取数值用于绘图
                if hasattr(val, "value"):
                    val = val.value
                self.sample_received.emit(msg.arbitration_id, m, s, t, float(val))
