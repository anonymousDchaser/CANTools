# core/can_data.py
"""CAN 报文分析工具 — 统一数据模型"""
from dataclasses import dataclass, field
import numpy as np


@dataclass
class SignalDef:
    """DBC 中的信号定义"""
    name: str
    start_bit: int
    length: int
    byte_order: str          # 'intel' | 'motorola'
    scale: float
    offset: float
    unit: str
    min_val: float
    max_val: float
    choices: dict = field(default_factory=dict)  # 值描述 {0: "OFF", 1: "ON"}


@dataclass
class MessageDef:
    """DBC 中的报文定义"""
    frame_id: int
    name: str
    dlc: int
    is_fd: bool
    signals: list = field(default_factory=list)  # list[SignalDef]


@dataclass
class DecodedSignal:
    """解码后的信号数据"""
    msg_name: str
    sig_name: str
    timestamps: np.ndarray   # float64, 秒
    values: np.ndarray       # float64, 物理值