"""DBC 文件解析封装，基于 cantools 库"""
import cantools
from core.can_data import SignalDef, MessageDef


def parse_dbc(file_path: str) -> list[MessageDef]:
    db = cantools.database.load_file(file_path)
    messages = []
    for msg in db.messages:
        signals = []
        for sig in msg.signals:
            byte_order = "intel" if sig.byte_order == "little_endian" else "motorola"
            # 提取 cantools 信号值描述
            choices = {}
            if hasattr(sig, 'choices') and sig.choices:
                choices = dict(sig.choices)
            signals.append(SignalDef(
                name=sig.name,
                start_bit=sig.start,
                length=sig.length,
                byte_order=byte_order,
                scale=sig.scale,
                offset=sig.offset,
                unit=sig.unit or "",
                min_val=sig.minimum if sig.minimum is not None else 0.0,
                max_val=sig.maximum if sig.maximum is not None else 0.0,
                choices=choices,
            ))
        messages.append(MessageDef(
            frame_id=msg.frame_id,
            name=msg.name,
            dlc=msg.length,
            is_fd=msg.is_fd if hasattr(msg, "is_fd") else False,
            signals=signals,
        ))
    return messages
