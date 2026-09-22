"""BLF/ASC CAN 日志文件加载器"""
import os
import re
from array import array

import numpy as np
import pandas as pd
import can
from can.util import dlc2len

from core.byte_change import compute_byte_change_array


# ═══════════════════════ ASC(.asc) 解析器 ═══════════════════════
#
# 为什么不用 python-can 的 can.ASCReader：
#  1) ASCReader 以【文本模式】打开文件，其 _extract_header() / __iter__ 用
#     `for line in self.file` 迭代；Python 文本文件对象一旦经 next() 迭代过，
#     tell() 即被禁用，本加载器取读取位置做进度时抛
#     OSError: telling position disabled by next() call
#     —— GUI 加载 .asc 会直接失败（BLF 不受影响：BLFReader 是二进制打开）。
#  2) ASCReader._extract_header() 读到第一条非头部行时 break，该行被消费后丢弃，
#     导致文件首帧丢失（实测样例 133898 行只解析出 133897 帧），并把测量时间
#     原点右移到第二帧，与 CANoe 显示的时间轴对不上。
#
# 本实现按【二进制】逐行读取（readline，tell() 始终可用），单遍扫描：
# 头部行就地更新解析状态、数据行就地产出，不做「先读头部、再进数据」的两段式，
# 因而既不丢首帧也不受 tell() 限制。未识别行（注释 / 统计 / J1939 等）直接跳过，
# 与 ASCReader 的容错行为一致。
# 支持：经典数据帧、远程帧、扩展帧( ID 后缀 x )、CAN FD 帧；dec/hex 两种进制。

_BASE_BY_NAME = {"hex": 16, "dec": 10}
_DATA_LINE_RE = re.compile(r"^([\d.]+)\s+(\S+)\s+(.*)$")


class AscMessageReader:
    """CANoe ASCII(.asc) 报文读取器：二进制逐行解析，tell() 安全且不丢首帧。"""

    def __init__(self, file_path: str):
        self._fh = open(file_path, "rb")
        # 默认 hex，可被文件头 `base hex|dec [timestamps absolute|relative]` 覆盖
        self._base = 16

    # ── 与 python-can Reader 对齐的最小接口 ──

    @property
    def file(self):
        """底层文件对象（二进制，tell() 在迭代中依然可用）。"""
        return self._fh

    def read_position(self) -> int:
        """已读取的字节位置，供外部画进度条。"""
        try:
            return self._fh.tell()
        except (OSError, ValueError):
            return 0

    def close(self):
        try:
            self._fh.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __iter__(self):
        try:
            while True:
                raw = self._fh.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                msg = self._parse_line(line)
                if msg is not None:
                    yield msg
        finally:
            self.close()

    # ── 行解析 ──

    def _parse_line(self, line: str):
        low = line.lower()
        # 头部 / 事件行：就地更新状态后继续（不做两段式读取，因此不会吞掉首帧）
        if low.startswith("base "):
            self._update_base(line)
            return None
        if (low.startswith("date ") or low.startswith("//")
                or "triggerblock" in low or "internal events" in low):
            return None

        m = _DATA_LINE_RE.match(line)
        if m is None:
            return None
        try:
            ts = float(m.group(1))
        except ValueError:
            return None
        channel_str, rest = m.group(2), m.group(3)

        if channel_str.upper() == "CANFD":
            return self._parse_fd(rest, ts)
        if not channel_str.isdigit():
            # `0.000000 Start of measurement` 等事件行、statistic、J1939TP
            return None
        return self._parse_classic(rest, ts, int(channel_str) - 1)

    def _update_base(self, line: str):
        toks = line.split()
        if len(toks) >= 2 and toks[1].lower() in _BASE_BY_NAME:
            self._base = _BASE_BY_NAME[toks[1].lower()]

    def _parse_classic(self, rest: str, ts: float, channel: int):
        """经典帧：`<id>[x] Rx|Tx d <dlc> <data...>` 或远程帧 `... r [dlc]`。"""
        if rest[:10].lower() == "errorframe":
            return None                      # 错误帧不参与信号分析
        toks = rest.split(None, 2)
        if len(toks) < 2:
            return None
        id_str, direction = toks[0], toks[1]
        payload = toks[2] if len(toks) > 2 else ""

        is_ext = id_str[-1:].lower() == "x"  # 末尾 x 表示扩展帧
        try:
            arb = int(id_str[:-1] if is_ext else id_str, self._base)
        except ValueError:
            return None
        is_rx = direction == "Rx"

        if payload[:1].lower() == "r":
            # 远程帧
            parts = payload.split()
            dlc = 0
            if len(parts) > 1:
                try:
                    dlc = int(parts[1], self._base)
                except ValueError:
                    dlc = 0
            return can.Message(timestamp=ts, arbitration_id=arb,
                               is_extended_id=is_ext, channel=channel,
                               is_rx=is_rx, is_remote_frame=True, dlc=dlc)

        parts = payload.split(None, 2)
        if len(parts) < 2:
            return None
        try:
            dlc = dlc2len(int(parts[1], self._base))
        except (ValueError, KeyError):
            return None

        data = b""
        if len(parts) > 2:
            buf = bytearray()
            for tok in parts[2].split()[:min(8, dlc)]:
                try:
                    buf.append(int(tok, self._base))
                except ValueError:
                    break
            data = bytes(buf)
        return can.Message(timestamp=ts, arbitration_id=arb,
                           is_extended_id=is_ext, channel=channel,
                           is_rx=is_rx, dlc=dlc, data=data)

    def _parse_fd(self, rest: str, ts: float):
        """CAN FD 帧：`<ch> Rx|Tx <id> [name] <brs> <esi> <dlc> <len> <data...>`。"""
        toks = rest.split(None, 2)
        if len(toks) < 3:
            return None
        ch_str, direction = toks[0], toks[1]
        if not ch_str.isdigit():
            return None
        tail = toks[2]
        if tail[:10].lower() == "errorframe":
            return None

        parts = tail.split(None, 2)
        if len(parts) < 2:
            return None
        id_str, bridge = parts[0], parts[1]
        body = parts[2] if len(parts) > 2 else ""
        if bridge.isdigit():
            brs = bridge
            fields = body.split(None, 3)
        else:
            # ID 后插了报文名列（非数字），BRS 顺延
            fields = (bridge + " " + body).split(None, 4)
        if len(fields) < 3:
            return None
        esi, dlc_str, length_str = fields[0], fields[1], fields[2]
        data_str = fields[3] if len(fields) > 3 else ""

        is_ext = id_str[-1:].lower() == "x"
        try:
            arb = int(id_str[:-1] if is_ext else id_str, self._base)
            dlc = int(dlc_str, self._base)
            data_len = int(length_str)
        except ValueError:
            return None

        kwargs = dict(timestamp=ts, arbitration_id=arb, is_extended_id=is_ext,
                      is_fd=True, channel=int(ch_str) - 1,
                      is_rx=direction == "Rx", bitrate_switch=(brs == "1"),
                      error_state_indicator=(esi == "1"))
        if data_len == 0:
            kwargs["is_remote_frame"] = True
            kwargs["dlc"] = dlc
            return can.Message(**kwargs)

        kwargs["dlc"] = data_len
        buf = bytearray()
        for tok in data_str.split()[:data_len]:
            try:
                buf.append(int(tok, self._base))
            except ValueError:
                break
        kwargs["data"] = bytes(buf)
        return can.Message(**kwargs)


def _read_position(reader) -> int:
    """取 reader 已读取的字节位置（供进度条）。

    ⚠️ 不可直接调 reader.file.tell()：python-can 的 ASCReader 是文本模式打开，
    文本文件对象被 next() 迭代过后 tell() 会被禁用并抛
    OSError: telling position disabled by next() call。
    这里优先用 reader 自报的位置，其次退回二进制文件对象的 tell()，
    任何异常都回落到 0（仅影响进度显示，不影响加载）。
    """
    getter = getattr(reader, "read_position", None)
    if callable(getter):
        try:
            return int(getter())
        except Exception:  # noqa: BLE001
            return 0
    fh = getattr(reader, "file", None)
    if fh is None:
        return 0
    try:
        return int(fh.tell())
    except Exception:  # noqa: BLE001
        return 0


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
        # 不用 can.ASCReader：文本模式迭代后 tell() 被禁用（进度回调抛
        # OSError: telling position disabled by next() call），且其头部解析
        # 会吞掉文件首帧。详见 AscMessageReader 顶部说明。
        reader = AscMessageReader(file_path)
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
            current_pos = _read_position(reader)
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
