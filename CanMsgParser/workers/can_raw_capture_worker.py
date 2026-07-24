# workers/can_raw_capture_worker.py
"""实时原始报文捕获后台 worker：接收总线上全部 CAN 帧并可选录制为 BLF

设计要点：
- 独立 QThread 中创建并使用独立的 can.Bus，不跨线程共享总线对象。
- 接收全部帧（不做信号筛选），通过 frame_received 信号回传每帧原始信息，
  供「实时报文页」以「同 ID 单行」方式展示。
- 支持运行中开始/停止录制：调用 start_recording(path) 创建 BLFWriter，
  每收到一帧即写入；stop_recording() 关闭写入器。录制写入由锁保护。
"""
import threading
import time

from PyQt5.QtCore import QThread, pyqtSignal

import can

from core.can_utils import connect_bus


class CanRawCaptureWorker(QThread):
    """实时接收全部 CAN 帧并可选录制的后台线程"""

    # (相对时间秒, 报文ID, DLC, 数据字节, 是否扩展帧, 是否FD)
    frame_received = pyqtSignal(float, int, int, bytes, bool, bool)
    status_changed = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, channel: str = "PCAN_USBBUS1", bitrate: int = 500000,
                 record_path: str | None = None):
        super().__init__()
        self._channel = channel
        self._bitrate = bitrate
        self._record_path = record_path
        self._running = False
        self._writer = None
        self._writer_lock = threading.Lock()

    def run(self):
        self._running = True
        bus, err = connect_bus(self._channel, self._bitrate)
        if bus is None:
            self.error_occurred.emit(err)
            return
        try:
            # 构造时传入的录制路径：启动时即开始录制
            if self._record_path:
                try:
                    self._writer = can.BLFWriter(self._record_path)
                except Exception as e:  # noqa: BLE001
                    self.error_occurred.emit(f"创建录制文件失败: {e}")
                    self._writer = None

            self.status_changed.emit(
                f"已连接 PCAN ({self._channel} @ {self._bitrate}bps)，监听全部报文"
            )
            start = time.time()
            while self._running:
                msg = bus.recv(timeout=0.05)
                if msg is None:
                    continue
                rel = msg.timestamp - start
                self.frame_received.emit(
                    rel, msg.arbitration_id, msg.dlc,
                    bytes(msg.data), msg.is_extended_id, msg.is_fd,
                )
                with self._writer_lock:
                    if self._writer is not None:
                        try:
                            self._writer.on_message_received(msg)
                        except Exception:  # noqa: BLE001
                            pass
        except Exception as e:  # noqa: BLE001
            self.error_occurred.emit(f"监听异常: {e}")
        finally:
            with self._writer_lock:
                if self._writer is not None:
                    try:
                        self._writer.stop()
                    except Exception:  # noqa: BLE001
                        pass
                    self._writer = None
            try:
                bus.shutdown()
            except Exception:  # noqa: BLE001
                pass
            self.status_changed.emit("监听已停止")

    def start_recording(self, path: str):
        """运行中开始录制到指定 BLF 文件（若已在录制则切换文件）"""
        with self._writer_lock:
            if self._writer is not None:
                try:
                    self._writer.stop()
                except Exception:  # noqa: BLE001
                    pass
            try:
                self._writer = can.BLFWriter(path)
            except Exception as e:  # noqa: BLE001
                self.error_occurred.emit(f"创建录制文件失败: {e}")

    def stop_recording(self):
        """停止录制并关闭 BLF 写入器"""
        with self._writer_lock:
            if self._writer is not None:
                try:
                    self._writer.stop()
                except Exception:  # noqa: BLE001
                    pass
                self._writer = None

    def stop(self):
        """请求停止监听"""
        self._running = False
