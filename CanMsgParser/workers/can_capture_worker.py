# workers/can_capture_worker.py
"""信号实时监控后台 worker：通过 PCAN 实时接收 CAN 帧并解码勾选信号

设计要点：
- 在独立 QThread 中创建并使用独立的 can.Bus，不跨线程共享总线对象。
- 通过阻塞式 bus.recv(timeout) 轮询，配合 _running 标志实现干净退出，
  避免 Notifier 额外线程带来的信号跨线程复杂度。
- 解码后的采样点通过 sample_received 信号回传 GUI 线程（Qt 自动排队连接）。
"""
from PyQt5.QtCore import QThread, pyqtSignal

import time

from core.can_utils import (
    connect_bus, load_dbc, build_signal_slots, decode_frame,
)


class CanCaptureWorker(QThread):
    """实时接收并解码选中信号的后台线程"""

    # (msg_name, sig_name, 相对时间秒, 物理值)
    sample_received = pyqtSignal(str, str, float, float)
    status_changed = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, dbc_path: str, signals: list,
                 channel: str = "PCAN_USBBUS1", bitrate: int = 500000):
        super().__init__()
        self._dbc_path = dbc_path
        self._signals = signals
        self._channel = channel
        self._bitrate = bitrate
        self._running = False

    def run(self):
        self._running = True
        bus, err = connect_bus(self._channel, self._bitrate)
        if bus is None:
            self.error_occurred.emit(err)
            return
        try:
            db, err = load_dbc(self._dbc_path)
            if db is None:
                self.error_occurred.emit(err)
                return
            slots, err = build_signal_slots(db, self._signals)
            if slots is None:
                self.error_occurred.emit(err)
                return

            # frame_id -> [(msg_name, sig_name), ...]，仅解码选中的信号
            frame_map: dict = {}
            for slot in slots:
                frame_map.setdefault(slot.frame_id, []).append(
                    (slot.msg_name, slot.sig_name)
                )

            self.status_changed.emit(
                f"已连接 PCAN ({self._channel} @ {self._bitrate}bps)，"
                f"监控 {len(self._signals)} 个信号"
            )
            start = time.time()
            while self._running:
                msg = bus.recv(timeout=0.05)
                if msg is None:
                    continue
                targets = frame_map.get(msg.arbitration_id)
                if not targets:
                    continue
                decoded = decode_frame(db, msg.arbitration_id, bytes(msg.data))
                if not decoded:
                    continue
                t = time.time() - start
                for (m, s) in targets:
                    if s in decoded:
                        self.sample_received.emit(m, s, t, float(decoded[s]))
        except Exception as e:  # noqa: BLE001 — 统一上报异常，避免线程静默崩溃
            self.error_occurred.emit(f"监控异常: {e}")
        finally:
            try:
                bus.shutdown()
            except Exception:  # noqa: BLE001
                pass
            self.status_changed.emit("监控已停止")

    def stop(self):
        """请求停止监控（线程在下一次 recv 超时后退出）"""
        self._running = False
