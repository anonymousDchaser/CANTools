# core/can_utils.py
"""PCAN 总线连接与 DBC 编解码共享工具

集中封装 python-can 的 PCAN 硬件连接以及 cantools 的信号编解码逻辑，
供「信号实时监控」与「信号模拟上报」两个后台 worker 复用，避免重复代码，
也便于统一排查硬件/编码问题。

线程安全说明：
- 每个 worker 在自己的 QThread 中创建并使用独立的 can.Bus 实例，
  不在多个线程间共享同一个总线对象，因此不存在跨线程持锁调用的风险。
- 本模块的函数本身不持有全局锁，纯函数式封装。
"""
import os
from dataclasses import dataclass, field
from typing import Optional

import can
import cantools


# 默认 PCAN-USB 通道与波特率（与参考项目 VehicleTMasterProj 一致）
DEFAULT_CHANNEL = "PCAN_USBBUS1"
DEFAULT_BITRATE = 500000


@dataclass
class SignalSlot:
    """一个被选中信号的定位信息"""
    msg_name: str
    sig_name: str
    frame_id: int
    unit: str = ""


@dataclass
class FrameEncodeGroup:
    """按报文(frame_id)聚合的一组信号，用于一次编码发送"""
    frame_id: int
    msg_name: str
    signals: list = field(default_factory=list)  # list[SignalSlot]


def connect_bus(channel: str = DEFAULT_CHANNEL, bitrate: int = DEFAULT_BITRATE):
    """连接 PCAN 硬件总线。

    Returns:
        (bus, None) 成功； (None, error_msg) 失败。
    """
    try:
        bus = can.Bus(interface="pcan", channel=channel, bitrate=bitrate)
        return bus, None
    except Exception as e:  # noqa: BLE001 — 硬件连接失败需向上层返回可读错误
        return None, f"PCAN 连接失败: {e}"


# 替换符（U+FFFD）：cantools 以 errors='replace' 打开 DBC，编码不匹配时
# 非法字节会变成该字符，可据此判断所选编码是否正确。
_REPLACEMENT = "\ufffd"


def _db_decode_has_replacement(db) -> bool:
    """解码后是否出现替换符，证明编码不匹配"""
    for m in db.messages:
        if _REPLACEMENT in m.name:
            return True
        for s in m.signals:
            if _REPLACEMENT in (s.unit or "") or _REPLACEMENT in s.name:
                return True
    return False


def _db_has_cjk(db) -> bool:
    """解码结果是否含中文(CJK)，用于判断 GBK 是否更合适"""
    for m in db.messages:
        if any("\u4e00" <= c <= "\u9fff" for c in m.name):
            return True
        for s in m.signals:
            if (s.unit and any("\u4e00" <= c <= "\u9fff" for c in s.unit)) \
                    or any("\u4e00" <= c <= "\u9fff" for c in s.name):
                return True
    return False


def load_dbc_database(dbc_path: str):
    """以对中文矩阵最友好的编码加载 DBC 数据库。

    依次尝试 gbk / cp1252 / utf-8：优先选择解码后含有中文(CJK)且无替换符
    的结果（中文 CAN 矩阵通常为 GBK 编码，cantools 默认的 cp1252 会把
    「°」「分」等解码成乱码）；无中文的欧标 DBC 则回退 cp1252/utf-8。
    全部编码尝试失败时再回退到 cantools 默认编码(cp1252)。
    """
    for enc in ("gbk", "cp1252", "utf-8"):
        try:
            db = cantools.database.load_file(dbc_path, encoding=enc)
        except Exception as e:  # noqa: BLE001
            continue
        if _db_decode_has_replacement(db):
            continue
        if enc == "gbk" and not _db_has_cjk(db):
            # 无中文的 DBC 用 GBK 可能误伤（如欧标重音字母），回退 cp1252
            continue
        return db
    # 兜底：默认编码（cp1252）
    return cantools.database.load_file(dbc_path)


def load_dbc(dbc_path: str):
    """加载 DBC 数据库文件，返回 cantools database 对象。

    Returns:
        (db, None) 成功； (None, error_msg) 失败。
    """
    if not os.path.exists(dbc_path):
        return None, f"DBC 文件不存在: {dbc_path}"
    try:
        db = load_dbc_database(dbc_path)
        return db, None
    except Exception as e:  # noqa: BLE001
        return None, f"DBC 加载失败: {e}"


def build_signal_slots(db, signals: list) -> tuple[list, Optional[str]]:
    """根据选中的 (msg_name, sig_name) 列表，在 DBC 中定位每个信号的 frame_id。

    Args:
        db: cantools database 对象
        signals: [(msg_name, sig_name), ...]

    Returns:
        (slots, None) 成功，slots 为 SignalSlot 列表（已过滤掉 DBC 中找不到的信号）；
        (None, error_msg) 若某个选中信号在 DBC 中完全找不到。
    """
    # 建立 signal_name -> 候选 message 列表 的索引（一个信号名可能出现在多个报文）
    name_index: dict = {}
    for msg in db.messages:
        for sig in msg.signals:
            name_index.setdefault(sig.name, []).append(msg)

    slots: list = []
    missing = []
    for msg_name, sig_name in signals:
        msg_def = None
        # 优先按 (msg_name, sig_name) 精确匹配
        try:
            msg_def = db.get_message_by_name(msg_name)
            if sig_name not in [s.name for s in msg_def.signals]:
                msg_def = None
        except (KeyError, AttributeError):
            msg_def = None

        if msg_def is None:
            # 退而求其次：仅按信号名在所有报文中查找（用于容错）
            candidates = name_index.get(sig_name, [])
            if candidates:
                msg_def = candidates[0]
            else:
                missing.append(f"{msg_name}.{sig_name}")
                continue

        unit = ""
        for sig in msg_def.signals:
            if sig.name == sig_name:
                unit = sig.unit or ""
                break
        slots.append(SignalSlot(
            msg_name=msg_def.name,
            sig_name=sig_name,
            frame_id=msg_def.frame_id,
            unit=unit,
        ))

    if missing:
        return None, f"以下信号在 DBC 中未找到: {', '.join(missing)}"
    return slots, None


def group_by_frame(slots: list) -> list:
    """将 SignalSlot 列表按 frame_id 聚合为 FrameEncodeGroup。"""
    groups: dict = {}
    for slot in slots:
        g = groups.get(slot.frame_id)
        if g is None:
            g = FrameEncodeGroup(frame_id=slot.frame_id, msg_name=slot.msg_name)
            groups[slot.frame_id] = g
        g.signals.append(slot)
    return list(groups.values())


def encode_frame(db, group: FrameEncodeGroup, values: dict) -> tuple[bytes, Optional[str]]:
    """将一个报文内的多个信号值编码为 CAN 数据字节。

    Args:
        db: cantools database
        group: FrameEncodeGroup
        values: {sig_name: 物理值} 只需包含要发送的信号，其余用 DBC 默认值填充

    Returns:
        (data_bytes, None) 成功； (b'', error_msg) 失败。
    """
    try:
        msg_def = db.get_message_by_frame_id(group.frame_id)
        # 用 DBC 中每个信号的 offset 作为默认值，保证未指定的信号也有合法值
        all_signals = {s.name: (s.offset if s.offset else 0) for s in msg_def.signals}
        all_signals.update(values)
        data = msg_def.encode(all_signals, scaling=True, strict=False)
        return data, None
    except Exception as e:  # noqa: BLE001
        return b"", f"报文 {group.msg_name} 编码失败: {e}"


def decode_frame(db, frame_id: int, data: bytes) -> dict:
    """解码一帧 CAN 数据为 {sig_name: 物理值}。失败时返回空字典。"""
    try:
        msg_def = db.get_message_by_frame_id(frame_id)
        return dict(msg_def.decode(data))
    except Exception:  # noqa: BLE001
        return {}
