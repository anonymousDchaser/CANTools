"""DecodeWorker 端到端冒烟：用 test.dbc 的 EngineRPM + 生成的 0x1A0 日志，
直接调用 run()，验证向量化解码接线正确（输出长度 / 与逐帧 cantools 一致）。"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PyQt5.QtWidgets import QApplication
import numpy as np
import cantools
from core.log_loader import load_log_file
from core.signal_cache import SignalCache
from workers.load_worker import DecodeWorker
from core.can_utils import load_dbc_database

N = 5000
FPS = 50.0

def gen_asc(path, dbc):
    # 用 dbc 中第一条报文的 frame_id 生成日志
    msg = dbc.messages[0]
    fid = msg.frame_id
    rng = np.random.default_rng(3)
    lines = ["base hex  timestamps absolute", ""]
    t = 0.0
    for i in range(N):
        dlc = len(msg.signals) and 8
        data = rng.integers(0, 256, size=8).tolist()
        hexs = " ".join(f"{b:02X}" for b in data)
        lines.append(f"   {t:.6f} 1  {fid:03X}             Rx   d 8 {hexs}")
        t += 1.0 / FPS
    open(path, "w").write("\n".join(lines))

def main():
    app = QApplication(sys.argv)
    dbc_path = os.path.join(os.path.dirname(__file__), "fixtures", "test.dbc")
    db = load_dbc_database(dbc_path)
    msg = db.messages[0]
    sig_name = msg.signals[0].name
    msg_name = msg.name

    tmp = tempfile.mktemp(suffix=".asc")
    gen_asc(tmp, db)
    fi, rd, bc = load_log_file(tmp)

    captured = []
    w = DecodeWorker(dbc_path, msg_name, sig_name, fi, rd, SignalCache())
    w.finished.connect(lambda ds: captured.append(ds))
    w.run()  # 直接调用 run() 完成解码（不进线程）

    assert captured, "DecodeWorker 未 emit finished"
    ds = captured[0]
    print(f"msg={msg_name} sig={sig_name}")
    print(f"  输出点数={len(ds.values)} 时间戳点数={len(ds.timestamps)}")
    assert len(ds.values) == len(ds.timestamps) == N, f"长度不一致: {len(ds.values)}/{len(ds.timestamps)}/{N}"

    # 与逐帧 cantools 解码对比（抽 50 点）
    raw_mat = rd[fi["frame_id"].to_numpy()]
    ref = np.array([msg.decode(bytes(raw_mat[r]), decode_choices=False, scaling=True)[sig_name]
                    for r in range(N)], dtype=np.float64)
    err = np.max(np.abs(ds.values - ref))
    print(f"  与逐帧 cantools 最大误差={err}")
    assert err < 1e-6, f"Worker 解码与 cantools 不一致: {err}"
    os.remove(tmp)
    print("DECODE_WORKER_SMOKE_OK")

def fid_mask(fi, fid):
    return fi["arbitration_id"] == fid

if __name__ == "__main__":
    main()
