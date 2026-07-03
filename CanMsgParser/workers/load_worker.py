# workers/load_worker.py
"""QThread 工作线程：文件加载和信号解码"""
import cantools
import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal
from core.can_data import MessageDef, DecodedSignal
from core.log_loader import load_log_file
from core.signal_cache import SignalCache


class LoadWorker(QThread):
    """后台线程：加载日志文件并建立索引"""
    progress = pyqtSignal(int)
    finished = pyqtSignal(object, object)  # (frame_index, raw_data)
    error = pyqtSignal(str)

    def __init__(self, file_path: str, parent=None):
        super().__init__(parent)
        self._file_path = file_path
        self._cancelled = False

    def run(self):
        try:
            frame_index, raw_data = load_log_file(self._file_path, progress_callback=self._on_progress)
            if not self._cancelled:
                self.finished.emit(frame_index, raw_data)
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

            db = cantools.database.load_file(self._dbc_path)
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

            timestamps = []
            values = []
            total = len(matched)
            last_progress = -1

            for idx, (_, row) in enumerate(matched.iterrows()):
                if self._cancelled:
                    return
                fid = row["frame_id"]
                frame_data = bytes(self._raw_data[fid, :row["dlc"]])
                try:
                    decoded = msg_def.decode(frame_data)
                    if self._sig_name in decoded:
                        timestamps.append(row["timestamp"])
                        values.append(float(decoded[self._sig_name]))
                except Exception:
                    pass

                progress = int(idx / total * 100)
                if progress != last_progress:
                    last_progress = progress
                    self.progress.emit(progress)

            ts_arr = np.array(timestamps, dtype=np.float64)
            val_arr = np.array(values, dtype=np.float64)
            if len(ts_arr) > 0:
                ts_arr -= ts_arr[0]

            self._cache.put(self._msg_name, self._sig_name, ts_arr, val_arr)

            if not self._cancelled:
                ds = DecodedSignal(msg_name=self._msg_name, sig_name=self._sig_name,
                                   timestamps=ts_arr, values=val_arr)
                self.finished.emit(ds)
                self.progress.emit(100)

        except Exception as e:
            if not self._cancelled:
                self.error.emit(str(e))

    def cancel(self):
        self._cancelled = True
