# workers/can_raw_capture_worker.py
"""实时原始报文捕获处理器：接收总线上全部 CAN 帧并可选录制为 BLF / ASC。

设计要点（共享连接架构）：
- 不再自管 can.Bus，也不自起收帧线程。总线由 CanConnectionManager 在 GUI
  主线程保持唯一一条；所有帧由管理器唯一的收帧线程 fan-out 出来，本处理器
  作为监听者被回调 process_message(msg) 处理。
- 接收全部帧（不做信号筛选），通过 frame_received 信号回传每帧原始信息，
  供「实时报文页」以「同 ID 单行」方式展示。
- 支持运行中开始/停止录制：start_recording(path) 按扩展名创建写入器
  （.asc → CANoe ASCII，其余 → BLF），每收到一帧即写入；stop_recording()
  关闭写入器。录制写入由锁保护。
"""
import os
import threading
import time

from PyQt5.QtCore import QObject, pyqtSignal

import can


def create_record_writer(path: str):
    """按扩展名创建录制写入器：.asc → CANoe ASCII 文本；其余 → BLF 二进制。

    - BLF：Vector 二进制格式，体积小、写入快，适合长时间 / 大数据量录制。
    - ASC：CANoe ASCII 文本格式，纯文本可读，便于文本工具二次处理与人工查看；
      但同内容体积明显大于 BLF，高频长时间录制请优先用 BLF。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".asc":
        return can.ASCWriter(path)
    return can.BLFWriter(path)


class CanRawCaptureWorker(QObject):
    """实时接收全部 CAN 帧并可选录制的处理器（由连接管理器驱动）"""

    # (相对时间秒, 报文ID, DLC, 数据字节, 是否扩展帧, 是否FD)
    frame_received = pyqtSignal(float, int, int, bytes, bool, bool)
    status_changed = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, interface_type: str = "peak",
                 channel: str = "PCAN_USBBUS1", bitrate: int = 500000,
                 record_path: str | None = None):
        super().__init__()
        self._interface_type = interface_type
        self._channel = channel
        self._bitrate = bitrate
        self._running = False
        self._start_ts = 0.0
        self._writer = None
        self._writer_lock = threading.Lock()
        # 构造时传入的录制路径：启动时即开始录制。
        # ⚠️ 该标志必须【先无条件初始化】：创建成功时若只在 except 分支赋值，
        # start_monitoring() 里读取它会 AttributeError（构造即录制的用法会直接崩）。
        self._init_record_error = None
        if record_path:
            try:
                self._writer = create_record_writer(record_path)
            except Exception as e:  # noqa: BLE001
                self._init_record_error = f"创建录制文件失败: {e}"

    def start_monitoring(self, record_path: str | None = None) -> bool:
        """由控件在 ensure_connected 之后调用：就绪并返回是否成功。"""
        self._running = True
        self._start_ts = time.time()
        # 启动时若同时指定了录制路径，立即开始
        if record_path is not None:
            self.start_recording(record_path)
        elif self._init_record_error is not None:
            self.error_occurred.emit(self._init_record_error)
        self.status_changed.emit(
            f"监听中（{self._interface_type} / {self._channel} @ {self._bitrate}）"
        )
        return True

    def process_message(self, msg):
        """由连接管理器收帧线程在收到每帧时回调：广播原始帧并写入 BLF。"""
        if not self._running:
            return
        rel = msg.timestamp - self._start_ts
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

    def start_recording(self, path: str):
        """运行中开始录制到指定文件（按扩展名决定 BLF / ASC；若已在录制则切换文件）"""
        with self._writer_lock:
            if self._writer is not None:
                try:
                    self._writer.stop()
                except Exception:  # noqa: BLE001
                    pass
            try:
                self._writer = create_record_writer(path)
            except Exception as e:  # noqa: BLE001
                self.error_occurred.emit(f"创建录制文件失败: {e}")

    def stop_recording(self):
        """停止录制并关闭录制写入器（BLF/ASC 均以 stop() 收尾：ASC 会补写 End TriggerBlock）"""
        with self._writer_lock:
            if self._writer is not None:
                try:
                    self._writer.stop()
                except Exception:  # noqa: BLE001
                    pass
                self._writer = None

    def stop(self):
        """请求停止监听（停止录制并置标志）。"""
        self.stop_recording()
        self._running = False
