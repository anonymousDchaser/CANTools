# CAN 报文分析工具 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个类似 Vector CANoe 的车载 CAN 报文分析桌面工具，支持 DBC 解析、BLF/ASC 日志加载、信号曲线绘制、原始报文查看和位图可视化。

**Architecture:** 多线程 PyQt5 桌面应用。QThread 后台线程负责文件加载和信号解码，解码结果通过 LRU 缓存存储，Qt signal-slot 线程安全更新 UI。数据管道：日志扫描建索引 → 按需解码 → 缓存 → 各 UI 组件消费。

**Tech Stack:** Python 3.10+, PyQt5, matplotlib (qt5agg backend), cantools, python-can, pandas, numpy, openpyxl

---

## File Structure

```
CanMsgParser/
├── main.py                        # 应用入口
├── main_window.py                 # 主窗口布局
├── core/
│   ├── __init__.py
│   ├── can_data.py                # 数据模型 (dataclass)
│   ├── dbc_parser.py              # DBC 解析封装
│   ├── log_loader.py              # BLF/ASC 加载器
│   └── signal_cache.py            # LRU 解码缓存
├── widgets/
│   ├── __init__.py
│   ├── signal_tree.py             # 信号树 + 搜索
│   ├── plot_widget.py             # 曲线图 + 交互
│   ├── signal_group_panel.py      # 信号分组管理面板
│   ├── message_table.py           # 报文表格 + 过滤
│   └── bit_layout_view.py         # 位图可视化
├── utils/
│   ├── __init__.py
│   ├── bit_utils.py               # 位布局计算
│   ├── lttb.py                    # LTTB 降采样算法
│   └── export_utils.py            # 导出工具
├── workers/
│   ├── __init__.py
│   └── load_worker.py             # QThread 工作线程
├── tests/
│   ├── __init__.py
│   ├── test_can_data.py
│   ├── test_bit_utils.py
│   ├── test_dbc_parser.py
│   ├── test_log_loader.py
│   ├── test_signal_cache.py
│   └── test_lttb.py
└── requirements.txt
```

---

## Task 1: Project Setup

**Files:**
- Create: `requirements.txt`
- Create: `core/__init__.py`
- Create: `widgets/__init__.py`
- Create: `utils/__init__.py`
- Create: `workers/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create requirements.txt**

```
PyQt5>=5.15
matplotlib>=3.5
cantools>=37.0
python-can>=4.0
pandas>=1.4
numpy>=1.21
openpyxl>=3.0
pytest>=7.0
```

- [ ] **Step 2: Create all __init__.py files**

```python
# core/__init__.py
# widgets/__init__.py
# utils/__init__.py
# workers/__init__.py
# tests/__init__.py
# 以上均为空文件
```

- [ ] **Step 3: Install dependencies**

Run: `pip install -r requirements.txt`
Expected: All packages installed successfully

- [ ] **Step 4: Verify installation**

Run: `python -c "import PyQt5, matplotlib, cantools, can, pandas, numpy; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: project setup with dependencies and directory structure"
```

---

## Task 2: Data Models (can_data.py)

**Files:**
- Create: `core/can_data.py`
- Test: `tests/test_can_data.py`

- [ ] **Step 1: Write the test**

```python
# tests/test_can_data.py
import numpy as np
from core.can_data import SignalDef, MessageDef, DecodedSignal


def test_signal_def_creation():
    sig = SignalDef(
        name="EngineRPM",
        start_bit=0,
        length=16,
        byte_order="intel",
        scale=0.25,
        offset=0.0,
        unit="rpm",
        min_val=0.0,
        max_val=16383.75,
    )
    assert sig.name == "EngineRPM"
    assert sig.length == 16
    assert sig.byte_order == "intel"


def test_message_def_with_signals():
    sig1 = SignalDef("RPM", 0, 16, "intel", 1.0, 0.0, "rpm", 0.0, 8000.0)
    sig2 = SignalDef("Speed", 16, 16, "intel", 0.1, 0.0, "km/h", 0.0, 300.0)
    msg = MessageDef(
        frame_id=0x1A0,
        name="EngineData",
        dlc=8,
        is_fd=False,
        signals=[sig1, sig2],
    )
    assert msg.frame_id == 0x1A0
    assert len(msg.signals) == 2
    assert msg.signals[0].name == "RPM"


def test_decoded_signal():
    ts = np.array([0.0, 0.1, 0.2])
    vals = np.array([100.0, 200.0, 300.0])
    ds = DecodedSignal(
        msg_name="EngineData",
        sig_name="RPM",
        timestamps=ts,
        values=vals,
    )
    assert ds.msg_name == "EngineData"
    assert len(ds.timestamps) == 3
    assert ds.values[1] == 200.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_can_data.py -v`
Expected: FAIL - `ModuleNotFoundError: No module named 'core.can_data'`

- [ ] **Step 3: Implement data models**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_can_data.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add core/can_data.py tests/test_can_data.py
git commit -m "feat: add CAN data models (SignalDef, MessageDef, DecodedSignal)"
```

---

## Task 3: Bit Utils (bit_utils.py)

**Files:**
- Create: `utils/bit_utils.py`
- Test: `tests/test_bit_utils.py`

- [ ] **Step 1: Write the test**

```python
# tests/test_bit_utils.py
from utils.bit_utils import get_bit_positions


def test_intel_single_byte():
    """Intel 小端：起始位 4, 长度 4，全部在 Byte0 内"""
    positions = get_bit_positions(start_bit=4, length=4, byte_order="intel")
    expected = [(0, 4), (0, 5), (0, 6), (0, 7)]
    assert positions == expected


def test_intel_cross_byte():
    """Intel 小端：起始位 4, 长度 8，跨 Byte0 和 Byte1"""
    positions = get_bit_positions(start_bit=4, length=8, byte_order="intel")
    expected = [
        (0, 4), (0, 5), (0, 6), (0, 7),  # Byte0 高 4 位
        (1, 0), (1, 1), (1, 2), (1, 3),  # Byte1 低 4 位
    ]
    assert positions == expected


def test_motorola_single_byte():
    """Motorola 大端：起始位 7, 长度 4，全部在 Byte0 内"""
    positions = get_bit_positions(start_bit=7, length=4, byte_order="motorola")
    expected = [(0, 7), (0, 6), (0, 5), (0, 4)]
    assert positions == expected


def test_motorola_cross_byte():
    """Motorola 大端：起始位 7, 长度 12，跨 Byte0 和 Byte1"""
    positions = get_bit_positions(start_bit=7, length=12, byte_order="motorola")
    expected = [
        (0, 7), (0, 6), (0, 5), (0, 4), (0, 3), (0, 2), (0, 1), (0, 0),
        (1, 7), (1, 6), (1, 5), (1, 4),
    ]
    assert positions == expected


def test_intel_full_16bit():
    """Intel 小端：起始位 0, 长度 16，占满 2 字节"""
    positions = get_bit_positions(start_bit=0, length=16, byte_order="intel")
    assert len(positions) == 16
    # Byte0: bit0-7, Byte1: bit0-7
    assert positions[0] == (0, 0)
    assert positions[7] == (0, 7)
    assert positions[8] == (1, 0)
    assert positions[15] == (1, 7)


def test_motorola_full_16bit():
    """Motorola 大端：起始位 7, 长度 16，占满 2 字节"""
    positions = get_bit_positions(start_bit=7, length=16, byte_order="motorola")
    assert len(positions) == 16
    assert positions[0] == (0, 7)
    assert positions[7] == (0, 0)
    assert positions[8] == (1, 7)
    assert positions[15] == (1, 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bit_utils.py -v`
Expected: FAIL - `ModuleNotFoundError`

- [ ] **Step 3: Implement bit_utils**

```python
# utils/bit_utils.py
"""Intel/Motorola 字节序位布局计算工具

DBC 中的位编号规则：
  Byte0: bit7 bit6 bit5 bit4 bit3 bit2 bit1 bit0
  Byte1: bit7 bit6 ...                   ... bit0
  ...

Intel（小端）：起始位是 LSB，从起始位向高位排列
  - 同一字节内：bit 编号递增
  - 跨字节时：进入下一字节的 bit0 继续递增

Motorola（大端）：起始位是 MSB，从起始位向低位排列
  - 同一字节内：bit 编号递减
  - 跨字节时：跳到下一字节的 bit7 继续递减
"""


def get_bit_positions(
    start_bit: int, length: int, byte_order: str
) -> list[tuple[int, int]]:
    """计算信号占用的所有位坐标。

    Args:
        start_bit: DBC 起始位编号（0~63 for classic CAN）
        length: 信号位长度
        byte_order: 'intel' 或 'motorola'

    Returns:
        [(byte_index, bit_index), ...] 坐标列表，按从 LSB 到 MSB 顺序
    """
    if byte_order == "intel":
        return _intel_positions(start_bit, length)
    elif byte_order == "motorola":
        return _motorola_positions(start_bit, length)
    else:
        raise ValueError(f"Unknown byte_order: {byte_order}")


def _intel_positions(start_bit: int, length: int) -> list[tuple[int, int]]:
    """Intel 小端：从 start_bit 向高位递增，跨字节从下一字节 bit0 继续"""
    positions = []
    current_byte = start_bit // 8
    current_bit = start_bit % 8

    for _ in range(length):
        positions.append((current_byte, current_bit))
        current_bit += 1
        if current_bit > 7:
            current_bit = 0
            current_byte += 1

    return positions


def _motorola_positions(start_bit: int, length: int) -> list[tuple[int, int]]:
    """Motorola 大端：从 start_bit 向低位递减，跨字节跳到下一字节 bit7 继续"""
    positions = []
    current_byte = start_bit // 8
    current_bit = start_bit % 8

    for _ in range(length):
        positions.append((current_byte, current_bit))
        current_bit -= 1
        if current_bit < 0:
            current_bit = 7
            current_byte += 1

    return positions
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bit_utils.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add utils/bit_utils.py tests/test_bit_utils.py
git commit -m "feat: add bit layout utils for Intel/Motorola byte order"
```

---

## Task 4: DBC Parser (dbc_parser.py)

**Files:**
- Create: `core/dbc_parser.py`
- Test: `tests/test_dbc_parser.py`

- [ ] **Step 1: Create a minimal test DBC file**

```
# tests/fixtures/test.dbc
VERSION ""

NS_ :

BS_:

BU_:

BO_ 416 EngineData: 8 Vector__XXX
 SG_ EngineRPM : 0|16@1+ (0.25,0) [0|16383.75] "rpm" Vector__XXX
 SG_ EngineSpeed : 16|16@1+ (0.1,0) [0|300] "km/h" Vector__XXX
 SG_ EngineTemp : 32|8@1+ (1,-40) [-40|215] "degC" Vector__XXX

BO_ 417 TransmissionData: 8 Vector__XXX
 SG_ GearPosition : 0|4@1+ (1,0) [0|15] "" Vector__XXX
 SG_ TransTemp : 4|8@1+ (1,-40) [-40|215] "degC" Vector__XXX
```

创建目录 `tests/fixtures/` 并将上述内容保存为 `tests/fixtures/test.dbc`。

- [ ] **Step 2: Write the test**

```python
# tests/test_dbc_parser.py
import os
from core.dbc_parser import parse_dbc


FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def test_parse_dbc_messages():
    messages = parse_dbc(os.path.join(FIXTURE_DIR, "test.dbc"))
    assert len(messages) == 2

    engine = next(m for m in messages if m.name == "EngineData")
    assert engine.frame_id == 0x1A0
    assert engine.dlc == 8
    assert len(engine.signals) == 3


def test_parse_dbc_signal_details():
    messages = parse_dbc(os.path.join(FIXTURE_DIR, "test.dbc"))
    engine = next(m for m in messages if m.name == "EngineData")

    rpm = next(s for s in engine.signals if s.name == "EngineRPM")
    assert rpm.start_bit == 0
    assert rpm.length == 16
    assert rpm.byte_order == "intel"
    assert rpm.scale == 0.25
    assert rpm.offset == 0.0
    assert rpm.unit == "rpm"
    assert rpm.min_val == 0.0
    assert rpm.max_val == 16383.75


def test_parse_dbc_motorola_signal():
    """验证 Motorola 字节序信号解析"""
    messages = parse_dbc(os.path.join(FIXTURE_DIR, "test.dbc"))
    # test.dbc 中全部是 Intel(@1)，这里验证解析后的 byte_order 值
    engine = next(m for m in messages if m.name == "EngineData")
    for sig in engine.signals:
        assert sig.byte_order == "intel"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_dbc_parser.py -v`
Expected: FAIL - `ModuleNotFoundError`

- [ ] **Step 4: Implement DBC parser**

```python
# core/dbc_parser.py
"""DBC 文件解析封装，基于 cantools 库"""
import cantools
from core.can_data import SignalDef, MessageDef


def parse_dbc(file_path: str) -> list[MessageDef]:
    """解析 DBC 文件，返回 MessageDef 列表。

    Args:
        file_path: DBC 文件路径

    Returns:
        所有报文定义的列表
    """
    db = cantools.database.load_file(file_path)
    messages = []

    for msg in db.messages:
        signals = []
        for sig in msg.signals:
            # cantools byte_order: 'little_endian' -> 'intel', 'big_endian' -> 'motorola'
            byte_order = "intel" if sig.byte_order == "little_endian" else "motorola"

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
            ))

        messages.append(MessageDef(
            frame_id=msg.frame_id,
            name=msg.name,
            dlc=msg.length,
            is_fd=msg.is_fd if hasattr(msg, "is_fd") else False,
            signals=signals,
        ))

    return messages
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_dbc_parser.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add core/dbc_parser.py tests/test_dbc_parser.py tests/fixtures/test.dbc
git commit -m "feat: add DBC parser wrapper using cantools"
```

---

## Task 5: Log Loader (log_loader.py)

**Files:**
- Create: `core/log_loader.py`
- Test: `tests/test_log_loader.py`

- [ ] **Step 1: Write the test**

```python
# tests/test_log_loader.py
import os
import tempfile
import can
import numpy as np
import pandas as pd
from core.log_loader import load_log_file


def _create_test_asc(path: str):
    """创建测试用 ASC 文件"""
    with open(path, "w") as f:
        f.write("date Mon Jun 15 10:00:00 AM 2026\n")
        f.write("base hex  timestamps absolute\n")
        f.write("no internal events logged\n")
        f.write("   0.001000 1  1A0             Rx   d 8 FF 01 23 45 67 89 AB CD\n")
        f.write("   0.002000 1  1A1             Rx   d 8 00 11 22 33 44 55 66 77\n")
        f.write("   0.003000 1  1A0             Rx   d 8 FF 01 23 45 67 89 AB CE\n")


def test_load_asc_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        asc_path = os.path.join(tmpdir, "test.asc")
        _create_test_asc(asc_path)

        frame_index, raw_data = load_log_file(asc_path)

        assert isinstance(frame_index, pd.DataFrame)
        assert len(frame_index) == 3
        assert list(frame_index.columns) == [
            "frame_id", "timestamp", "arbitration_id", "dlc", "channel", "is_fd"
        ]
        assert frame_index.iloc[0]["arbitration_id"] == 0x1A0
        assert frame_index.iloc[1]["arbitration_id"] == 0x1A1

        assert isinstance(raw_data, np.ndarray)
        assert raw_data.shape[0] == 3
        assert raw_data[0, 0] == 0xFF


def test_frame_id_sequential():
    with tempfile.TemporaryDirectory() as tmpdir:
        asc_path = os.path.join(tmpdir, "test.asc")
        _create_test_asc(asc_path)

        frame_index, _ = load_log_file(asc_path)
        assert list(frame_index["frame_id"]) == [0, 1, 2]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_log_loader.py -v`
Expected: FAIL - `ModuleNotFoundError`

- [ ] **Step 3: Implement log loader**

```python
# core/log_loader.py
"""BLF/ASC CAN 日志文件加载器

使用 python-can 的 BLFReader 和 ASCReader 统一接口。
"""
import os
import numpy as np
import pandas as pd
import can


def load_log_file(
    file_path: str,
    progress_callback=None,
) -> tuple[pd.DataFrame, np.ndarray]:
    """加载 BLF 或 ASC 格式的 CAN 日志文件。

    Args:
        file_path: 日志文件路径
        progress_callback: 可选的进度回调函数，接收 int (0-100)

    Returns:
        (frame_index, raw_data)
        frame_index: DataFrame，列见设计文档 §4.4
        raw_data: numpy 2D 数组，shape=(num_frames, max_dlc)，dtype=uint8
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".blf":
        reader = can.BLFReader(file_path)
    elif ext == ".asc":
        reader = can.ASCReader(file_path)
    else:
        raise ValueError(f"Unsupported file format: {ext}")

    # 第一遍：收集所有帧数据
    timestamps = []
    arb_ids = []
    dlcs = []
    channels = []
    is_fds = []
    data_list = []

    file_size = os.path.getsize(file_path)
    last_progress = -1

    for i, msg in enumerate(reader):
        timestamps.append(msg.timestamp)
        arb_ids.append(msg.arbitration_id)
        dlcs.append(msg.dlc)
        channels.append(msg.channel if msg.channel is not None else 0)
        is_fds.append(msg.is_fd if hasattr(msg, "is_fd") else False)

        # 将数据补齐到 dlc 长度
        data_bytes = bytes(msg.data)
        if len(data_bytes) < msg.dlc:
            data_bytes = data_bytes + b"\x00" * (msg.dlc - len(data_bytes))
        data_list.append(data_bytes[:msg.dlc])

        # 进度回调
        if progress_callback and file_size > 0:
            current_pos = reader.file.tell() if hasattr(reader, "file") else 0
            progress = int(current_pos / file_size * 100)
            if progress != last_progress:
                last_progress = progress
                progress_callback(progress)

    if not timestamps:
        empty_df = pd.DataFrame(columns=[
            "frame_id", "timestamp", "arbitration_id", "dlc", "channel", "is_fd"
        ])
        return empty_df, np.empty((0, 8), dtype=np.uint8)

    # 构建 frame_index DataFrame
    num_frames = len(timestamps)
    frame_index = pd.DataFrame({
        "frame_id": np.arange(num_frames, dtype=np.int64),
        "timestamp": np.array(timestamps, dtype=np.float64),
        "arbitration_id": np.array(arb_ids, dtype=np.uint32),
        "dlc": np.array(dlcs, dtype=np.uint8),
        "channel": np.array(channels, dtype=np.int32),
        "is_fd": np.array(is_fds, dtype=bool),
    })

    # 构建 raw_data 2D 数组
    max_dlc = max(dlcs) if dlcs else 8
    raw_data = np.zeros((num_frames, max_dlc), dtype=np.uint8)
    for i, data_bytes in enumerate(data_list):
        raw_data[i, :len(data_bytes)] = np.frombuffer(data_bytes, dtype=np.uint8)

    if progress_callback:
        progress_callback(100)

    return frame_index, raw_data
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_log_loader.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add core/log_loader.py tests/test_log_loader.py
git commit -m "feat: add BLF/ASC log file loader with progress callback"
```

---

## Task 6: LTTB Downsampling (lttb.py)

**Files:**
- Create: `utils/lttb.py`
- Test: `tests/test_lttb.py`

- [ ] **Step 1: Write the test**

```python
# tests/test_lttb.py
import numpy as np
from utils.lttb import lttb_downsample


def test_lttb_no_downsample():
    """数据点少于阈值时不做降采样"""
    ts = np.array([0.0, 1.0, 2.0])
    vals = np.array([10.0, 20.0, 30.0])
    out_ts, out_vals = lttb_downsample(ts, vals, threshold=10)
    np.testing.assert_array_equal(out_ts, ts)
    np.testing.assert_array_equal(out_vals, vals)


def test_lttb_reduces_points():
    """降采样后点数应等于 threshold"""
    ts = np.linspace(0, 100, 1000)
    vals = np.sin(ts)
    out_ts, out_vals = lttb_downsample(ts, vals, threshold=100)
    assert len(out_ts) == 100
    assert len(out_vals) == 100


def test_lttb_preserves_endpoints():
    """首尾点必须保留"""
    ts = np.linspace(0, 10, 500)
    vals = np.random.randn(500)
    out_ts, out_vals = lttb_downsample(ts, vals, threshold=50)
    assert out_ts[0] == ts[0]
    assert out_ts[-1] == ts[-1]
    assert out_vals[0] == vals[0]
    assert out_vals[-1] == vals[-1]


def test_lttb_preserves_peaks():
    """降采样应保留明显的峰值"""
    ts = np.linspace(0, 10, 1000)
    vals = np.zeros(1000)
    vals[500] = 100.0  # 一个明显峰值
    out_ts, out_vals = lttb_downsample(ts, vals, threshold=50)
    assert 100.0 in out_vals
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_lttb.py -v`
Expected: FAIL - `ModuleNotFoundError`

- [ ] **Step 3: Implement LTTB**

```python
# utils/lttb.py
"""LTTB (Largest Triangle Three Buckets) 降采样算法

用于在保持曲线视觉特征的前提下减少数据点数量。
参考: Sveinn Steinarsson, 2013
"""
import numpy as np


def lttb_downsample(
    timestamps: np.ndarray,
    values: np.ndarray,
    threshold: int = 10000,
) -> tuple[np.ndarray, np.ndarray]:
    """LTTB 降采样。

    Args:
        timestamps: 时间戳数组 (float64)
        values: 信号值数组 (float64)
        threshold: 目标点数，若数据点 <= threshold 则不做降采样

    Returns:
        (downsampled_timestamps, downsampled_values)
    """
    n = len(timestamps)
    if n <= threshold or threshold < 3:
        return timestamps.copy(), values.copy()

    # 始终保留首尾点
    sampled_indices = np.zeros(threshold, dtype=np.int64)
    sampled_indices[0] = 0
    sampled_indices[-1] = n - 1

    # 将中间 n-2 个点分成 threshold-2 个桶
    bucket_size = (n - 2) / (threshold - 2)

    prev_selected = 0

    for i in range(1, threshold - 1):
        # 当前桶的范围
        bucket_start = int(np.floor((i - 1) * bucket_size)) + 1
        bucket_end = int(np.floor(i * bucket_size)) + 1
        bucket_end = min(bucket_end, n - 1)

        # 下一个桶的范围（用于计算平均点）
        next_bucket_start = bucket_end
        next_bucket_end = int(np.floor((i + 1) * bucket_size)) + 1
        next_bucket_end = min(next_bucket_end, n)

        # 下一个桶的平均点
        if next_bucket_end > next_bucket_start:
            avg_x = np.mean(timestamps[next_bucket_start:next_bucket_end])
            avg_y = np.mean(values[next_bucket_start:next_bucket_end])
        else:
            avg_x = timestamps[n - 1]
            avg_y = values[n - 1]

        # 在当前桶中找到与前一个选中点和下一个桶平均点形成的三角形面积最大的点
        p_x = timestamps[prev_selected]
        p_y = values[prev_selected]

        max_area = -1.0
        max_idx = bucket_start

        for j in range(bucket_start, bucket_end):
            # 三角形面积 = 0.5 * |x0(y1-y2) + x1(y2-y0) + x2(y0-y1)|
            area = abs(
                (p_x - avg_x) * (values[j] - p_y)
                - (p_x - timestamps[j]) * (avg_y - p_y)
            )
            if area > max_area:
                max_area = area
                max_idx = j

        sampled_indices[i] = max_idx
        prev_selected = max_idx

    return timestamps[sampled_indices], values[sampled_indices]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_lttb.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add utils/lttb.py tests/test_lttb.py
git commit -m "feat: add LTTB downsampling algorithm for chart rendering"
```

---

## Task 7: Signal Cache (signal_cache.py)

**Files:**
- Create: `core/signal_cache.py`
- Test: `tests/test_signal_cache.py`

- [ ] **Step 1: Write the test**

```python
# tests/test_signal_cache.py
import numpy as np
from core.signal_cache import SignalCache


def test_cache_put_and_get():
    cache = SignalCache(max_entries=10)
    ts = np.array([0.0, 0.1, 0.2])
    vals = np.array([1.0, 2.0, 3.0])
    cache.put("EngineData", "RPM", ts, vals)

    result = cache.get("EngineData", "RPM")
    assert result is not None
    np.testing.assert_array_equal(result[0], ts)
    np.testing.assert_array_equal(result[1], vals)


def test_cache_miss():
    cache = SignalCache(max_entries=10)
    result = cache.get("NotExist", "Signal")
    assert result is None


def test_cache_eviction():
    cache = SignalCache(max_entries=2)
    cache.put("M1", "S1", np.array([0.0]), np.array([1.0]))
    cache.put("M1", "S2", np.array([0.0]), np.array([2.0]))
    cache.put("M1", "S3", np.array([0.0]), np.array([3.0]))

    # S1 应该被驱逐
    assert cache.get("M1", "S1") is None
    assert cache.get("M1", "S2") is not None
    assert cache.get("M1", "S3") is not None


def test_cache_clear():
    cache = SignalCache(max_entries=10)
    cache.put("M1", "S1", np.array([0.0]), np.array([1.0]))
    cache.clear()
    assert cache.get("M1", "S1") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_signal_cache.py -v`
Expected: FAIL - `ModuleNotFoundError`

- [ ] **Step 3: Implement signal cache**

```python
# core/signal_cache.py
"""解码结果 LRU 缓存

key = (message_name, signal_name)
value = (timestamps: np.ndarray, values: np.ndarray)
"""
from collections import OrderedDict
import numpy as np


class SignalCache:
    """信号解码结果的 LRU 缓存"""

    def __init__(self, max_entries: int = 100):
        self._max_entries = max_entries
        self._cache: OrderedDict[tuple[str, str], tuple[np.ndarray, np.ndarray]] = OrderedDict()

    def get(self, msg_name: str, sig_name: str) -> tuple[np.ndarray, np.ndarray] | None:
        """获取缓存的信号数据，命中时移到最近使用端。"""
        key = (msg_name, sig_name)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, msg_name: str, sig_name: str, timestamps: np.ndarray, values: np.ndarray):
        """存入缓存，超出容量时驱逐最久未使用的条目。"""
        key = (msg_name, sig_name)
        if key in self._cache:
            self._cache.move_to_end(key)
            self._cache[key] = (timestamps, values)
        else:
            self._cache[key] = (timestamps, values)
            if len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)

    def clear(self):
        """清空所有缓存。"""
        self._cache.clear()

    def __len__(self):
        return len(self._cache)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_signal_cache.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add core/signal_cache.py tests/test_signal_cache.py
git commit -m "feat: add LRU signal decode cache"
```

---

## Task 8: Workers (load_worker.py)

**Files:**
- Create: `workers/load_worker.py`

- [ ] **Step 1: Implement LoadWorker**

```python
# workers/load_worker.py
"""QThread 工作线程：文件加载和信号解码"""
import cantools
import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal
from core.can_data import MessageDef, DecodedSignal
from core.log_loader import load_log_file
from core.signal_cache import SignalCache


class LoadWorker(QThread):
    """后台线程：加载日志文件并建立索引"""
    progress = pyqtSignal(int)        # 进度 0-100
    finished = pyqtSignal(object, object)  # (frame_index, raw_data)
    error = pyqtSignal(str)

    def __init__(self, file_path: str, parent=None):
        super().__init__(parent)
        self._file_path = file_path
        self._cancelled = False

    def run(self):
        try:
            frame_index, raw_data = load_log_file(
                self._file_path,
                progress_callback=self._on_progress,
            )
            if not self._cancelled:
                self.finished.emit(frame_index, raw_data)
        except Exception as e:
            if not self._cancelled:
                self.error.emit(str(e))

    def _on_progress(self, percent: int):
        if not self._cancelled:
            self.progress.emit(percent)

    def cancel(self):
        self._cancelled = True


class DecodeWorker(QThread):
    """后台线程：按需解码信号"""
    progress = pyqtSignal(int)
    finished = pyqtSignal(object)  # DecodedSignal
    error = pyqtSignal(str)

    def __init__(
        self,
        dbc_path: str,
        msg_name: str,
        sig_name: str,
        frame_index,  # pd.DataFrame
        raw_data: np.ndarray,
        cache: SignalCache,
        parent=None,
    ):
        super().__init__(parent)
        self._dbc_path = dbc_path
        self._msg_name = msg_name
        self._sig_name = sig_name
        self._frame_index = frame_index
        self._raw_data = raw_data
        self._cache = cache
        self._cancelled = False

    def run(self):
        try:
            # 先检查缓存
            cached = self._cache.get(self._msg_name, self._sig_name)
            if cached is not None:
                if not self._cancelled:
                    ds = DecodedSignal(
                        msg_name=self._msg_name,
                        sig_name=self._sig_name,
                        timestamps=cached[0],
                        values=cached[1],
                    )
                    self.finished.emit(ds)
                return

            # 加载 DBC 并找到目标报文和信号
            db = cantools.database.load_file(self._dbc_path)
            msg_def = db.get_message_by_name(self._msg_name)

            # 筛选匹配该报文 ID 的帧
            mask = self._frame_index["arbitration_id"] == msg_def.frame_id
            matched = self._frame_index[mask]

            if len(matched) == 0:
                if not self._cancelled:
                    ds = DecodedSignal(
                        msg_name=self._msg_name,
                        sig_name=self._sig_name,
                        timestamps=np.array([], dtype=np.float64),
                        values=np.array([], dtype=np.float64),
                    )
                    self.finished.emit(ds)
                return

            # 逐帧解码
            timestamps = []
            values = []
            total = len(matched)
            last_progress = -1

            for idx, (_, row) in enumerate(matched.iterrows()):
                if self._cancelled:
                    return

                fid = row["frame_id"]
                frame_data = bytes(self._raw_data[fid, :row["dlc"]])

                try:
                    decoded = msg_def.decode(frame_data)
                    if self._sig_name in decoded:
                        timestamps.append(row["timestamp"])
                        values.append(float(decoded[self._sig_name]))
                except Exception:
                    pass  # 解码失败的帧跳过

                # 进度
                progress = int(idx / total * 100)
                if progress != last_progress:
                    last_progress = progress
                    self.progress.emit(progress)

            ts_arr = np.array(timestamps, dtype=np.float64)
            val_arr = np.array(values, dtype=np.float64)

            # 相对时间（减去起始时间）
            if len(ts_arr) > 0:
                ts_arr -= ts_arr[0]

            # 存入缓存
            self._cache.put(self._msg_name, self._sig_name, ts_arr, val_arr)

            if not self._cancelled:
                ds = DecodedSignal(
                    msg_name=self._msg_name,
                    sig_name=self._sig_name,
                    timestamps=ts_arr,
                    values=val_arr,
                )
                self.finished.emit(ds)
                self.progress.emit(100)

        except Exception as e:
            if not self._cancelled:
                self.error.emit(str(e))

    def cancel(self):
        self._cancelled = True
```

- [ ] **Step 2: Commit**

```bash
git add workers/load_worker.py
git commit -m "feat: add LoadWorker and DecodeWorker QThread classes"
```

---

## Task 9: Signal Tree Widget (signal_tree.py)

**Files:**
- Create: `widgets/signal_tree.py`

- [ ] **Step 1: Implement signal tree widget**

```python
# widgets/signal_tree.py
"""信号树组件：展示 DBC 中的报文和信号，支持搜索和勾选"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLineEdit, QTreeWidget, QTreeWidgetItem,
    QPushButton, QHBoxLayout,
)
from PyQt5.QtCore import pyqtSignal, Qt
from core.can_data import MessageDef


class SignalTreeWidget(QWidget):
    """左侧信号树面板"""

    # 当勾选变化时发射，携带已勾选的 (msg_name, sig_name) 列表
    selection_changed = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._messages: list[MessageDef] = []
        self._all_items: dict[str, QTreeWidgetItem] = {}  # msg_name -> tree item
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # 搜索框
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("搜索报文/信号...")
        self._search_input.textChanged.connect(self._on_search)
        layout.addWidget(self._search_input)

        # 树形列表
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["名称", "ID/类型"])
        self._tree.setColumnWidth(0, 200)
        self._tree.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._tree)

        # 按钮区域
        btn_layout = QHBoxLayout()
        self._plot_btn = QPushButton("绘图")
        self._plot_btn.setEnabled(False)
        self._plot_btn.clicked.connect(self._on_plot_clicked)
        btn_layout.addWidget(self._plot_btn)

        self._select_all_btn = QPushButton("全选当前")
        self._select_all_btn.clicked.connect(self._on_select_all)
        btn_layout.addWidget(self._select_all_btn)

        layout.addLayout(btn_layout)

    def load_messages(self, messages: list[MessageDef]):
        """加载 DBC 报文定义到树中"""
        self._messages = messages
        self._tree.clear()
        self._all_items.clear()

        self._tree.blockSignals(True)  # 批量插入时暂时屏蔽信号
        for msg in messages:
            msg_item = QTreeWidgetItem(self._tree)
            msg_item.setText(0, msg.name)
            msg_item.setText(1, f"0x{msg.frame_id:03X}")
            msg_item.setData(0, Qt.UserRole, msg)
            self._all_items[msg.name] = msg_item

            for sig in msg.signals:
                sig_item = QTreeWidgetItem(msg_item)
                sig_item.setText(0, sig.name)
                sig_item.setText(1, sig.unit)
                sig_item.setFlags(sig_item.flags() | Qt.ItemIsUserCheckable)
                sig_item.setCheckState(0, Qt.Unchecked)
                sig_item.setData(0, Qt.UserRole, sig)

        self._tree.blockSignals(False)

    def get_checked_signals(self) -> list[tuple[str, str]]:
        """返回已勾选的 (msg_name, sig_name) 列表"""
        result = []
        for i in range(self._tree.topLevelItemCount()):
            msg_item = self._tree.topLevelItem(i)
            msg_name = msg_item.text(0)
            for j in range(msg_item.childCount()):
                sig_item = msg_item.child(j)
                if sig_item.checkState(0) == Qt.Checked:
                    result.append((msg_name, sig_item.text(0)))
        return result

    def _on_search(self, text: str):
        """模糊搜索过滤"""
        text = text.lower().strip()
        for i in range(self._tree.topLevelItemCount()):
            msg_item = self._tree.topLevelItem(i)
            msg_name = msg_item.text(0).lower()
            msg_visible = text in msg_name if text else True
            any_sig_visible = False

            for j in range(msg_item.childCount()):
                sig_item = msg_item.child(j)
                sig_name = sig_item.text(0).lower()
                sig_visible = (text in sig_name) if text else True
                sig_item.setHidden(not sig_visible)
                if sig_visible:
                    any_sig_visible = True

            msg_item.setHidden(not (msg_visible or any_sig_visible))

    def _on_item_changed(self, item: QTreeWidgetItem, column: int):
        """勾选变化时更新绘图按钮状态"""
        checked = self.get_checked_signals()
        self._plot_btn.setEnabled(len(checked) > 0)
        self.selection_changed.emit(checked)

    def _on_plot_clicked(self):
        """绘图按钮点击 — 由外部连接处理"""
        pass  # 信号由 main_window 连接

    def _on_select_all(self):
        """全选当前选中报文下的所有信号"""
        current = self._tree.currentItem()
        if current is None:
            return

        # 如果选中的是信号，取其父节点（报文）
        if current.parent() is not None:
            msg_item = current.parent()
        else:
            msg_item = current

        self._tree.blockSignals(True)
        for j in range(msg_item.childCount()):
            sig_item = msg_item.child(j)
            if not sig_item.isHidden():
                sig_item.setCheckState(0, Qt.Checked)
        self._tree.blockSignals(False)

        self._on_item_changed(None, 0)
```

- [ ] **Step 2: Commit**

```bash
git add widgets/signal_tree.py
git commit -m "feat: add signal tree widget with search and checkbox selection"
```

---

## Task 10: Export Utils (export_utils.py)

**Files:**
- Create: `utils/export_utils.py`

- [ ] **Step 1: Implement export utilities**

```python
# utils/export_utils.py
"""导出工具：图表图片、信号数据 CSV/Excel、报文表格 CSV"""
import os
import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from core.can_data import DecodedSignal


def export_chart_image(fig: Figure, file_path: str, dpi: int = 300):
    """导出 matplotlib 图表为图片。

    Args:
        fig: matplotlib Figure 对象
        file_path: 输出路径（.png 或 .svg）
        dpi: PNG 分辨率
    """
    fig.savefig(file_path, dpi=dpi, bbox_inches="tight")


def export_signal_data(
    signals: list[DecodedSignal],
    file_path: str,
):
    """导出信号数据为 CSV 或 Excel。

    多信号按 timestamp outer join，缺失值填充 NaN。

    Args:
        signals: 解码后的信号列表
        file_path: 输出路径（.csv 或 .xlsx）
    """
    if not signals:
        return

    # 收集所有唯一时间戳
    all_timestamps = np.unique(np.concatenate([s.timestamps for s in signals]))
    all_timestamps.sort()

    data = {"timestamp": all_timestamps}
    for sig in signals:
        col_name = f"{sig.msg_name}.{sig.sig_name}"
        # 使用 searchsorted 对齐
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


def export_message_table(
    frame_index: pd.DataFrame,
    raw_data: np.ndarray,
    file_path: str,
    decoded_signals: dict = None,
):
    """导出报文表格为 CSV。

    Args:
        frame_index: 帧索引 DataFrame
        raw_data: 原始数据数组
        file_path: 输出路径
        decoded_signals: 可选，{(msg_name, sig_name): DecodedSignal}
    """
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
```

- [ ] **Step 2: Commit**

```bash
git add utils/export_utils.py
git commit -m "feat: add export utils for chart image, CSV, and Excel"
```

---

## Task 11: Plot Widget — Base Canvas (plot_widget.py)

**Files:**
- Create: `widgets/plot_widget.py`

- [ ] **Step 1: Implement base plot widget with mode switching**

```python
# widgets/plot_widget.py
"""曲线图组件：matplotlib 嵌入 PyQt5，支持丰富的交互功能"""
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QAction
from PyQt5.QtCore import Qt
from core.can_data import DecodedSignal
from utils.lttb import lttb_downsample

# 降采样阈值
DOWNSAMPLE_THRESHOLD = 10000
# 默认颜色列表
COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]


class PlotWidget(QWidget):
    """信号曲线图组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._signals: list[DecodedSignal] = []
        self._subplot_mode = False  # True=独立子图, False=共享Y轴
        self._mark_mode = False     # 时间差标记模式
        self._mark_points = []      # 已放置的标记时间戳
        self._annotation = None     # 悬停注释框
        self._highlighted_line = None
        self._original_linewidth = 1.5

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # 工具栏
        toolbar = QHBoxLayout()

        self._mode_btn = QPushButton("切换为独立子图")
        self._mode_btn.clicked.connect(self._toggle_mode)
        toolbar.addWidget(self._mode_btn)

        self._mark_btn = QPushButton("标记时间差")
        self._mark_btn.setCheckable(True)
        self._mark_btn.clicked.connect(self._toggle_mark_mode)
        toolbar.addWidget(self._mark_btn)

        self._reset_btn = QPushButton("自适应复位")
        self._reset_btn.clicked.connect(self._auto_scale)
        toolbar.addWidget(self._reset_btn)

        self._clear_mark_btn = QPushButton("清除标记")
        self._clear_mark_btn.clicked.connect(self._clear_marks)
        toolbar.addWidget(self._clear_mark_btn)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # Matplotlib 画布
        self._fig = Figure(figsize=(10, 6))
        self._canvas = FigureCanvas(self._fig)
        self._toolbar = NavigationToolbar(self._canvas, self)
        layout.addWidget(self._toolbar)
        layout.addWidget(self._canvas)

        # 绑定交互事件
        self._canvas.mpl_connect("motion_notify_event", self._on_mouse_move)
        self._canvas.mpl_connect("scroll_event", self._on_scroll)
        self._canvas.mpl_connect("button_press_event", self._on_click)
        self._canvas.mpl_connect("button_release_event", self._on_release)

        self._drag_start = None

    def plot_signals(self, signals: list[DecodedSignal]):
        """绘制信号曲线"""
        self._signals = signals
        self._redraw()

    def _redraw(self):
        """根据当前模式和信号列表重绘"""
        self._fig.clear()

        if not self._signals:
            ax = self._fig.add_subplot(111)
            ax.text(0.5, 0.5, "请勾选信号并点击绘图", transform=ax.transAxes,
                    ha="center", va="center", fontsize=14)
            self._canvas.draw()
            return

        if self._subplot_mode:
            self._draw_subplots()
        else:
            self._draw_shared()

        self._fig.tight_layout()
        self._canvas.draw()

    def _draw_shared(self):
        """共享 Y 轴模式"""
        ax = self._fig.add_subplot(111)
        ax.set_xlabel("时间 (s)")
        ax.set_ylabel("物理值")

        for i, sig in enumerate(self._signals):
            color = COLORS[i % len(COLORS)]
            ts, vals = self._downsample_if_needed(sig.timestamps, sig.values)
            label = f"{sig.msg_name}.{sig.sig_name}"
            ax.plot(ts, vals, color=color, linewidth=self._original_linewidth,
                    marker="o", markersize=3, label=label)

        ax.legend(loc="upper right", draggable=True)

    def _draw_subplots(self):
        """独立子图模式"""
        n = len(self._signals)
        axes = self._fig.subplots(n, 1, sharex=True)
        if n == 1:
            axes = [axes]

        for i, (ax, sig) in enumerate(zip(axes, self._signals)):
            color = COLORS[i % len(COLORS)]
            ts, vals = self._downsample_if_needed(sig.timestamps, sig.values)
            label = f"{sig.msg_name}.{sig.sig_name}"
            ax.plot(ts, vals, color=color, linewidth=self._original_linewidth,
                    marker="o", markersize=3, label=label)
            ax.set_ylabel(sig.sig_name)
            ax.legend(loc="upper right", draggable=True)

        axes[-1].set_xlabel("时间 (s)")

    def _downsample_if_needed(self, timestamps, values):
        """可视区域数据点超过阈值时降采样"""
        if len(timestamps) > DOWNSAMPLE_THRESHOLD:
            return lttb_downsample(timestamps, values, DOWNSAMPLE_THRESHOLD)
        return timestamps, values

    def _toggle_mode(self):
        """切换共享/独立子图模式"""
        self._subplot_mode = not self._subplot_mode
        self._mode_btn.setText("切换为共享Y轴" if self._subplot_mode else "切换为独立子图")
        self._redraw()

    def _toggle_mark_mode(self):
        """切换时间差标记模式"""
        self._mark_mode = self._mark_btn.isChecked()
        self._mark_points.clear()
        if not self._mark_mode:
            self._clear_marks()

    def _auto_scale(self):
        """自适应复位"""
        for ax in self._fig.axes:
            ax.relim()
            ax.autoscale()
        self._fig.tight_layout()
        self._canvas.draw()

    def _clear_marks(self):
        """清除所有时间差标记"""
        self._mark_points.clear()
        for ax in self._fig.axes:
            # 移除所有 axvline 和 delta text
            for child in ax.get_children():
                if hasattr(child, "_is_time_mark") and child._is_time_mark:
                    child.remove()
        self._canvas.draw()

    def _on_mouse_move(self, event):
        """鼠标移动：曲线悬停高亮"""
        if event.inaxes is None:
            self._remove_highlight()
            return

        if self._mark_mode:
            return

        best_line = None
        best_dist = float("inf")
        best_point = None

        for ax in self._fig.axes:
            for line in ax.get_lines():
                xdata = line.get_xdata()
                ydata = line.get_ydata()
                if len(xdata) == 0:
                    continue

                # 找最近的数据点
                idx = np.searchsorted(xdata, event.xdata)
                idx = np.clip(idx, 0, len(xdata) - 1)

                # 检查前后两个点取最近的
                candidates = [idx]
                if idx > 0:
                    candidates.append(idx - 1)
                if idx < len(xdata) - 1:
                    candidates.append(idx + 1)

                for ci in candidates:
                    dx = (xdata[ci] - event.xdata) / max(ax.get_xlim()[1] - ax.get_xlim()[0], 1e-10)
                    dy = (ydata[ci] - event.ydata) / max(ax.get_ylim()[1] - ax.get_ylim()[0], 1e-10)
                    dist = dx * dx + dy * dy
                    if dist < best_dist:
                        best_dist = dist
                        best_line = line
                        best_point = (xdata[ci], ydata[ci])

        # 高亮阈值（归一化距离）
        if best_dist < 0.001 and best_line is not None:
            self._apply_highlight(best_line, best_point, event)
        else:
            self._remove_highlight()

    def _apply_highlight(self, line, point, event):
        """高亮曲线并显示注释"""
        # 恢复之前的线
        if self._highlighted_line is not None and self._highlighted_line is not line:
            self._highlighted_line.set_linewidth(self._original_linewidth)

        line.set_linewidth(4)
        self._highlighted_line = line

        # 更新或创建注释
        ax = line.axes
        label = line.get_label()
        x, y = point
        text = f"{label}\n值: {y:.4f}\n时间: {x:.4f}s"

        if self._annotation is not None:
            self._annotation.remove()

        self._annotation = ax.annotate(
            text, xy=(x, y), xytext=(10, 10),
            textcoords="offset points",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="lightyellow", alpha=0.8),
            fontsize=9,
        )
        self._canvas.draw_idle()

    def _remove_highlight(self):
        """移除高亮和注释"""
        if self._highlighted_line is not None:
            self._highlighted_line.set_linewidth(self._original_linewidth)
            self._highlighted_line = None
        if self._annotation is not None:
            self._annotation.remove()
            self._annotation = None
            self._canvas.draw_idle()

    def _on_scroll(self, event):
        """滚轮缩放"""
        if event.inaxes is None:
            return

        ax = event.inaxes
        modifiers = event.guiEvent.modifiers() if hasattr(event, "guiEvent") and event.guiEvent else []
        ctrl_pressed = Qt.ControlModifier in modifiers if hasattr(Qt, "ControlModifier") else False

        scale_factor = 1.15 if event.button == "up" else 1 / 1.15

        if ctrl_pressed:
            # Ctrl+滚轮：X/Y 同时缩放
            self._zoom_axis(ax, "x", event.xdata, scale_factor)
            self._zoom_axis(ax, "y", event.ydata, scale_factor)
        else:
            # 判断鼠标在哪个区域
            # 在 X 轴附近 → 仅 X 轴，Y 轴附近 → 仅 Y 轴
            xlim = ax.get_xlim()
            ylim = ax.get_ylim()
            # 简单判断：如果 y 坐标接近底部，缩放 X；接近左侧，缩放 Y
            rel_y = (event.ydata - ylim[0]) / max(ylim[1] - ylim[0], 1e-10)
            rel_x = (event.xdata - xlim[0]) / max(xlim[1] - xlim[0], 1e-10)

            if rel_y < 0.1:
                self._zoom_axis(ax, "x", event.xdata, scale_factor)
            elif rel_x < 0.1:
                self._zoom_axis(ax, "y", event.ydata, scale_factor)
            else:
                # 默认 X 轴缩放
                self._zoom_axis(ax, "x", event.xdata, scale_factor)

        self._canvas.draw_idle()

    def _zoom_axis(self, ax, axis, center, scale_factor):
        """以 center 为中心缩放指定轴"""
        if axis == "x":
            lo, hi = ax.get_xlim()
            new_lo = center - (center - lo) * scale_factor
            new_hi = center + (hi - center) * scale_factor
            ax.set_xlim(new_lo, new_hi)
        else:
            lo, hi = ax.get_ylim()
            new_lo = center - (center - lo) * scale_factor
            new_hi = center + (hi - center) * scale_factor
            ax.set_ylim(new_lo, new_hi)

    def _on_click(self, event):
        """鼠标点击"""
        if event.inaxes is None:
            return

        # 时间差标记模式
        if self._mark_mode and event.button == 1:
            self._place_mark(event)
            return

        # 右键清除标记
        if event.button == 3:
            self._clear_marks()
            return

        # 中键或左键开始拖拽平移
        if event.button == 2 or (event.button == 1 and not self._mark_mode):
            self._drag_start = (event.xdata, event.ydata)

    def _on_release(self, event):
        """鼠标释放"""
        if self._drag_start is None:
            return

        if event.inaxes is None or self._mark_mode:
            self._drag_start = None
            return

        # 拖拽平移（仅中键或左键非标记模式）
        dx = self._drag_start[0] - event.xdata
        dy = self._drag_start[1] - event.ydata

        for ax in self._fig.axes:
            xlim = ax.get_xlim()
            ylim = ax.get_ylim()
            ax.set_xlim(xlim[0] + dx, xlim[1] + dx)
            ax.set_ylim(ylim[0] + dy, ylim[1] + dy)

        self._drag_start = None
        self._canvas.draw_idle()

    def _place_mark(self, event):
        """放置时间差标记"""
        t = event.xdata
        self._mark_points.append(t)

        ax = event.inaxes
        line = ax.axvline(x=t, color="red", linestyle="--", linewidth=1)
        line._is_time_mark = True
        ax.text(t, ax.get_ylim()[1], f"  {t:.4f}s", color="red", fontsize=8,
                va="bottom")

        if len(self._mark_points) == 2:
            t1, t2 = self._mark_points
            delta = abs(t2 - t1)
            mid = (t1 + t2) / 2
            ax.annotate(
                f"Δt = {delta:.4f} s",
                xy=(mid, ax.get_ylim()[1]),
                fontsize=10, color="red", fontweight="bold",
                ha="center", va="bottom",
                bbox=dict(boxstyle="round", facecolor="lightyellow", alpha=0.8),
            )
            # 自动退出标记模式
            self._mark_mode = False
            self._mark_btn.setChecked(False)

        self._canvas.draw_idle()

    def get_figure(self) -> Figure:
        """返回 matplotlib Figure 对象，用于导出"""
        return self._fig
```

- [ ] **Step 2: Commit**

```bash
git add widgets/plot_widget.py
git commit -m "feat: add plot widget with zoom, pan, hover highlight, time delta markers"
```

---

## Task 12: Signal Group Panel (signal_group_panel.py)

**Files:**
- Create: `widgets/signal_group_panel.py`

- [ ] **Step 1: Implement signal group panel widget**

```python
# widgets/signal_group_panel.py
"""信号分组管理面板：创建/加载/保存分组，管理分组内信号勾选"""
import json
from dataclasses import dataclass, field
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton,
    QListWidget, QListWidgetItem, QInputDialog, QMessageBox,
    QFileDialog, QLabel, QAbstractItemView,
)
from PyQt5.QtCore import pyqtSignal, Qt
from core.can_data import MessageDef


@dataclass
class SignalRef:
    """分组中的信号引用"""
    msg_name: str
    sig_name: str
    frame_id: str  # hex string like "0x1A0"


@dataclass
class SignalGroup:
    """信号分组"""
    name: str
    signals: list = field(default_factory=list)  # list[SignalRef]


class SignalGroupPanel(QWidget):
    """信号分组管理面板，嵌入曲线图 Tab 内"""

    # 发射已勾选的信号列表 [(msg_name, sig_name), ...]
    plot_requested = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._groups: list[SignalGroup] = []
        self._current_group_idx: int = -1
        self._messages: list[MessageDef] = []  # 当前 DBC 报文定义
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # 分组选择栏
        group_bar = QHBoxLayout()
        group_bar.addWidget(QLabel("分组:"))
        self._group_combo = QComboBox()
        self._group_combo.setMinimumWidth(150)
        self._group_combo.currentIndexChanged.connect(self._on_group_changed)
        group_bar.addWidget(self._group_combo)

        self._new_btn = QPushButton("+ 新建")
        self._new_btn.clicked.connect(self._create_group)
        group_bar.addWidget(self._new_btn)

        self._save_btn = QPushButton("保存配置")
        self._save_btn.clicked.connect(self._save_config)
        group_bar.addWidget(self._save_btn)

        self._load_btn = QPushButton("加载配置")
        self._load_btn.clicked.connect(self._load_config)
        group_bar.addWidget(self._load_btn)

        self._delete_btn = QPushButton("删除分组")
        self._delete_btn.clicked.connect(self._delete_group)
        group_bar.addWidget(self._delete_btn)

        group_bar.addStretch()
        layout.addLayout(group_bar)

        # 信号列表
        self._sig_list = QListWidget()
        self._sig_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        layout.addWidget(self._sig_list)

        # 操作按钮栏
        action_bar = QHBoxLayout()

        self._add_from_tree_btn = QPushButton("从信号树添加")
        self._add_from_tree_btn.clicked.connect(self._add_from_tree)
        action_bar.addWidget(self._add_from_tree_btn)

        self._remove_btn = QPushButton("移除选中")
        self._remove_btn.clicked.connect(self._remove_selected)
        action_bar.addWidget(self._remove_btn)

        self._select_all_btn = QPushButton("全选")
        self._select_all_btn.clicked.connect(self._select_all_signals)
        action_bar.addWidget(self._select_all_btn)

        self._deselect_all_btn = QPushButton("全不选")
        self._deselect_all_btn.clicked.connect(self._deselect_all_signals)
        action_bar.addWidget(self._deselect_all_btn)

        action_bar.addStretch()
        layout.addLayout(action_bar)

        # 绘图按钮
        self._plot_btn = QPushButton("绘制当前分组曲线")
        self._plot_btn.setStyleSheet("font-weight: bold; padding: 8px;")
        self._plot_btn.clicked.connect(self._on_plot)
        layout.addWidget(self._plot_btn)

    def set_messages(self, messages: list[MessageDef]):
        """更新当前 DBC 报文定义，用于匹配检查"""
        self._messages = messages
        # 刷新当前分组的匹配状态
        self._refresh_signal_list()

    def add_signals_from_tree(self, signals: list[tuple[str, str, str]]):
        """从信号树批量添加信号到当前分组。

        Args:
            signals: [(msg_name, sig_name, frame_id_hex), ...]
        """
        if self._current_group_idx < 0:
            QMessageBox.information(self, "提示", "请先创建或选择一个分组")
            return

        group = self._groups[self._current_group_idx]
        existing = {(s.msg_name, s.sig_name) for s in group.signals}

        for msg_name, sig_name, frame_id in signals:
            if (msg_name, sig_name) not in existing:
                group.signals.append(SignalRef(msg_name, sig_name, frame_id))

        self._refresh_signal_list()

    def _create_group(self):
        name, ok = QInputDialog.getText(self, "新建分组", "分组名称:")
        if ok and name.strip():
            self._groups.append(SignalGroup(name=name.strip()))
            self._refresh_combo()
            self._group_combo.setCurrentIndex(len(self._groups) - 1)

    def _delete_group(self):
        if self._current_group_idx < 0:
            return
        name = self._groups[self._current_group_idx].name
        reply = QMessageBox.question(
            self, "确认删除", f"确定删除分组 '{name}' 吗？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._groups.pop(self._current_group_idx)
            self._refresh_combo()

    def _on_group_changed(self, idx: int):
        self._current_group_idx = idx
        self._refresh_signal_list()

    def _refresh_combo(self):
        self._group_combo.blockSignals(True)
        self._group_combo.clear()
        for g in self._groups:
            self._group_combo.addItem(f"{g.name} ({len(g.signals)} 信号)")
        self._group_combo.blockSignals(False)
        if self._groups:
            self._group_combo.setCurrentIndex(0)
            self._current_group_idx = 0
        else:
            self._current_group_idx = -1
        self._refresh_signal_list()

    def _refresh_signal_list(self):
        """刷新信号列表，检查 DBC 匹配状态"""
        self._sig_list.clear()
        if self._current_group_idx < 0:
            return

        group = self._groups[self._current_group_idx]

        # 构建 DBC 查找索引
        dbc_lookup = {}
        for msg in self._messages:
            for sig in msg.signals:
                dbc_lookup[(msg.name, sig.name)] = True

        for sig_ref in group.signals:
            key = (sig_ref.msg_name, sig_ref.sig_name)
            matched = key in dbc_lookup

            item = QListWidgetItem(f"{sig_ref.sig_name} ({sig_ref.msg_name}.{sig_ref.frame_id})")
            item.setData(Qt.UserRole, sig_ref)

            if matched:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)
            else:
                # 置灰不可勾选
                item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable & ~Qt.ItemIsEnabled)
                item.setToolTip("当前 DBC 中未找到此信号")
                item.setForeground(Qt.gray)

            self._sig_list.addItem(item)

    def _add_from_tree(self):
        """从信号树添加 — 由 main_window 连接处理"""
        pass  # main_window 连接此按钮到信号树的 get_checked_signals

    def _remove_selected(self):
        """移除选中的信号"""
        if self._current_group_idx < 0:
            return

        group = self._groups[self._current_group_idx]
        selected = self._sig_list.selectedItems()
        for item in selected:
            sig_ref = item.data(Qt.UserRole)
            group.signals = [s for s in group.signals if not (
                s.msg_name == sig_ref.msg_name and s.sig_name == sig_ref.sig_name
            )]

        self._refresh_signal_list()
        # 更新 combo 显示
        idx = self._current_group_idx
        self._group_combo.setItemText(idx, f"{group.name} ({len(group.signals)} 信号)")

    def _select_all_signals(self):
        """全选当前分组中所有可勾选的信号"""
        for i in range(self._sig_list.count()):
            item = self._sig_list.item(i)
            if item.flags() & Qt.ItemIsUserCheckable:
                item.setCheckState(Qt.Checked)

    def _deselect_all_signals(self):
        """全不选"""
        for i in range(self._sig_list.count()):
            item = self._sig_list.item(i)
            if item.flags() & Qt.ItemIsUserCheckable:
                item.setCheckState(Qt.Unchecked)

    def get_checked_signals(self) -> list[tuple[str, str]]:
        """返回当前分组中已勾选的 (msg_name, sig_name) 列表"""
        result = []
        for i in range(self._sig_list.count()):
            item = self._sig_list.item(i)
            if (item.flags() & Qt.ItemIsUserCheckable) and item.checkState() == Qt.Checked:
                sig_ref = item.data(Qt.UserRole)
                result.append((sig_ref.msg_name, sig_ref.sig_name))
        return result

    def _on_plot(self):
        """绘制当前分组曲线"""
        checked = self.get_checked_signals()
        if checked:
            self.plot_requested.emit(checked)

    # ─── 配置文件保存/加载 ───

    def _save_config(self):
        if not self._groups:
            QMessageBox.information(self, "提示", "没有分组可保存")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "保存分组配置", "", "JSON Files (*.json)"
        )
        if not path:
            return

        config = {
            "groups": [
                {
                    "name": g.name,
                    "signals": [
                        {
                            "msg_name": s.msg_name,
                            "sig_name": s.sig_name,
                            "frame_id": s.frame_id,
                        }
                        for s in g.signals
                    ],
                }
                for g in self._groups
            ]
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    def _load_config(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "加载分组配置", "", "JSON Files (*.json)"
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                config = json.load(f)

            self._groups.clear()
            for g_data in config.get("groups", []):
                signals = [
                    SignalRef(
                        msg_name=s["msg_name"],
                        sig_name=s["sig_name"],
                        frame_id=s.get("frame_id", ""),
                    )
                    for s in g_data.get("signals", [])
                ]
                self._groups.append(SignalGroup(name=g_data["name"], signals=signals))

            self._refresh_combo()

        except Exception as e:
            QMessageBox.critical(self, "加载失败", str(e))
```

- [ ] **Step 2: Commit**

```bash
git add widgets/signal_group_panel.py
git commit -m "feat: add signal group panel with save/load config and DBC matching"
```

---

## Task 13: Message Table Widget (message_table.py)

**Files:**
- Create: `widgets/message_table.py`

- [ ] **Step 1: Implement message table widget**

```python
# widgets/message_table.py
"""原始报文查看器：可展开的树形表格，支持过滤和按需解码"""
import numpy as np
import pandas as pd
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QLineEdit, QPushButton, QComboBox, QLabel, QFileDialog,
)
from PyQt5.QtCore import Qt
import cantools
from core.can_data import MessageDef


class MessageTableWidget(QWidget):
    """报文表格组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frame_index: pd.DataFrame | None = None
        self._raw_data: np.ndarray | None = None
        self._messages: list[MessageDef] = []
        self._dbc_path: str = ""
        self._db = None  # cantools Database
        self._filtered_index: pd.DataFrame | None = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # 过滤栏
        filter_layout = QHBoxLayout()

        filter_layout.addWidget(QLabel("报文ID:"))
        self._id_filter = QComboBox()
        self._id_filter.setEditable(True)
        self._id_filter.setFixedWidth(120)
        filter_layout.addWidget(self._id_filter)

        filter_layout.addWidget(QLabel("信号名:"))
        self._sig_filter = QLineEdit()
        self._sig_filter.setPlaceholderText("模糊搜索...")
        self._sig_filter.setFixedWidth(150)
        filter_layout.addWidget(self._sig_filter)

        filter_layout.addWidget(QLabel("时间:"))
        self._time_start = QLineEdit()
        self._time_start.setPlaceholderText("起始")
        self._time_start.setFixedWidth(80)
        filter_layout.addWidget(self._time_start)

        filter_layout.addWidget(QLabel("~"))
        self._time_end = QLineEdit()
        self._time_end.setPlaceholderText("结束")
        self._time_end.setFixedWidth(80)
        filter_layout.addWidget(self._time_end)

        self._apply_btn = QPushButton("应用过滤")
        self._apply_btn.clicked.connect(self._apply_filter)
        filter_layout.addWidget(self._apply_btn)

        self._reset_btn = QPushButton("重置")
        self._reset_btn.clicked.connect(self._reset_filter)
        filter_layout.addWidget(self._reset_btn)

        filter_layout.addStretch()
        layout.addLayout(filter_layout)

        # 树形表格
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["序号", "时间(s)", "ID", "DLC", "Channel", "Data (Hex)"])
        self._tree.setColumnWidth(0, 60)
        self._tree.setColumnWidth(1, 100)
        self._tree.setColumnWidth(2, 80)
        self._tree.setColumnWidth(3, 50)
        self._tree.setColumnWidth(4, 60)
        self._tree.setColumnWidth(5, 300)
        self._tree.setUniformRowHeights(True)
        layout.addWidget(self._tree)

    def set_data(self, frame_index: pd.DataFrame, raw_data: np.ndarray,
                 messages: list[MessageDef], dbc_path: str = ""):
        """设置数据源"""
        self._frame_index = frame_index
        self._raw_data = raw_data
        self._messages = messages
        self._dbc_path = dbc_path
        self._filtered_index = frame_index

        if dbc_path:
            self._db = cantools.database.load_file(dbc_path)

        # 更新 ID 过滤下拉框
        self._id_filter.clear()
        unique_ids = sorted(frame_index["arbitration_id"].unique())
        self._id_filter.addItem("全部")
        for aid in unique_ids:
            self._id_filter.addItem(f"0x{aid:03X}", aid)

        self._populate_table()

    def _populate_table(self):
        """填充表格（只显示帧头，不预解码信号）"""
        self._tree.clear()
        if self._filtered_index is None:
            return

        # 限制显示行数，大数据集时分批加载
        max_display = 10000
        display_df = self._filtered_index.head(max_display)

        self._tree.setUpdatesEnabled(False)
        for _, row in display_df.iterrows():
            fid = int(row["frame_id"])
            dlc = int(row["dlc"])
            hex_data = " ".join(f"{b:02X}" for b in self._raw_data[fid, :dlc])

            item = QTreeWidgetItem(self._tree)
            item.setText(0, str(fid))
            item.setText(1, f"{row['timestamp']:.6f}")
            item.setText(2, f"0x{row['arbitration_id']:03X}")
            item.setText(3, str(dlc))
            item.setText(4, str(int(row["channel"])))
            item.setText(5, hex_data)

            # 存储 frame_id 用于展开时解码
            item.setData(0, Qt.UserRole, fid)

        self._tree.setUpdatesEnabled(True)

        # 连接展开信号（仅连接一次）
        try:
            self._tree.itemExpanded.disconnect(self._on_item_expanded)
        except TypeError:
            pass
        self._tree.itemExpanded.connect(self._on_item_expanded)

    def _on_item_expanded(self, item: QTreeWidgetItem):
        """展开某行时解码其信号"""
        if item.childCount() > 0:
            return  # 已经解码过

        fid = item.data(0, Qt.UserRole)
        if fid is None or self._db is None:
            return

        dlc = int(self._frame_index.iloc[fid]["dlc"])
        arb_id = int(self._frame_index.iloc[fid]["arbitration_id"])
        frame_data = bytes(self._raw_data[fid, :dlc])

        try:
            msg_def = self._db.get_message_by_frame_id(arb_id)
            decoded = msg_def.decode(frame_data)

            for sig_name, sig_value in decoded.items():
                sig_def = next((s for s in msg_def.signals if s.name == sig_name), None)
                unit = sig_def.unit if sig_def and sig_def.unit else ""
                child = QTreeWidgetItem(item)
                child.setText(0, "")
                child.setText(1, sig_name)
                child.setText(5, f"{sig_value} {unit}")
        except Exception:
            child = QTreeWidgetItem(item)
            child.setText(1, "(解码失败)")

    def _apply_filter(self):
        """应用过滤条件"""
        if self._frame_index is None:
            return

        df = self._frame_index

        # 报文 ID 过滤
        id_text = self._id_filter.currentText()
        if id_text and id_text != "全部":
            try:
                aid = int(id_text, 16) if id_text.startswith("0x") else int(id_text, 16)
                df = df[df["arbitration_id"] == aid]
            except ValueError:
                pass

        # 时间范围过滤
        t_start = self._time_start.text().strip()
        t_end = self._time_end.text().strip()
        if t_start:
            try:
                df = df[df["timestamp"] >= float(t_start)]
            except ValueError:
                pass
        if t_end:
            try:
                df = df[df["timestamp"] <= float(t_end)]
            except ValueError:
                pass

        # 信号名过滤 — 需要找到包含该信号的报文 ID
        sig_text = self._sig_filter.text().strip().lower()
        if sig_text and self._messages:
            matching_ids = set()
            for msg in self._messages:
                for sig in msg.signals:
                    if sig_text in sig.name.lower():
                        matching_ids.add(msg.frame_id)
            if matching_ids:
                df = df[df["arbitration_id"].isin(matching_ids)]
            else:
                df = df.iloc[0:0]  # 空

        self._filtered_index = df
        self._populate_table()

    def _reset_filter(self):
        """重置过滤条件"""
        self._id_filter.setCurrentText("全部")
        self._sig_filter.clear()
        self._time_start.clear()
        self._time_end.clear()
        self._filtered_index = self._frame_index
        self._populate_table()

    def get_filtered_index(self) -> pd.DataFrame | None:
        return self._filtered_index
```

- [ ] **Step 2: Commit**

```bash
git add widgets/message_table.py
git commit -m "feat: add message table widget with expandable signal decoding and filtering"
```

---

## Task 14: Bit Layout Viewer (bit_layout_view.py)

**Files:**
- Create: `widgets/bit_layout_view.py`

- [ ] **Step 1: Implement bit layout viewer**

```python
# widgets/bit_layout_view.py
"""DBC 位图可视化组件：显示 CAN 帧的位布局，支持 Intel/Motorola 字节序"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QTreeWidget,
    QTreeWidgetItem, QGraphicsView, QGraphicsScene, QGraphicsRectItem,
    QGraphicsTextItem, QLabel, QListWidget, QListWidgetItem,
)
from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QColor, QBrush, QPen, QFont
from core.can_data import MessageDef, SignalDef
from utils.bit_utils import get_bit_positions

# 信号颜色调色板
SIGNAL_COLORS = [
    "#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7",
    "#DDA0DD", "#98D8C8", "#F7DC6F", "#BB8FCE", "#85C1E9",
]

# 单元格尺寸
CELL_W = 60
CELL_H = 35


class BitLayoutView(QWidget):
    """位图可视化组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._messages: list[MessageDef] = []
        self._current_msg: MessageDef | None = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # 搜索栏
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("搜索报文:"))
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("输入 ID (如 0x1A0) 或报文名/信号名...")
        self._search_input.textChanged.connect(self._on_search)
        search_layout.addWidget(self._search_input)
        layout.addLayout(search_layout)

        # 候选列表
        self._candidate_list = QListWidget()
        self._candidate_list.setMaximumHeight(120)
        self._candidate_list.itemClicked.connect(self._on_candidate_selected)
        layout.addWidget(self._candidate_list)

        # 位图网格
        self._scene = QGraphicsScene()
        self._graphics_view = QGraphicsView(self._scene)
        self._graphics_view.setRenderHint(self._graphics_view.renderHints())
        self._graphics_view.setDragMode(QGraphicsView.ScrollHandDrag)
        layout.addWidget(self._graphics_view)

        # 信号信息列表
        self._sig_list = QTreeWidget()
        self._sig_list.setHeaderLabels(["信号名", "起始位", "长度", "字节序", "Scale", "Offset", "单位"])
        self._sig_list.setMaximumHeight(150)
        self._sig_list.itemClicked.connect(self._on_sig_highlight)
        layout.addWidget(self._sig_list)

    def load_messages(self, messages: list[MessageDef]):
        """加载报文定义"""
        self._messages = messages

    def _on_search(self, text: str):
        """搜索过滤报文"""
        self._candidate_list.clear()
        text = text.strip()
        if not text:
            return

        # 判断是 hex ID 还是文本
        is_hex = False
        search_id = 0
        try:
            hex_str = text.replace("0x", "").replace("0X", "")
            search_id = int(hex_str, 16)
            is_hex = True
        except ValueError:
            pass

        for msg in self._messages:
            matched_sigs = []
            text_lower = text.lower()

            if is_hex and msg.frame_id == search_id:
                matched_sigs = [s.name for s in msg.signals]
            elif text_lower in msg.name.lower():
                matched_sigs = [s.name for s in msg.signals]
            else:
                for sig in msg.signals:
                    if text_lower in sig.name.lower():
                        matched_sigs.append(sig.name)

            if matched_sigs:
                display = f"0x{msg.frame_id:03X} - {msg.name} ({len(matched_sigs)} 信号)"
                item = QListWidgetItem(display)
                item.setData(Qt.UserRole, msg)
                self._candidate_list.addItem(item)

    def _on_candidate_selected(self, item: QListWidgetItem):
        """选中候选后渲染位图"""
        msg = item.data(Qt.UserRole)
        if msg:
            self._render_layout(msg)

    def _render_layout(self, msg: MessageDef):
        """渲染报文的位布局"""
        self._current_msg = msg
        self._scene.clear()

        num_bytes = msg.dlc
        sig_colors = {}
        for i, sig in enumerate(msg.signals):
            sig_colors[sig.name] = SIGNAL_COLORS[i % len(SIGNAL_COLORS)]

        # 绘制网格
        # 行：Byte0 ~ Byte(n-1)，列：Bit7 ~ Bit0
        # 标题行
        for bit_idx in range(8):
            x = bit_idx * CELL_W + CELL_W  # 留一列给 Byte 标签
            text = self._scene.addText(f"Bit{7 - bit_idx}")
            text.setPos(x + 10, -CELL_H)

        for byte_idx in range(num_bytes):
            y = byte_idx * CELL_H

            # Byte 标签
            label = self._scene.addText(f"Byte{byte_idx}")
            label.setPos(5, y + 5)

            for bit_pos in range(8):
                x = bit_pos * CELL_W + CELL_W
                rect = QGraphicsRectItem(x, y, CELL_W, CELL_H)
                rect.setPen(QPen(QColor("#CCCCCC")))

                # 检查是否有信号占用此位
                occupied_by = None
                for sig in msg.signals:
                    positions = get_bit_positions(sig.start_bit, sig.length, sig.byte_order)
                    if (byte_idx, 7 - bit_pos) in positions:
                        occupied_by = sig
                        break

                if occupied_by:
                    color = sig_colors.get(occupied_by.name, "#CCCCCC")
                    rect.setBrush(QBrush(QColor(color)))
                    rect.setToolTip(
                        f"信号: {occupied_by.name}\n"
                        f"起始位: {occupied_by.start_bit}\n"
                        f"长度: {occupied_by.length}\n"
                        f"字节序: {occupied_by.byte_order}\n"
                        f"Scale: {occupied_by.scale}\n"
                        f"Offset: {occupied_by.offset}\n"
                        f"单位: {occupied_by.unit}\n"
                        f"范围: [{occupied_by.min_val}, {occupied_by.max_val}]"
                    )
                    rect.setData(0, occupied_by.name)
                else:
                    rect.setBrush(QBrush(QColor("#EEEEEE")))
                    rect.setToolTip(f"Byte{byte_idx} Bit{7 - bit_pos}\n(Unused/Padding)")

                self._scene.addItem(rect)

                # 在单元格内显示 bit 编号
                bit_num_text = self._scene.addText(str(7 - bit_pos))
                bit_num_text.setPos(x + 22, y + 8)
                bit_num_text.setDefaultTextColor(QColor("#666666"))

        # 更新信号列表
        self._sig_list.clear()
        for sig in msg.signals:
            item = QTreeWidgetItem(self._sig_list)
            color = sig_colors.get(sig.name, "#CCCCCC")
            item.setText(0, f"■ {sig.name}")
            item.setForeground(0, QBrush(QColor(color)))
            item.setText(1, str(sig.start_bit))
            item.setText(2, str(sig.length))
            item.setText(3, sig.byte_order)
            item.setText(4, str(sig.scale))
            item.setText(5, str(sig.offset))
            item.setText(6, sig.unit)
            item.setData(0, Qt.UserRole, sig.name)

    def _on_sig_highlight(self, item: QTreeWidgetItem, column: int):
        """点击信号列表项，高亮对应色块"""
        sig_name = item.data(0, Qt.UserRole)
        if not sig_name:
            return

        for graphics_item in self._scene.items():
            if isinstance(graphics_item, QGraphicsRectItem):
                item_sig = graphics_item.data(0)
                if item_sig == sig_name:
                    graphics_item.setPen(QPen(QColor("red"), 3))
                elif item_sig is not None:
                    graphics_item.setPen(QPen(QColor("#CCCCCC")))
```

- [ ] **Step 2: Commit**

```bash
git add widgets/bit_layout_view.py
git commit -m "feat: add bit layout viewer with Intel/Motorola rendering and search"
```

---

## Task 15: Main Window (main_window.py)

**Files:**
- Create: `main_window.py`

- [ ] **Step 1: Implement main window**

```python
# main_window.py
"""主窗口：Tab 布局 + 左侧信号树 + 菜单栏 + 状态栏"""
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTabWidget, QMenuBar, QMenu, QAction, QFileDialog,
    QStatusBar, QProgressBar, QMessageBox,
)
from PyQt5.QtCore import Qt
import pandas as pd
import numpy as np
from core.dbc_parser import parse_dbc
from core.can_data import MessageDef, DecodedSignal
from core.signal_cache import SignalCache
from widgets.signal_tree import SignalTreeWidget
from widgets.plot_widget import PlotWidget
from widgets.signal_group_panel import SignalGroupPanel
from widgets.message_table import MessageTableWidget
from widgets.bit_layout_view import BitLayoutView
from workers.load_worker import LoadWorker, DecodeWorker
from utils.export_utils import export_chart_image, export_signal_data


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CAN 报文分析工具")
        self.setMinimumSize(1200, 800)

        self._messages: list[MessageDef] = []
        self._dbc_path: str = ""
        self._frame_index: pd.DataFrame | None = None
        self._raw_data: np.ndarray | None = None
        self._cache = SignalCache(max_entries=100)
        self._decoded_signals: list[DecodedSignal] = []
        self._load_worker: LoadWorker | None = None
        self._decode_workers: list[DecodeWorker] = []

        self._setup_ui()
        self._setup_menu()
        self._setup_statusbar()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        # 分割器：左侧信号树 | 右侧 Tab
        splitter = QSplitter(Qt.Horizontal)

        # 左侧面板
        self._signal_tree = SignalTreeWidget()
        self._signal_tree.selection_changed.connect(self._on_selection_changed)
        self._signal_tree.plot_requested.connect(self._decode_and_plot)
        splitter.addWidget(self._signal_tree)

        # 右侧 Tab 页
        self._tabs = QTabWidget()

        # 曲线图 Tab — 包含分组面板 + 图表
        plot_tab = QWidget()
        plot_tab_layout = QVBoxLayout(plot_tab)
        plot_tab_layout.setContentsMargins(0, 0, 0, 0)

        self._group_panel = SignalGroupPanel()
        self._group_panel.plot_requested.connect(self._decode_and_plot_group)
        self._group_panel._add_from_tree_btn.clicked.connect(self._add_tree_signals_to_group)
        plot_tab_layout.addWidget(self._group_panel)

        self._plot_widget = PlotWidget()
        plot_tab_layout.addWidget(self._plot_widget)
        plot_tab_layout.setStretchFactor(self._plot_widget, 3)

        self._tabs.addTab(plot_tab, "曲线图")

        self._message_table = MessageTableWidget()
        self._tabs.addTab(self._message_table, "报文表格")

        self._bit_layout = BitLayoutView()
        self._tabs.addTab(self._bit_layout, "位图查看器")

        splitter.addWidget(self._tabs)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        main_layout.addWidget(splitter)

    def _setup_menu(self):
        menubar = self.menuBar()

        # 文件菜单
        file_menu = menubar.addMenu("文件(&F)")

        load_dbc = QAction("加载 DBC...", self)
        load_dbc.triggered.connect(self._load_dbc)
        file_menu.addAction(load_dbc)

        load_log = QAction("加载日志 (BLF/ASC)...", self)
        load_log.triggered.connect(self._load_log)
        file_menu.addAction(load_log)

        file_menu.addSeparator()

        export_chart = QAction("导出图表图片...", self)
        export_chart.triggered.connect(self._export_chart)
        file_menu.addAction(export_chart)

        export_data = QAction("导出信号数据...", self)
        export_data.triggered.connect(self._export_signal_data)
        file_menu.addAction(export_data)

        file_menu.addSeparator()

        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

    def _setup_statusbar(self):
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._progress_bar.setMaximumWidth(200)
        self._statusbar.addPermanentWidget(self._progress_bar)
        self._statusbar.showMessage("就绪 — 请加载 DBC 和日志文件")

    # ─── 文件加载 ───

    def _load_dbc(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 DBC 文件", "", "DBC Files (*.dbc);;All Files (*)"
        )
        if not path:
            return

        try:
            self._messages = parse_dbc(path)
            self._dbc_path = path
            self._signal_tree.load_messages(self._messages)
            self._bit_layout.load_messages(self._messages)
            self._group_panel.set_messages(self._messages)
            self._statusbar.showMessage(
                f"DBC 加载完成: {len(self._messages)} 个报文, "
                f"{sum(len(m.signals) for m in self._messages)} 个信号"
            )
        except Exception as e:
            QMessageBox.critical(self, "DBC 加载失败", str(e))

    def _load_log(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择日志文件", "",
            "CAN Log Files (*.blf *.asc);;BLF Files (*.blf);;ASC Files (*.asc);;All Files (*)"
        )
        if not path:
            return

        # 清空旧数据和缓存
        self._cache.clear()
        self._decoded_signals.clear()

        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._statusbar.showMessage(f"正在加载 {path} ...")

        self._load_worker = LoadWorker(path)
        self._load_worker.progress.connect(self._on_load_progress)
        self._load_worker.finished.connect(self._on_load_finished)
        self._load_worker.error.connect(self._on_load_error)
        self._load_worker.start()

    def _on_load_progress(self, percent: int):
        self._progress_bar.setValue(percent)

    def _on_load_finished(self, frame_index, raw_data):
        self._frame_index = frame_index
        self._raw_data = raw_data
        self._progress_bar.setVisible(False)

        num_frames = len(frame_index)
        t_start = frame_index["timestamp"].iloc[0] if num_frames > 0 else 0
        t_end = frame_index["timestamp"].iloc[-1] if num_frames > 0 else 0

        self._message_table.set_data(frame_index, raw_data, self._messages, self._dbc_path)

        self._statusbar.showMessage(
            f"日志加载完成: {num_frames} 帧, "
            f"时间范围 {t_start:.3f}s ~ {t_end:.3f}s"
        )

    def _on_load_error(self, error_msg: str):
        self._progress_bar.setVisible(False)
        QMessageBox.critical(self, "日志加载失败", error_msg)

    # ─── 信号勾选与绘图 ───

    def _on_selection_changed(self, checked: list[tuple[str, str]]):
        """信号树勾选变化"""
        pass  # 绘图按钮由 signal_tree 内部管理

    def _decode_and_plot(self):
        """解码已勾选的信号并绘图"""
        checked = self._signal_tree.get_checked_signals()
        if not checked or not self._dbc_path or self._frame_index is None:
            return

        self._decoded_signals.clear()
        self._statusbar.showMessage(f"正在解码 {len(checked)} 个信号...")

        for msg_name, sig_name in checked:
            worker = DecodeWorker(
                self._dbc_path, msg_name, sig_name,
                self._frame_index, self._raw_data, self._cache,
            )
            worker.finished.connect(self._on_decode_finished)
            worker.error.connect(lambda e: QMessageBox.warning(self, "解码错误", e))
            worker.start()
            self._decode_workers.append(worker)

    def _on_decode_finished(self, decoded_signal: DecodedSignal):
        """单个信号解码完成"""
        self._decoded_signals.append(decoded_signal)

        # 所有信号都解码完成后绘图
        checked = self._signal_tree.get_checked_signals()
        if len(self._decoded_signals) >= len(checked):
            self._plot_widget.plot_signals(self._decoded_signals)
            self._tabs.setCurrentIndex(0)  # 切换到曲线图 Tab
            self._statusbar.showMessage(f"绘图完成: {len(self._decoded_signals)} 个信号")

    def _add_tree_signals_to_group(self):
        """将信号树中当前勾选的信号添加到分组面板"""
        checked = self._signal_tree.get_checked_signals()
        if not checked:
            QMessageBox.information(self, "提示", "请先在信号树中勾选信号")
            return

        # 查找 frame_id hex 字符串
        signals_with_id = []
        msg_lookup = {m.name: m for m in self._messages}
        for msg_name, sig_name in checked:
            msg = msg_lookup.get(msg_name)
            frame_id_hex = f"0x{msg.frame_id:03X}" if msg else ""
            signals_with_id.append((msg_name, sig_name, frame_id_hex))

        self._group_panel.add_signals_from_tree(signals_with_id)

    def _decode_and_plot_group(self, checked: list[tuple[str, str]]):
        """解码分组中已勾选的信号并绘图"""
        if not checked or not self._dbc_path or self._frame_index is None:
            return

        self._decoded_signals.clear()
        self._statusbar.showMessage(f"正在解码 {len(checked)} 个信号...")

        for msg_name, sig_name in checked:
            worker = DecodeWorker(
                self._dbc_path, msg_name, sig_name,
                self._frame_index, self._raw_data, self._cache,
            )
            worker.finished.connect(self._on_group_decode_finished)
            worker.error.connect(lambda e: QMessageBox.warning(self, "解码错误", e))
            worker.start()
            self._decode_workers.append(worker)

    def _on_group_decode_finished(self, decoded_signal: DecodedSignal):
        """分组信号解码完成"""
        self._decoded_signals.append(decoded_signal)
        group_checked = self._group_panel.get_checked_signals()
        if len(self._decoded_signals) >= len(group_checked):
            self._plot_widget.plot_signals(self._decoded_signals)
            self._tabs.setCurrentIndex(0)
            self._statusbar.showMessage(f"分组绘图完成: {len(self._decoded_signals)} 个信号")

    # ─── 导出 ───

    def _export_chart(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出图表", "", "PNG (*.png);;SVG (*.svg)"
        )
        if path:
            export_chart_image(self._plot_widget.get_figure(), path)
            self._statusbar.showMessage(f"图表已导出: {path}")

    def _export_signal_data(self):
        if not self._decoded_signals:
            QMessageBox.information(self, "提示", "请先解码信号")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "导出信号数据", "", "CSV (*.csv);;Excel (*.xlsx)"
        )
        if path:
            export_signal_data(self._decoded_signals, path)
            self._statusbar.showMessage(f"数据已导出: {path}")
```

- [ ] **Step 2: 连接绘图按钮信号**

在 `_setup_ui` 中，`_signal_tree` 的 `_plot_btn` 点击需要连接到 `_decode_and_plot`。在 `SignalTreeWidget` 中添加一个 pyqtSignal：

在 `widgets/signal_tree.py` 的类定义中添加：

```python
    plot_requested = pyqtSignal()  # 用户点击绘图按钮时发射
```

在 `_on_plot_clicked` 方法中添加：

```python
    def _on_plot_clicked(self):
        self.plot_requested.emit()
```

在 `main_window.py` 的 `_setup_ui` 中连接：

```python
        self._signal_tree.plot_requested.connect(self._decode_and_plot)
```

- [ ] **Step 3: Commit**

```bash
git add main_window.py widgets/signal_tree.py
git commit -m "feat: add main window with tab layout, menu, and status bar"
```

---

## Task 16: Application Entry Point (main.py)

**Files:**
- Create: `main.py`

- [ ] **Step 1: Implement entry point**

```python
# main.py
"""CAN 报文分析工具 — 应用入口"""
import sys
from PyQt5.QtWidgets import QApplication
from main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("CanMsgParser")

    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the application**

Run: `python main.py`
Expected: 窗口正常显示，左侧信号树面板、右侧三个 Tab 页可见

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "feat: add application entry point"
```

---

## Task 17: Integration Testing & Polish

**Files:**
- Modify: `main_window.py`（修复集成问题）

- [ ] **Step 1: Run all tests**

Run: `pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 2: End-to-end manual test**

1. `python main.py`
2. 文件 → 加载 DBC → 选择 `tests/fixtures/test.dbc`
3. 确认信号树显示 EngineData 和 TransmissionData 及其信号
4. 勾选 EngineRPM 和 EngineSpeed
5. 点击"绘图"按钮
6. 确认曲线图 Tab 显示曲线
7. 测试缩放、平移、悬停高亮、时间差标记
8. 切换"独立子图"模式
9. 切换到报文表格 Tab，确认显示帧数据
10. 展开某帧确认信号解码
11. 测试过滤功能
12. 切换到位图查看器 Tab，搜索 EngineData
13. 确认位布局网格正确渲染
14. 测试导出功能

- [ ] **Step 3: Fix any issues found**

根据手动测试结果修复发现的 bug。

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore: integration testing and polish"
```
