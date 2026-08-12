"""无头基准：实测报文表 set_data / 首绘 / fetchMore / data() 耗时，定位切页与展开卡顿。
用法：QT_QPA_PLATFORM=offscreen python tests/bench_message_table.py
"""
import os, sys, time, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
import numpy as np
import pandas as pd

from core.log_loader import load_log_file
from widgets.message_table import MessageTableWidget, MessageTableModel

N = 200_000
FPS = 50.0


def gen_asc(path):
    lines = ["version 8.0.0", "base hex  timestamps absolute", ""]
    t = 0.0
    ids = [0x1A0, 0x1A1, 0x1A2, 0x100, 0x200]
    rng = np.random.default_rng(0)
    for i in range(N):
        arb = ids[i % len(ids)]
        dlc = int(rng.integers(1, 9))
        data = rng.integers(0, 256, size=dlc).tolist()
        hexs = " ".join(f"{b:02X}" for b in data)
        lines.append(f"   {t:.6f} 1  {arb:03X}             Rx   d {dlc} {hexs}")
        t += 1.0 / FPS
    with open(path, "w") as f:
        f.write("\n".join(lines))
    return path


def main():
    app = QApplication(sys.argv)
    tmp = tempfile.mktemp(suffix=".asc")
    print(f"生成 {N} 帧 ASC ...")
    t0 = time.time()
    gen_asc(tmp)
    print(f"  生成耗时 {time.time()-t0:.2f}s")

    t0 = time.time()
    frame_index, raw_data, byte_change = load_log_file(tmp)
    print(f"load_log_file 耗时 {time.time()-t0:.2f}s  shape={raw_data.shape}")

    w = MessageTableWidget()
    model = w._model

    # 1) set_data
    t0 = time.time()
    w.set_data(frame_index, raw_data, [], dbc_path="", byte_change=byte_change)
    print(f"[1] set_data 耗时 {time.time()-t0:.4f}s  rowCount={model.rowCount()}")

    # 2) 首绘（模拟切到该页：show + processEvents + 强制 viewport repaint）
    t0 = time.time()
    w.show()
    QApplication.processEvents()
    w._tree.viewport().repaint()
    QApplication.processEvents()
    print(f"[2] 首绘(show+repaint) 耗时 {time.time()-t0:.4f}s  _loaded={model._loaded}/{model._total}")

    # 3) data() 对可见区 30 行 × 6 列
    t0 = time.time()
    for r in range(30):
        idx = model.index(r, 0)
        for c in range(6):
            model.data(model.index(r, c))
    print(f"[3] 可见区 30×6 data() 耗时 {time.time()-t0:.4f}s")

    # 4) fetchMore 展开第 0 行（需 DBC 才解码；这里无 DBC 应走 decode_err 分支）
    pidx = model.index(0, 0)
    t0 = time.time()
    can = model.canFetchMore(pidx)
    if can:
        model.fetchMore(pidx)
    print(f"[4] canFetchMore+fetchMore 第0行 耗时 {time.time()-t0:.4f}s  childCount={model.rowCount(pidx)}")

    # 5) 1000 行 data()（滚动场景）
    t0 = time.time()
    for r in range(1000):
        model.data(model.index(r, 5))
    print(f"[5] 1000 行 data(col5) 耗时 {time.time()-t0:.4f}s")

    # 6) 拆分首绘：show / processEvents / repaint 各段耗时
    w2 = MessageTableWidget()
    w2.set_data(frame_index.iloc[:2000], raw_data, [], dbc_path="", byte_change=byte_change[:2000])
    t0 = time.time(); w2.show(); print(f"[6a] show(2000行) {time.time()-t0:.4f}s")
    t0 = time.time(); QApplication.processEvents(); print(f"[6b] processEvents(2000行) {time.time()-t0:.4f}s")
    t0 = time.time(); w2._tree.viewport().repaint(); QApplication.processEvents(); print(f"[6c] repaint(2000行) {time.time()-t0:.4f}s")

    # 7) 带 DBC 的双击展开路径（用项目自带 test.dbc；0x1A0 有定义）
    dbc = os.path.join(os.path.dirname(__file__), "fixtures", "test.dbc")
    if os.path.exists(dbc):
        w3 = MessageTableWidget()
        # 只取含 0x1A0 的前若干帧，便于展开到已定义报文
        sub = frame_index[frame_index["arbitration_id"] == 0x1A0].head(50)
        w3.set_data(sub, raw_data, [], dbc_path=dbc, byte_change=byte_change[:len(sub)])
        w3.show(); QApplication.processEvents()
        # 找到一行 0x1A0 的顶层 index
        target = -1
        for r in range(model.rowCount()):
            pass
        # 在 w3 的模型里找 0x1A0
        m3 = w3._model
        tgt = -1
        for r in range(len(sub)):
            if int(sub.iloc[r]["arbitration_id"]) == 0x1A0:
                tgt = r; break
        pidx = m3.index(tgt, 0)
        t0 = time.time()
        m3.fetchMore(pidx)
        QApplication.processEvents()
        print(f"[7] fetchMore+DBC 展开第{tgt}行 耗时 {time.time()-t0:.4f}s  childCount={m3.rowCount(pidx)}")
        # 展开后整树 repaint
        t0 = time.time(); w3._tree.viewport().repaint(); QApplication.processEvents(); print(f"[7b] 展开后 repaint 耗时 {time.time()-t0:.4f}s")

    os.remove(tmp)
    print("DONE")


if __name__ == "__main__":
    main()
