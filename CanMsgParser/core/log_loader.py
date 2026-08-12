"""BLF/ASC CAN 日志文件加载器"""
import os
from array import array

import numpy as np
import pandas as pd
import can

from core.byte_change import compute_byte_change_array


def load_log_file(file_path: str, progress_callback=None) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """加载 CAN 日志文件，返回 (frame_index, raw_data, byte_change)。

    - frame_index: DataFrame，列 [frame_id, timestamp, arbitration_id, dlc, channel, is_fd]
      - frame_id 即 raw_data 的行索引（全局，过滤后仍成立）
      - timestamp 已归一化到测量起点 t0（与信号曲线共用时间轴，对齐 TSMaster）
    - raw_data: (N, max_dlc) uint8 数组，raw_data[frame_id, :dlc] 为该帧原始字节
    - byte_change: (N, max_dlc) uint16 数组，逐帧逐字节"距上次变化帧数"（向量化预计算）

    内存策略（借鉴 CANoe/TSMaster 的紧凑二进制缓冲）：
    - 逐帧只向 array('d'/'I'/'B'/'i'/'b') 与 bytearray 追加原生类型，避免产生 N 个
      Python 对象（旧实现每帧 append bytes 对象，是加载期内存暴涨的根因之一）。
    - 循环结束后用 np.frombuffer 零拷贝转 numpy，再用 cumsum 偏移一次成型 raw_data。
    - byte_change 在加载线程内向量化计算，避免回到 UI 线程做 GB 级嵌套字典。
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".blf":
        reader = can.BLFReader(file_path)
    elif ext == ".asc":
        reader = can.ASCReader(file_path)
    else:
        raise ValueError(f"Unsupported file format: {ext}")

    timestamps = array("d")   # float64
    arb_ids = array("I")      # uint32
    dlcs = array("B")         # uint8
    channels = array("i")     # int32
    is_fds = array("b")       # int8 (0/1)
    data_ba = bytearray()     # 连续原始字节（长度 = 各帧 dlc 之和）

    file_size = os.path.getsize(file_path)
    last_progress = -1

    for msg in reader:
        timestamps.append(msg.timestamp)
        arb_ids.append(msg.arbitration_id)
        d = msg.dlc
        dlcs.append(d)
        channels.append(msg.channel if msg.channel is not None else 0)
        is_fd = msg.is_fd if hasattr(msg, "is_fd") else False
        is_fds.append(1 if is_fd else 0)

        data_bytes = bytes(msg.data)
        if len(data_bytes) < d:
            data_bytes = data_bytes + b"\x00" * (d - len(data_bytes))
        data_ba.extend(data_bytes[:d])

        if progress_callback and file_size > 0:
            current_pos = reader.file.tell() if hasattr(reader, "file") else 0
            progress = int(current_pos / file_size * 100)
            if progress != last_progress:
                last_progress = progress
                progress_callback(progress)

    num_frames = len(timestamps)
    if num_frames == 0:
        empty_df = pd.DataFrame(columns=[
            "frame_id", "timestamp", "arbitration_id", "dlc", "channel", "is_fd"
        ])
        return empty_df, np.empty((0, 8), dtype=np.uint8), np.empty((0, 8), dtype=np.uint16)

    # 统一时间原点：BLF 用测量开始时间(start_timestamp)，其它格式回落到首帧。
    # 这样消息表与信号曲线共用同一时间轴(t=0 为测量开始)，与市场工具(TSMaster 等)
    # 一致；且不同信号的时间原点相同，下发信号与上报信号的反馈时长可直接相减比较。
    start_ts = getattr(reader, "start_timestamp", None)
    raw_ts = np.frombuffer(timestamps, dtype=np.float64)
    first_ts = float(raw_ts[0]) if raw_ts.size else 0.0
    if start_ts is not None and start_ts > 0 and start_ts <= first_ts:
        t0 = float(start_ts)
    else:
        t0 = first_ts
    norm_ts = raw_ts - t0

    frame_index = pd.DataFrame({
        "frame_id": np.arange(num_frames, dtype=np.int64),
        "timestamp": norm_ts,
        "arbitration_id": np.frombuffer(arb_ids, dtype=np.uint32),
        "dlc": np.frombuffer(dlcs, dtype=np.uint8),
        "channel": np.frombuffer(channels, dtype=np.int32),
        "is_fd": np.frombuffer(is_fds, dtype=np.int8).astype(bool),
    })

    dlc_arr = frame_index["dlc"].to_numpy()
    max_dlc = int(dlc_arr.max()) if dlc_arr.size else 8
    raw_data = np.zeros((num_frames, max_dlc), dtype=np.uint8)
    if num_frames:
        # 用 cumsum 得到每帧在连续字节流中的起始偏移，再逐帧切片填入（无 N 个 Python 对象）。
        offsets = np.zeros(num_frames + 1, dtype=np.int64)
        np.cumsum(dlc_arr.astype(np.int64), out=offsets[1:])
        flat = np.frombuffer(data_ba, dtype=np.uint8)
        for i in range(num_frames):
            d = int(dlc_arr[i])
            if d:
                raw_data[i, :d] = flat[offsets[i]:offsets[i] + d]

    byte_change = compute_byte_change_array(frame_index, raw_data, max_dlc)

    if progress_callback:
        progress_callback(100)
    return frame_index, raw_data, byte_change
