# core/byte_change.py
"""向量化字节变化检测（替代原 message_table 中的 _compute_byte_change_info）

输出 (N, max_dlc) 的 uint16 数组，arr[iloc, byte_idx] = 该帧该字节距上次变化的
"筛选后序列帧数"（与旧逻辑的语义一致）：

- 某 arbitration_id 的首帧：所有字节 = NO_CHANGE(999)
- 同一 ID 的连续帧之间比较（仅 dlc > byte_idx 的帧才参与，其余无此字节）：
  - 字节值变化         → 0   （刚变化，高亮）
  - 未变化且此前从未变化 → NO_CHANGE(999)
  - 未变化且此前变化过   → 当前帧全局序号 - 上次变化帧全局序号
- 跨 arbitration_id 独立跟踪。

NO_CHANGE 取值 999（> FADE_FRAMES=500，渲染为正常色），与旧实现一致。
距离上限钳到 uint16 最大值，避免极长日志（>65535 帧未变化）下的数值回绕；
超过 FADE_FRAMES 一律按正常色渲染，钳顶不影响显示。

算法：先按 arbitration_id 做稳定排序分组（O(N log N)），再逐 (ID, 字节) 在组内用
numpy 向量化计算"最近一次变化位置"，从而把原 O(N) 的 Python 逐帧循环改为向量化，
可在后台线程对 10M 级帧量在亚秒级算完，且不再产生 GB 级嵌套字典。
"""
import numpy as np

NO_CHANGE = 999
_UINT16_MAX = np.iinfo(np.uint16).max


def compute_byte_change_array(frame_index, raw_data, max_dlc=None, no_change=NO_CHANGE):
    """计算每帧每字节的"距上次变化帧数"数组。

    参数:
        frame_index: DataFrame，必须含 frame_id / arbitration_id / dlc 列
                     （与 log_loader 输出格式一致）。
        raw_data:    (N, D) uint8 数组，raw_data[frame_id, :dlc] 为某帧原始字节。
        max_dlc:     字节列数（默认取 raw_data.shape[1]）。
        no_change:   首帧/从未变化的哨兵值（默认 999）。

    返回:
        (N, max_dlc) uint16 数组，arr[iloc, byte_idx] 为距上次变化的帧数。
    """
    n = len(frame_index)
    if raw_data is not None and getattr(raw_data, "ndim", 0) == 2:
        d_default = int(raw_data.shape[1])
    elif frame_index is not None and len(frame_index) > 0:
        d_default = int(frame_index["dlc"].to_numpy().max())
    else:
        d_default = 8

    if n == 0 or d_default <= 0:
        d = max_dlc if max_dlc else d_default
        return np.full((0, d if d > 0 else 8), no_change, dtype=np.uint16)

    if max_dlc is None:
        max_dlc = d_default
    max_dlc = int(max_dlc)

    ids = frame_index["arbitration_id"].to_numpy()
    dlc = frame_index["dlc"].to_numpy()
    fids = frame_index["frame_id"].to_numpy()

    out = np.full((n, max_dlc), no_change, dtype=np.uint16)

    # 按 arbitration_id 稳定排序分组：组内保持原始(筛选后)出现顺序
    order = np.argsort(ids, kind="stable")
    s_ids = ids[order]
    s_dlc = dlc[order]
    s_fid = fids[order]

    # 组边界：s_ids 中相邻不同的位置
    change_id = np.empty(s_ids.shape, dtype=bool)
    change_id[0] = True
    change_id[1:] = s_ids[1:] != s_ids[:-1]
    starts = np.flatnonzero(change_id)
    ends = np.append(starts[1:], s_ids.size)

    for s, e in zip(starts, ends):
        grp_g = order[s:e].astype(np.int64)   # 组内各帧的全局 iloc 位置（筛选后顺序）
        grp_dlc = s_dlc[s:e]
        grp_fid = s_fid[s:e]

        for b in range(max_dlc):
            dlc_ok = grp_dlc > b
            if not dlc_ok.any():
                continue
            rb = grp_g[dlc_ok]                       # 仅 dlc>b 的帧参与（其余无此字节）
            gr = rb                                  # 全局 iloc 位置 = 全局序号
            vals = raw_data[grp_fid[dlc_ok], b].astype(np.int64)
            k = vals.size
            if k == 0:
                continue

            # 与同 ID 内前一帧（同字节）比较 → 变化点
            change = np.empty(k, dtype=bool)
            change[0] = False
            change[1:] = vals[1:] != vals[:-1]
            change_idx = np.flatnonzero(change)

            c = np.full(k, no_change, dtype=np.int64)
            if change_idx.size:
                # m_idx[i] = 最近的 change 位置 <= i；之前无 change 则为 -1
                m_idx = np.full(k, -1, dtype=np.int64)
                m_idx[change_idx] = change_idx
                valid = m_idx != -1
                idx_arr = np.where(valid, np.arange(k), 0)
                np.maximum.accumulate(idx_arr, out=idx_arr)
                m_filled = np.where(valid, m_idx, idx_arr)

                first_change = int(change_idx[0])
                before = np.arange(k) < first_change
                other = ~before
                c[before] = no_change
                # 变化元素本身 m_filled==自身 → 0；其后元素为 gr 差值（全局距离）
                c[other] = gr[other] - gr[m_filled[other]]

            # 钳顶防止 uint16 回绕（>FADE_FRAMES 均按正常色渲染，钳顶无影响）
            c = np.minimum(c, _UINT16_MAX)
            out[rb, b] = c.astype(np.uint16)

    return out
