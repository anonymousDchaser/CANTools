# CAN 报文分析工具 — 设计规格书

**日期**: 2026-06-15
**项目名称**: CanMsgParser
**技术栈**: Python 3 + PyQt5 + Matplotlib + cantools + python-can + pandas

---

## 1. 项目概述

开发一个类似 Vector CANoe 的车载 CAN 报文分析桌面工具，支持 DBC 解析、BLF/ASC 日志加载、信号曲线绘制、原始报文查看、位图可视化等功能。

### 1.1 核心约束

- 支持大型文件（GB 级，百万帧以上）
- 同时支持经典 CAN（8 字节）和 CAN FD（8~64 字节）
- Tab 页签式布局 + 左侧信号树
- 多线程架构保证 UI 响应性

---

## 2. 架构方案

**选定方案**：多线程 + Qt Signal-Slot

- QThread 后台线程负责文件加载和信号解码
- 解码结果通过 LRU 缓存存储
- Qt signal-slot 线程安全更新 UI
- 架构上预留可替换的 Loader 接口，方便后续优化

---

## 3. 模块结构

```
CanMsgParser/
├── main.py                    # 入口，创建 QApplication 和主窗口
├── main_window.py             # 主窗口，Tab 布局 + 左侧信号树
├── core/
│   ├── __init__.py
│   ├── dbc_parser.py          # DBC 文件解析封装（cantools）
│   ├── log_loader.py          # BLF/ASC 文件加载器
│   ├── signal_cache.py        # 解码结果 LRU 缓存
│   └── can_data.py            # 统一数据模型
├── widgets/
│   ├── __init__.py
│   ├── signal_tree.py         # 信号树 + 模糊搜索
│   ├── plot_widget.py         # 曲线图（matplotlib 嵌入 + 交互）
│   ├── message_table.py       # 原始报文表格（可展开树形）
│   └── bit_layout_view.py     # 位图可视化
├── utils/
│   ├── __init__.py
│   ├── bit_utils.py           # Intel/Motorola 位布局计算工具
│   └── export_utils.py        # 导出图片/CSV 工具
├── workers/
│   ├── __init__.py
│   └── load_worker.py         # QThread 文件加载/解码工作线程
└── requirements.txt
```

---

## 4. 数据管道

### 4.1 文件加载流程

```
日志文件（BLF/ASC）
    │
    ▼ QThread 后台线程
┌──────────────────────────────┐
│ 第一阶段：扫描建索引          │
│  - 遍历所有帧，提取：         │
│    timestamp, arbitration_id, │
│    dlc, channel, is_fd        │
│  - 存入 pandas DataFrame      │
│    (frame_index)              │
│  - 原始数据存入 numpy 2D 数组 │
│    raw_data[frame_id, :dlc]   │
│  - 通过 Qt signal 发送进度%   │
├──────────────────────────────┐
│ 第二阶段：按需解码（主线程触发）│
│  - 用户勾选信号或展开帧时触发  │
│  - 从 frame_index 筛选对应 ID │
│    的帧，逐帧用 cantools 解码  │
│  - 解码结果：                  │
│    timestamps[] + values[]    │
│  - 存入 signal_cache          │
└──────────────────────────────┘
```

### 4.2 BLF/ASC 统一接口

- 使用 `python-can` 的 `can.BLFReader` 和 `can.ASCReader`，两者均实现迭代器接口
- `log_loader.py` 根据文件扩展名自动选择 reader，对外暴露统一 API
- CAN FD 帧通过 `is_fd` 标志和 `dlc` 字段区分

### 4.3 原始数据存储策略

扫描阶段将所有帧的原始数据加载到内存，使用 numpy 2D 数组存储：

```python
raw_data: np.ndarray   # shape=(num_frames, max_dlc), dtype=uint8
```

内存开销估算：
- 100 万帧经典 CAN：100万 × 8 字节 ≈ 8MB
- 100 万帧 CAN FD（混合）：100万 × 64 字节 ≈ 64MB

frame_index DataFrame 只保留元数据，解码时从 `raw_data[frame_id]` 取字节。

### 4.4 帧索引 DataFrame 结构

| 列名 | 类型 | 说明 |
|---|---|---|
| frame_id | int64 | 帧序号 |
| timestamp | float64 | 时间戳（秒） |
| arbitration_id | uint32 | CAN ID |
| dlc | uint8 | 数据长度 |
| channel | int | 通道号 |
| is_fd | bool | 是否 CAN FD |

### 4.5 解码缓存（`signal_cache.py`）

```python
class SignalCache:
    """LRU 缓存，key = (message_name, signal_name)，value = (timestamps, values)"""
    def __init__(self, max_entries=100):
        ...
    def get_or_decode(self, msg_name, sig_name) -> (np.ndarray, np.ndarray):
        ...
```

- 缓存容量 100 个信号
- 缓存命中直接返回 numpy 数组
- 切换 DBC 或日志文件时清空缓存

### 4.6 数据模型（`can_data.py`）

```python
@dataclass
class SignalDef:        # DBC 中的信号定义
    name: str
    start_bit: int
    length: int
    byte_order: str     # 'intel' | 'motorola'
    scale: float
    offset: float
    unit: str
    min_val: float
    max_val: float

@dataclass
class MessageDef:       # DBC 中的报文定义
    frame_id: int
    name: str
    dlc: int
    is_fd: bool
    signals: list[SignalDef]

@dataclass
class DecodedSignal:    # 解码后的信号数据
    msg_name: str
    sig_name: str
    timestamps: np.ndarray   # float64, 秒
    values: np.ndarray       # float64, 物理值
```

---

## 5. UI 布局

### 5.1 主窗口结构

```
┌─────────────────────────────────────────────────────┐
│  菜单栏：文件(F)  视图(V)  工具(T)  帮助(H)          │
├────────────┬────────────────────────────────────────┤
│            │  [曲线图]  [报文表格]  [位图查看器]       │
│  信号树     │ ┌──────────────────────────────────────┐│
│  (左侧面板) │ │                                      ││
│            │ │        当前 Tab 页内容                 ││
│ ┌────────┐ │ │                                      ││
│ │搜索框   │ │ │                                      ││
│ ├────────┤ │ │                                      ││
│ │报文-信号│ │ │                                      ││
│ │树形列表 │ │ │                                      ││
│ │☑勾选框 │ │ │                                      ││
│ │        │ │ │                                      ││
│ ├────────┤ │ └──────────────────────────────────────┘│
│ │绘图按钮 │ │                                        │
│ │导出按钮 │ │                                        │
│ └────────┘ │                                        │
├────────────┴────────────────────────────────────────┤
│  状态栏：文件信息 | 帧数 | 时间范围 | 解码进度         │
└─────────────────────────────────────────────────────┘
```

### 5.2 文件加载流程

1. 菜单栏 → 文件 → 加载 DBC / 加载日志（BLF/ASC）
2. 弹出文件选择对话框
3. 创建 `LoadWorker`（QThread），传入文件路径
4. 状态栏显示进度条和百分比
5. 加载完成后，信号树填充数据，状态栏显示统计信息

### 5.3 信号树交互

- 树节点结构：`报文名 (0xID)` → `信号名`
- 每个信号节点带 checkbox
- 勾选信号后，底部"绘图"按钮可用
- 支持 Ctrl+A 全选当前报文下所有信号
- 搜索框实时模糊过滤报文和信号名称

---

## 6. 曲线图组件（`plot_widget.py`）

### 6.1 基础绘图

- 继承 `FigureCanvasQTAgg`，嵌入 PyQt5
- X 轴：时间戳（秒，相对起始时间）
- Y 轴：信号物理值（DBC 解码后的值）
- 每个信号用不同颜色，折线连接 + 圆点标记（标记报文发送时刻）
- 图例可拖拽，显示信号名 + 单位

### 6.2 两种图表模式

**模式 1：独立子图**
- 每个信号一个 subplot，共享 X 轴（时间轴）
- 各 subplot 独立 Y 轴，自动适配值域
- 适合值域差异大的信号对比

**模式 2：共享 Y 轴**
- 所有信号绘制在同一个 Axes 上
- Y 轴自动缩放到包含所有值的范围
- 通过图例颜色区分信号

切换时保留当前勾选的信号列表，重新绘制。

### 6.3 交互功能

**（1）时间差标记**
- 点击工具栏"标记时间差"按钮进入标记模式
- 第一次点击曲线区域 → 放置标记 A（垂直线 + 时间标签）
- 第二次点击 → 放置标记 B，显示 `Δt = x.xxx s`
- 再次点击工具栏按钮或右键清除标记

**（2）曲线悬停高亮**
- 鼠标移动时，计算到每条曲线的最近距离
- 最近曲线加粗显示（linewidth 2→4）
- 显示注释框：`信号名: 值 单位 @ 时间s`
- 离开曲线区域时恢复原始样式

**（3）缩放**
- `Ctrl + 鼠标滚轮`：X/Y 轴同时缩放
- 选中 X 轴区域 + 滚轮：仅 X 轴缩放
- 选中 Y 轴区域 + 滚轮：仅 Y 轴缩放
- 缩放以鼠标位置为中心点

**（4）自适应复位**
- 工具栏一键按钮
- 调用 `ax.autoscale()` + `fig.tight_layout()`
- 恢复包含所有数据的最佳视图

**（5）曲线平移**
- 鼠标中键拖拽 / 左键按住拖拽（非标记模式时）
- 平移视图范围，不修改数据

### 6.4 性能优化

- 可视区域数据点 > 10000 时自动 LTTB 降采样
- 缩放/平移后重新采样当前可视区域
- 原始数据不丢失，仅渲染时降采样

### 6.5 信号分组管理

用户可将信号按业务场景分组（如"空调"、"车辆设置"、"能量流"），方便快速绘制相关信号曲线。

**UI 布局**：分组管理面板嵌入在曲线图 Tab 页内部，位于图表上方。

**功能：**
- 创建/删除分组
- 从左侧信号树批量添加已勾选信号到当前分组
- 分组内信号可逐个勾选或全选/全不选
- 点击"绘制当前分组曲线"绘制分组内已勾选的信号

**分组配置文件（JSON 格式）：**
```json
{
  "groups": [
    {
      "name": "空调",
      "signals": [
        {"msg_name": "ACControl", "sig_name": "AC_CompressorStatus", "frame_id": "0x2B0"},
        {"msg_name": "ACControl", "sig_name": "AC_Temperature", "frame_id": "0x2B0"}
      ]
    }
  ]
}
```

**DBC 匹配逻辑：**
- 加载分组配置时，对每个信号按 `msg_name` + `sig_name` 在当前 DBC 中查找
- 匹配成功 → 正常显示，可勾选
- 匹配失败 → 置灰不可勾选，tooltip 提示"当前 DBC 中未找到此信号"

**与信号树的关系：**
- 左侧信号树的自由勾选绘图功能保持不变
- 分组绘图和信号树自由绘图互不冲突，切换时更新曲线
- "从信号树添加"：将左侧信号树当前勾选的信号批量添加到当前分组

---

## 7. 原始报文查看器（`message_table.py`）

### 7.1 表格结构

使用 `QTreeWidget` 实现可展开的树形表格：

```
┌──────┬────────┬─────┬────┬─────────┬────────────────────────────────┐
│ 序号  │ 时间(s) │ ID  │DLC │ Channel │ Data (Hex)                     │
├──────┼────────┼─────┼────┼─────────┼────────────────────────────────┤
│ ▶ 1  │ 0.0012 │ 0x1A0│ 8  │   0    │ FF 01 23 45 67 89 AB CD       │
│   ├─ Signal_RPM     │        │ 2500 rpm                       │
│   ├─ Signal_Speed   │        │ 80.5 km/h                      │
│   └─ Signal_Temp    │        │ 45.0 °C                        │
└──────┴────────┴─────┴────┴─────────┴────────────────────────────────┘
```

### 7.2 按需解码策略

- 表格初始只显示帧头信息
- 展开某行时触发该帧信号解码
- 解码结果缓存在 `signal_cache`
- 批量展开时使用 DecodeWorker 后台解码

### 7.3 过滤功能

- 按报文 ID 过滤：下拉框选择或手动输入 hex ID
- 按信号名过滤：输入框模糊匹配
- 按时间范围过滤：起止时间输入
- 在 frame_index DataFrame 上执行向量化过滤

### 7.4 性能优化

- 虚拟模式只渲染可见行（约 50~100 个 item）
- 滚动时动态回收和复用 item
- 过滤结果集大时同样只渲染可见部分

---

## 8. 位图可视化（`bit_layout_view.py`）

### 8.1 位布局网格

- 使用 `QGraphicsView` + `QGraphicsScene` 渲染
- 经典 CAN：8 字节 × 8 bit = 64 bits 网格
- CAN FD：根据 DLC 自动扩展（最大 64 字节 × 8 bit = 512 bits）
- 每个 bit 单元格显示 bit 编号，信号占用的单元格用颜色填充

### 8.2 Intel vs Motorola 字节序

**Intel（小端）**：起始位是 LSB，从起始位向高位连续排列，跨字节时从下一字节的 bit0 继续

**Motorola（大端）**：起始位是 MSB，从起始位向低位排列，跨字节时跳到下一字节的 bit7 继续

`bit_utils.py` 中实现：
```python
def get_bit_positions(start_bit: int, length: int, byte_order: str) -> list[tuple[int, int]]:
    """返回所有被占用的 (byte, bit) 坐标列表"""
```

### 8.3 信号信息与交互

- 鼠标悬停信号色块显示 tooltip：名称、起始位、长度、字节序、scale、offset、单位、min/max
- 未被信号占用的 bit 显示灰色（padding/unused）
- 下方信号列表与网格颜色对应，点击列表项高亮对应信号色块

### 8.4 报文选择与搜索

- 搜索框支持实时模糊搜索
- 输入 hex 格式（如 `1A0`、`0x1A0`）→ 按报文 ID 精确匹配
- 输入文本 → 按报文名和信号名模糊匹配（大小写不敏感）
- 下拉候选列表，格式：`0xID - 报文名（N个信号匹配）`
- 选中后渲染该报文的位布局网格

---

## 9. 导出功能（`export_utils.py`）

### 9.1 图表导出

- PNG（300 DPI）/ SVG 矢量图
- 调用 `fig.savefig()`，自动包含图例和标记

### 9.2 信号数据导出

- CSV / Excel 格式
- 列结构：`[timestamp, signal_1, signal_2, ...]`
- 多信号时间戳不对齐时按 timestamp outer join，缺失值填充 NaN

### 9.3 报文表格导出

- 右键菜单 → "导出当前过滤结果"
- CSV 格式，列 = `[序号, 时间, ID, DLC, Channel, Data, 解码信号...]`

---

## 10. 多线程架构

### 10.1 线程模型

```
┌────────────────────────────────────────────────┐
│                 主线程（GUI 线程）                │
│  - UI 事件处理、绘图、用户交互                    │
│  - 接收 Worker 的 Qt signal 更新 UI             │
└──────────┬──────────────────────┬───────────────┘
           │ Qt signal            │ Qt signal
           ▼                      ▼
┌──────────────────┐   ┌──────────────────────────┐
│ LoadWorker       │   │ DecodeWorker             │
│ (QThread)        │   │ (QThread)                │
│                  │   │                          │
│ - 扫描日志文件    │   │ - 按需解码指定信号        │
│ - 建立帧索引      │   │ - 批量解码（全部展开时）  │
│ - 加载 raw_data   │   │ - 解码结果存入 cache     │
│                  │   │                          │
│ signals:         │   │ signals:                 │
│  progress(int)   │   │  progress(int)           │
│  finished(obj)   │   │  finished(DecodedSignal) │
│  error(str)      │   │  error(str)              │
└──────────────────┘   └──────────────────────────┘
```

### 10.2 UI 响应性保障

- 加载期间：禁用文件菜单和操作按钮，状态栏显示进度条
- 解码期间：曲线图显示"正在解码..."占位提示，已完成的信号先绘制
- 取消操作：Worker 支持 `cancel()` 方法，循环中检查标志位提前退出
- 异常处理：Worker 的 `error` signal 触发主线程弹出错误提示框

### 10.3 性能预算

| 操作 | 目标耗时 | 策略 |
|---|---|---|
| 加载 1GB BLF 建索引 | < 30s | QThread + 进度反馈 |
| 解码单个信号（100万帧） | < 5s | 按需 + 缓存 |
| 绘制 10 万点曲线 | < 1s | LTTB 降采样到 1 万点 |
| 报文表格首次显示 | < 2s | 只渲染可视行 |
| 过滤 100 万帧 | < 500ms | pandas 向量化过滤 |
| 位图渲染（64 字节 CAN FD） | < 200ms | QGraphicsView 直接绘制 |

---

## 11. 依赖库

```
PyQt5>=5.15
matplotlib>=3.5
cantools>=37.0
python-can>=4.0
pandas>=1.4
numpy>=1.21
openpyxl>=3.0       # Excel 导出
```
