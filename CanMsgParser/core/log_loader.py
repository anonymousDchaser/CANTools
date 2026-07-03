"""BLF/ASC CAN 日志文件加载器"""
import os
import numpy as np
import pandas as pd
import can


def load_log_file(file_path: str, progress_callback=None) -> tuple[pd.DataFrame, np.ndarray]:
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".blf":
        reader = can.BLFReader(file_path)
    elif ext == ".asc":
        reader = can.ASCReader(file_path)
    else:
        raise ValueError(f"Unsupported file format: {ext}")

    timestamps = []
    arb_ids = []
    dlcs = []
    channels = []
    is_fds = []
    data_list = []
    file_size = os.path.getsize(file_path)
    last_progress = -1

    for i, msg in enumerate(reader):
        timestamps.append(msg.timestamp)
        arb_ids.append(msg.arbitration_id)
        dlcs.append(msg.dlc)
        channels.append(msg.channel if msg.channel is not None else 0)
        is_fds.append(msg.is_fd if hasattr(msg, "is_fd") else False)
        data_bytes = bytes(msg.data)
        if len(data_bytes) < msg.dlc:
            data_bytes = data_bytes + b"\x00" * (msg.dlc - len(data_bytes))
        data_list.append(data_bytes[:msg.dlc])
        if progress_callback and file_size > 0:
            current_pos = reader.file.tell() if hasattr(reader, "file") else 0
            progress = int(current_pos / file_size * 100)
            if progress != last_progress:
                last_progress = progress
                progress_callback(progress)

    if not timestamps:
        empty_df = pd.DataFrame(columns=["frame_id", "timestamp", "arbitration_id", "dlc", "channel", "is_fd"])
        return empty_df, np.empty((0, 8), dtype=np.uint8)

    num_frames = len(timestamps)
    frame_index = pd.DataFrame({
        "frame_id": np.arange(num_frames, dtype=np.int64),
        "timestamp": np.array(timestamps, dtype=np.float64),
        "arbitration_id": np.array(arb_ids, dtype=np.uint32),
        "dlc": np.array(dlcs, dtype=np.uint8),
        "channel": np.array(channels, dtype=np.int32),
        "is_fd": np.array(is_fds, dtype=bool),
    })

    max_dlc = max(dlcs) if dlcs else 8
    raw_data = np.zeros((num_frames, max_dlc), dtype=np.uint8)
    for i, data_bytes in enumerate(data_list):
        raw_data[i, :len(data_bytes)] = np.frombuffer(data_bytes, dtype=np.uint8)

    if progress_callback:
        progress_callback(100)
    return frame_index, raw_data
