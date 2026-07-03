# utils/export_utils.py
"""导出工具：图表图片、信号数据 CSV/Excel、报文表格 CSV"""
import os
import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from core.can_data import DecodedSignal


def export_chart_image(fig: Figure, file_path: str, dpi: int = 300):
    """导出 matplotlib 图表为图片"""
    fig.savefig(file_path, dpi=dpi, bbox_inches="tight")


def export_signal_data(signals: list[DecodedSignal], file_path: str):
    """导出信号数据为 CSV 或 Excel。多信号按 timestamp outer join。"""
    if not signals:
        return
    all_timestamps = np.unique(np.concatenate([s.timestamps for s in signals]))
    all_timestamps.sort()
    data = {"timestamp": all_timestamps}
    for sig in signals:
        col_name = f"{sig.msg_name}.{sig.sig_name}"
        indices = np.searchsorted(sig.timestamps, all_timestamps)
        values = np.full(len(all_timestamps), np.nan)
        valid = indices < len(sig.timestamps)
        exact_match = valid & (sig.timestamps[np.clip(indices, 0, len(sig.timestamps) - 1)] == all_timestamps)
        values[exact_match] = sig.values[indices[exact_match]]
        data[col_name] = values
    df = pd.DataFrame(data)
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".csv":
        df.to_csv(file_path, index=False)
    elif ext in (".xlsx", ".xls"):
        df.to_excel(file_path, index=False)
    else:
        df.to_csv(file_path, index=False)


def export_message_table(frame_index: pd.DataFrame, raw_data: np.ndarray,
                         file_path: str, decoded_signals: dict = None):
    """导出报文表格为 CSV"""
    rows = []
    for _, row in frame_index.iterrows():
        fid = row["frame_id"]
        dlc = row["dlc"]
        hex_data = " ".join(f"{b:02X}" for b in raw_data[fid, :dlc])
        rows.append({
            "frame_id": fid,
            "timestamp": row["timestamp"],
            "arbitration_id": f"0x{row['arbitration_id']:03X}",
            "dlc": dlc,
            "channel": row["channel"],
            "data": hex_data,
        })
    df = pd.DataFrame(rows)
    df.to_csv(file_path, index=False)
