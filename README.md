# IDEATools
一些小想法做的东西~

---

# CAN 报文分析工具 (CanMsgParser)

一款类似 Vector CANoe 的车载 CAN 报文分析桌面工具，基于 Python + PyQt5 开发，支持 DBC 解析、BLF/ASC 日志加载、多信号曲线绘制、报文查看、位图可视化等功能。专为汽车电子工程师分析 CAN 总线数据而设计。

![Python](https://img.shields.io/badge/Python-3.10+-blue) ![PyQt5](https://img.shields.io/badge/GUI-PyQt5-green) ![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)

## ✨ 功能特性

### 📁 文件加载
- **DBC 文件解析**：基于 cantools 库，支持完整 DBC 信号定义（起始位、长度、字节序、缩放因子、偏移量、单位、值描述等）
- **日志文件加载**：支持 BLF 和 ASC 两种常见 CAN 日志格式（基于 python-can）
- **CAN FD 支持**：同时兼容经典 CAN（8 字节）和 CAN FD（最大 64 字节）
- **拖放加载**：直接拖拽 .dbc / .blf / .asc / .xlsx 文件到窗口即可加载
- **配置记忆**：自动记住上次加载的 DBC、Excel、分组配置路径，下次启动自动恢复

### 🌳 信号浏览与搜索
- 树形结构展示所有报文（Message）和信号（Signal）
- **模糊搜索**：支持按信号名/报文名搜索，也支持按 CAN ID（hex）搜索（如输入 `1A0` 或 `0x1A0`）
- 搜索时自动展开匹配的报文节点
- 支持单选、全选当前报文、取消全选

### 📊 信号曲线绘制（核心功能）
- 多信号时间-值曲线绘制，X 轴为时间戳（秒），Y 轴为信号物理值
- 每个信号用不同颜色折线 + 圆点标记（标记报文发送时刻）
- **两种图表模式可切换**：
  - 独立子图模式：每个信号一个 subplot，共享 X 轴
  - 共享 Y 轴模式：所有信号在同一图表，自动缩放
- **丰富交互**：
  - `Ctrl + 鼠标滚轮`：X/Y 轴同时缩放
  - 鼠标位置靠近 X/Y 轴区域 + 滚轮：单轴缩放
  - 鼠标拖拽：平移曲线视图
  - 悬停高亮：鼠标靠近曲线时加粗显示，弹出注释框显示信号名、当前值、时间
  - **值含义显示**：悬停注释框显示 DBC/Excel 中定义的值描述（如 `2 (OFF)`）
  - 单击固定：点击曲线可固定高亮显示，再次点击取消
  - 时间差标记：放置两个标记点，显示 Δt 时间差
  - 自适应复位：一键恢复最佳视图
- **大数据优化**：LTTB 降采样算法，超过 10000 点自动降采样，保留曲线特征

### 📂 信号分组管理
- 创建自定义分组（如"空调"、"车辆设置"、"能量流"）
- 从信号树批量添加信号到分组
- 分组内信号可逐个勾选或全选
- 分组配置支持保存/加载（JSON 格式）
- **DBC 匹配**：加载分组配置时，与当前 DBC 不匹配的信号自动置灰不可勾选

### 📋 原始报文查看器
- 表格形式显示原始 CAN 报文（序号、时间、ID、DLC、Channel、Data）
- **可展开行**：双击展开查看该帧所有解码后的信号值
- **值变化高亮**：当某字节值变化时，以亮红色高亮显示，并在后续 500 帧内逐渐渐变消退（基于筛选后的帧数）
- **多条件过滤**：按报文 ID、信号名、时间范围过滤，支持回车键触发
- 大数据量优化：只渲染可见行

### 🔲 DBC 位图可视化
- 显示报文数据帧的位布局网格（8 字节 = 64 bits，CAN FD 最大 512 bits）
- **Intel（小端）/ Motorola（大端）正确渲染**
- 每个信号用不同颜色高亮占用的 bit 位置（ColorBrewer 风格调色板）
- 单元格内显示：信号名（左上角）、bit 位号（居中）、MSB（左下角）、LSB（右下角）
- 鼠标悬停显示信号详细信息（起始位、长度、字节序、scale、offset、范围等）
- 搜索框支持按 ID 或报文名/信号名模糊搜索
- 网格随窗口大小动态缩放
- 下方信号详情表显示完整信号定义 + 值描述

### 💾 导出功能
- **图表导出**：PNG（300 DPI）/ SVG 矢量图
- **信号数据导出**：CSV / Excel，多信号按时间戳对齐
- **报文表格导出**：CSV 格式，含解码信号

### 🎨 其他特性
- **启动画面**：带小车和坐标图插图的优美启动界面，显示加载进度
- **依赖检测**：启动时自动检测必需 Python 库，缺失时弹窗询问是否自动 pip 安装
- **多线程架构**：QThread 后台加载和解码，UI 始终响应
- **专业暗色主题**：统一设计系统，护眼且美观
- **Excel 值描述**：支持从复杂 CAN 矩阵 Excel 表格提取信号值描述，DBC 优先、Excel 补充

## 🛠 技术栈

| 库 | 用途 |
|---|---|
| **PyQt5** | GUI 框架 |
| **matplotlib** | 图表绘制（qt5agg backend 嵌入） |
| **cantools** | DBC 文件解析与信号解码 |
| **python-can** | BLF/ASC 日志文件读取 |
| **pandas** | 数据处理与过滤 |
| **numpy** | 数值计算 |
| **openpyxl** | .xlsx 文件读写 |
| **xlrd 1.2.0** | .xls（OLE2）文件读取（1.2.0 是最后支持 .xls 的版本） |
| **lxml** | HTML 表格格式的 Excel 文件解析 |
| **PyInstaller** | 打包为可执行文件 |

## 📦 安装

### 环境要求
- Python 3.10+
- Windows 10/11（推荐）

### 从源码运行

```bash
git clone <repo-url>
cd CanMsgParser
pip install -r requirements.txt
python main.py
```

> 首次启动时，应用会自动检测依赖库，如缺失会弹窗询问是否自动安装。

### 打包为可执行文件

```bash
python build.py
```

生成的可执行文件位于 `dist/CanMsgParser/CanMsgParser.exe`，运行时不弹出控制台窗口。

## 📖 使用指南

### 快速上手

1. **加载 DBC 文件**
   - 菜单：文件 → 加载 DBC...
   - 或直接拖拽 .dbc 文件到窗口
   - 加载后左侧信号树显示所有报文和信号

2. **加载日志文件**
   - 菜单：文件 → 加载日志 (BLF/ASC)...
   - 或拖拽 .blf / .asc 文件到窗口
   - 状态栏显示帧数和时间范围

3. **绘制信号曲线**
   - 在信号树中勾选要绘制的信号
   - 点击"绘图"按钮
   - 曲线显示在曲线图 Tab

4. **查看原始报文**
   - 切换到"报文表格"Tab
   - 双击报文行展开查看解码后的信号值
   - 使用过滤栏按 ID/信号名/时间筛选

5. **查看位图布局**
   - 切换到"位图查看器"Tab
   - 搜索报文 ID 或名称
   - 查看信号在数据帧中的位布局

### 加载 Excel 值描述

如果你的 DBC 没有定义信号值描述（ValueDescriptions），可以通过 Excel 文件补充：

1. 菜单：文件 → 加载值描述 Excel...
2. 或拖拽 .xlsx / .xls 文件到窗口

**Excel 文件格式要求**：
- 必须包含 `Signal Name / 信号名称` 列
- 必须包含 `Signal Value Description / 信号值描述` 列
- 支持双语表头（英文+中文换行分隔）
- 值描述格式：`0:OFF, 1:ON` 或 `0=OFF; 1=ON` 或 `0x0-OFF` 等
- 支持多 sheet（自动查找数据 sheet）
- 兼容 .xls（OLE2）、.xlsx（OpenXML）、HTML 表格伪装的 .xls

### 信号分组使用

1. 在曲线图 Tab 的分组面板点击"+ 新建"
2. 输入分组名称（如"空调"）
3. 在左侧信号树勾选信号，点击"从信号树添加"
4. 分组内勾选要绘制的信号
5. 点击"绘制当前分组曲线"
6. 点击"保存配置"可将分组保存为 JSON 文件，下次启动自动加载

### 曲线图交互操作

| 操作 | 效果 |
|---|---|
| `Ctrl + 鼠标滚轮` | X/Y 轴同时缩放 |
| 鼠标靠近 X 轴 + 滚轮 | 仅 X 轴缩放 |
| 鼠标靠近 Y 轴 + 滚轮 | 仅 Y 轴缩放 |
| 鼠标拖拽 | 平移曲线视图 |
| 鼠标悬停曲线 | 高亮 + 显示值信息 |
| 单击曲线 | 固定高亮（再次单击取消） |
| "标记时间差"按钮 | 进入标记模式，点击两点显示 Δt |
| "自适应复位"按钮 | 恢复最佳视图 |
| "切换为独立子图/共享Y轴" | 切换图表模式 |

## 📂 项目结构

```
CanMsgParser/
├── main.py                        # 应用入口（含启动画面、依赖检测）
├── main_window.py                 # 主窗口布局
├── build.py                       # PyInstaller 打包脚本
├── requirements.txt               # 依赖列表
├── core/
│   ├── can_data.py                # 数据模型 (SignalDef, MessageDef, DecodedSignal)
│   ├── dbc_parser.py              # DBC 解析封装 (cantools)
│   ├── log_loader.py              # BLF/ASC 加载器 (python-can)
│   └── signal_cache.py            # LRU 解码缓存
├── widgets/
│   ├── signal_tree.py             # 信号树 + 搜索
│   ├── plot_widget.py             # 曲线图 + 交互
│   ├── signal_group_panel.py      # 信号分组管理
│   ├── message_table.py           # 报文表格 + 值变化高亮
│   ├── bit_layout_view.py         # 位图可视化
│   └── splash_screen.py           # 启动画面
├── utils/
│   ├── bit_utils.py               # Intel/Motorola 位布局计算
│   ├── lttb.py                    # LTTB 降采样算法
│   ├── export_utils.py            # 导出工具
│   ├── excel_value_loader.py      # Excel 值描述加载
│   └── dependency_check.py        # 启动依赖检测
├── workers/
│   └── load_worker.py             # QThread 工作线程
└── tests/                         # 单元测试与集成测试
```

## 🧪 测试

```bash
python -m pytest tests/ -v
```

覆盖：数据模型、位布局计算、DBC 解析、日志加载、LTTB 降采样、信号缓存、报文表格展开、字节变化高亮等。

## 👤 作者

- **作者**：laizhenxin
- **邮箱**：lzxDchaser@126.com

## 📝 许可

内部工具，仅限授权使用。
