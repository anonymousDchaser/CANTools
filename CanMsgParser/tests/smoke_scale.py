"""规模冒烟测试：用 20 万帧的合成 ASC 验证虚拟化管线在大数据量下不卡、不爆内存。"""
import os, sys, time, tempfile, tracemalloc

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
from core.log_loader import load_log_file
from widgets.message_table import MessageTableWidget

N = 200_000

def gen_asc(path):
    with open(path, "w") as f:
        f.write("date Mon Jun 15 10:00:00 AM 2026\n")
        f.write("base hex  timestamps absolute\n")
        f.write("no internal events logged\n")
        for i in range(N):
            t = 0.001 * i
            # 交替两个 ID，模拟真实混合流量
            arb = "1A0" if (i % 3) else "1A1"
            # 让部分字节逐帧变化，触发 byte_change 计算
            b0 = (i) & 0xFF
            b7 = ((i >> 8) ^ 0xCD) & 0xFF
            data = f"{b0:02X} 01 23 45 67 89 AB {b7:02X}"
            f.write(f"   {t:09.6f} 1  {arb}             Rx   d 8 {data}\n")

def main():
    app = QApplication.instance() or QApplication(sys.argv)
    tmp = tempfile.mkdtemp()
    asc = os.path.join(tmp, "scale.asc")
    print(f"[gen] 生成 {N} 帧 ASC ...")
    t0 = time.time()
    gen_asc(asc)
    print(f"[gen] 完成 用时 {time.time()-t0:.2f}s 文件大小 {os.path.getsize(asc)/1e6:.1f}MB")

    tracemalloc.start()
    print("[load] load_log_file ...")
    t0 = time.time()
    fi, raw, bc = load_log_file(asc)
    dt = time.time() - t0
    cur, peak = tracemalloc.get_traced_memory()
    print(f"[load] 完成 用时 {dt:.2f}s  帧数={len(fi)}")
    print(f"[load] raw_data={raw.shape} dtype={raw.dtype}  byte_change={bc.shape} dtype={bc.dtype}")
    print(f"[load] 解析阶段 Python 堆峰值 {peak/1e6:.1f}MB (tracemalloc, 不含 numpy 缓冲)")

    # 取出 DBC（用测试 fixture 验证解码路径也能跑）
    dbc = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tests", "fixtures", "test.dbc")

    w = MessageTableWidget()
    print("[set] set_data 注入模型（虚拟化，不应为每个帧建对象）...")
    t0 = time.time()
    messages = []  # 这里不依赖 messages，仅走解码缓存
    w.set_data(fi, raw, messages, dbc, bc)
    dt = time.time() - t0
    m = w._model
    print(f"[set] 完成 用时 {dt:.2f}s  rowCount(顶层)={m.rowCount()}")

    # 验证虚拟化：rowCount 巨大但 data() 只按需调用
    assert m.rowCount() == N, f"rowCount 应为 {N}, 实际 {m.rowCount()}"

    # 验证懒加载解码：展开第 0 行
    idx0 = m.index(0, 0)
    assert m.canFetchMore(idx0), "首个顶层帧应可展开"
    rc_before = m.rowCount(idx0)
    assert rc_before == 0, f"展开前子项数应为 0, 实际 {rc_before}"
    m.fetchMore(idx0)
    rc_after = m.rowCount(idx0)
    assert rc_after > 0, f"展开后应有子项, 实际 {rc_after}"
    # 取第一个子项信号名
    child0 = m.data(m.index(0, 1, idx0))
    print(f"[fetch] 展开第0行 -> 子项数={rc_after}, 首个信号列文本={child0!r}")
    assert child0 is not None, "首个子项信号名不应为 None（此前 r=s=0 撞 0 哨兵的回归）"

    # 验证跨 10 万行取数据不崩（抽样，不遍历全部）
    for r in (0, 100000, 199999):
        row = m.data(m.index(r, 5))  # Data(Hex) 列
        assert row is not None and len(row) > 0, f"第 {r} 行 Data 列为空"

    cur2, peak2 = tracemalloc.get_traced_memory()
    print(f"[mem] set_data 后 Python 堆峰值 {peak2/1e6:.1f}MB")
    print("SCALE SMOKE OK")

if __name__ == "__main__":
    main()
