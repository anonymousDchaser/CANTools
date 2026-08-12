# workers/load_worker.py
"""QThread 工作线程：文件加载和信号解码"""
import cantools
from core.can_utils import load_dbc_database
import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal
from core.can_data import MessageDef, DecodedSignal
from core.log_loader import load_log_file
from core.signal_cache import SignalCache
from core.signal_decode import decode_signal_matrix


class LoadWorker(QThread):
    """后台线程：加载日志文件并建立索引"""
    progress = pyqtSignal(int)
    finished = pyqtSignal(object, object, object)  # (frame_index, raw_data, byte_change)
    error = pyqtSignal(str)

    def __init__(self, file_path: str, parent=None):
        super().__init__(parent)
        self._file_path = file_path
        self._cancelled = False

    def run(self):
        try:
            frame_index, raw_data, byte_change = load_log_file(self._file_path, progress_callback=self._on_progress)
            if not self._cancelled:
                self.finished.emit(frame_index, raw_data, byte_change)
        except Exception as e:
            if not self._cancelled:
                self.error.emit(str(e))

    def _on_progress(self, percent: int):
        if not self._cancelled:
            self.progress.emit(percent)

    def cancel(self):
        self._cancelled = True


class DecodeWorker(QThread):
    """后台线程：按需解码信号"""
    progress = pyqtSignal(int)
    finished = pyqtSignal(object)  # DecodedSignal
    error = pyqtSignal(str)

    def __init__(self, dbc_path: str, msg_name: str, sig_name: str,
                 frame_index, raw_data: np.ndarray, cache: SignalCache, parent=None):
        super().__init__(parent)
        self._dbc_path = dbc_path
        self._msg_name = msg_name
        self._sig_name = sig_name
        self._frame_index = frame_index
        self._raw_data = raw_data
        self._cache = cache
        self._cancelled = False

    def run(self):
        try:
            cached = self._cache.get(self._msg_name, self._sig_name)
            if cached is not None:
                if not self._cancelled:
                    ds = DecodedSignal(msg_name=self._msg_name, sig_name=self._sig_name,
                                       timestamps=cached[0], values=cached[1])
                    self.finished.emit(ds)
                return

            db = load_dbc_database(self._dbc_path)
            msg_def = db.get_message_by_name(self._msg_name)
            mask = self._frame_index["arbitration_id"] == msg_def.frame_id
            matched = self._frame_index[mask]

            if len(matched) == 0:
                if not self._cancelled:
                    ds = DecodedSignal(msg_name=self._msg_name, sig_name=self._sig_name,
                                       timestamps=np.array([], dtype=np.float64),
                                       values=np.array([], dtype=np.float64))
                    self.finished.emit(ds)
                return

            # 向量化解码：一次性从原始字节矩阵解出全部匹配帧的目标信号，
            # 取代原 iterrows + 逐帧 msg_def.decode（20 万帧从 ~10s 降到 ~0.1s）。
            fids = matched["frame_id"].to_numpy()
            raw_mat = self._raw_data[fids]          # (M, 8) uint8，numpy 花式索引
            sig = msg_def.get_signal_by_name(self._sig_name)
            values = decode_signal_matrix(raw_mat, sig)
            timestamps = matched["timestamp"].to_numpy().astype(np.float64)
            # 注意：时间戳已在 log_loader 中统一归一到测量起点(t0)，
            # 此处【不再】按本信号首帧二次归零，否则不同信号时间原点不一致，
            # 会导致下发/上报信号反馈时长计算错误、与 TSMaster 等工具对不上。

            self._cache.put(self._msg_name, self._sig_name, timestamps, values)

            if not self._cancelled:
                ds = DecodedSignal(msg_name=self._msg_name, sig_name=self._sig_name,
                                   timestamps=timestamps, values=values)
                self.finished.emit(ds)
                self.progress.emit(100)

        except Exception as e:
            if not self._cancelled:
                self.error.emit(str(e))

    def cancel(self):
        self._cancelled = True
