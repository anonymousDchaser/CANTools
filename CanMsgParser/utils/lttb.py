"""LTTB (Largest Triangle Three Buckets) 降采样算法"""
import numpy as np


def lttb_downsample(timestamps: np.ndarray, values: np.ndarray, threshold: int = 10000) -> tuple[np.ndarray, np.ndarray]:
    n = len(timestamps)
    if n <= threshold or threshold < 3:
        return timestamps.copy(), values.copy()

    sampled_indices = np.zeros(threshold, dtype=np.int64)
    sampled_indices[0] = 0
    sampled_indices[-1] = n - 1
    bucket_size = (n - 2) / (threshold - 2)
    prev_selected = 0

    for i in range(1, threshold - 1):
        bucket_start = int(np.floor((i - 1) * bucket_size)) + 1
        bucket_end = int(np.floor(i * bucket_size)) + 1
        bucket_end = min(bucket_end, n - 1)
        next_bucket_start = bucket_end
        next_bucket_end = int(np.floor((i + 1) * bucket_size)) + 1
        next_bucket_end = min(next_bucket_end, n)

        if next_bucket_end > next_bucket_start:
            avg_x = np.mean(timestamps[next_bucket_start:next_bucket_end])
            avg_y = np.mean(values[next_bucket_start:next_bucket_end])
        else:
            avg_x = timestamps[n - 1]
            avg_y = values[n - 1]

        p_x = timestamps[prev_selected]
        p_y = values[prev_selected]
        max_area = -1.0
        max_idx = bucket_start

        for j in range(bucket_start, bucket_end):
            area = abs((p_x - avg_x) * (values[j] - p_y) - (p_x - timestamps[j]) * (avg_y - p_y))
            if area > max_area:
                max_area = area
                max_idx = j

        sampled_indices[i] = max_idx
        prev_selected = max_idx

    return timestamps[sampled_indices], values[sampled_indices]
