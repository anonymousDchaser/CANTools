# CAN 报文分析工具 (CanMsgParser) v1.1.0

基于 PyQt5 的 CAN 报文分析桌面工具，支持 DBC 解析、信号检索与分组、曲线绘制、
实时监控、信号模拟上报、报文回放（BLF）等功能。

## 版本说明

- **v1.1.1**：跨平台适配（macOS 可编译、中文字体按平台自动选择）、打包脚本
  按平台自动切换参数。详见「macOS 环境搭建与编译」。
- **v1.1.0**：信号分组与信号检索视图拆分独立、跨分组搜索与勾选框交互重构。

## 界面布局（停靠视图）

主窗口采用可浮动 / 可停靠的 `QDockWidget` 布局。与信号管理相关的两个视图已
**拆分为相互独立又互相联动**的视图，共用同一套深色主题（`widgets/theme.DARK_PANEL_QSS`），
保证视觉一致：

| 视图 | 停靠标题 | 作用 |
| --- | --- | --- |
| 信号分组 | **信号分组** | 管理多个信号分组（新建 / 删除 / 切换），编辑备注，将分组内信号分发出去 |
| 信号检索 | **信号检索** | 浏览 / 搜索 DBC 报文与信号，勾选后可分发到曲线图 / 实时监控 / 模拟上报，或「加入分组」 |

默认排布：两个视图**纵向堆叠**，**「信号检索」在上、「信号分组」在下**，
高度 1:1 均分。主窗口左侧有足够空间时浮出为同一列的两个悬浮窗，否则停靠回
左侧停靠区，两种情况下上下顺序一致。

> 浮动列宽取自面板的 `sizeHint / minimumSizeHint`，不再使用停靠态宽度
> （停靠时两窗被挤在左侧停靠区内，宽度远小于实际所需，会导致浮动后两窗重叠）。

两视图联动方式：
- 在「信号检索」视图勾选信号后点击 **加入分组**，信号即被加入「信号分组」视图的当前分组；
- 「信号分组」视图若无任何分组，会自动创建「默认分组」接收。

## 信号检索视图（信号检索）

- 顶部搜索框支持报文名 / CAN ID（十六进制，如 `1A0`）模糊搜索；
- 勾选信号使用与分组视图一致的单一 **全选** 勾选框（非按钮）：勾选即选中当前
  搜索结果中所有可见信号，取消即全不选；任一可见信号被取消勾选时，全选框自动
  变为未勾选状态；
- 「已勾选信号」列表跨搜索保留，可单独移除（Delete 键或「移除选中」）。

## 信号分组视图（信号分组）

- 分组内信号搜索为 **跨分组搜索**：按信号名 / 报文名 / 帧 ID / 备注匹配，命中信号按
  所属分组以「分组标题 + 子项」归类显示；
- 不再提供「移除选中」按钮，改用 **Delete 键** 移除选中信号；
- 「全选」单一勾选框（与信号检索视图一致）：勾选=全选当前可见信号，取消=全不选，
  任一可见信号取消勾选则全选框自动取消；
- **单次搜索勾选语义**：信号的勾选仅在当前一次搜索有效，重新搜索即恢复为不勾选状态；
- 「全选」勾选框位于**搜索框正下方**（紧邻搜索条件，便于搜索后立即批量勾选）；
- 备注列可编辑（描述信号功能），随分组配置 JSON 自动保存（2000ms 节流）。

## 模拟上报页

信号按 **CAN ID 聚合为「报文组」**：同一帧的多个信号填入同一帧一次性编码发送，
每组有独立的发送周期与「发送 / 停止」按钮。

- **「所有信号」勾选框**（位于报文组行的「手动值 / 所有信号」列）：
  - 勾选：展开该报文在 DBC 中的**全部信号**作为「补充行」（灰色显示），
    同样可修改模拟值 / 手动值 / 自动递增，并随整帧一起发送；
  - 取消：仅移除补充行，**用户已选信号及其已填模拟值原样保留**；
  - 无枚举可选的补充信号会预填手动值 `0`，避免发送时刷出大片「请填写手动值」；
  - 组内已无「用户已选」信号时整组销毁，不会留下只剩补充行的空壳组。
- 模拟值下拉框行高已调大（行高 40px / 控件 30px），修复选中后文字上下被裁切。

## 页签按钮宽度

主窗口顶部页签（`QTabWidget`）按钮的最小宽度已加大，确保四字 / 五字页签标签
（如「报文表格」「位图查看器」）完整显示、不再被裁切；页签总宽在最小窗口宽度
（1280px）内仍可容纳全部页签。

## 日志时间轴（与市面工具对齐）

载入 BLF / ASC 后，所有时间均以**统一原点**表示，与 TSMaster / CANalyzer 等市面
工具约定一致，便于直接对照：

- **原点 = 测量开始时刻**：BLF 取文件头 `start_timestamp`（测量开始），ASC 等无该
  字段时回落到首帧时间；消息表与信号曲线的 X 轴均以此为 `t=0`。
- **所有信号共用同一时间原点**：信号解码后不再按「本信号首帧」二次归零。这样
  「下发信号」与「上报信号」的时间轴完全对齐，**反馈时长可直接用两信号时间相减
  得到**，不会因各自归零而错位。
- 示例：某信号首帧出现于测量开始后 19.247s、其值为 5 的首帧位于 41.646s，则
  信号曲线上该点即显示在 **41.646s**，与 TSMaster 读数一致。

## 依赖

```
PyQt5 matplotlib cantools python-can pandas numpy openpyxl xlrd lxml uptime
```

- 完整清单见 `requirements.txt`（其中 `pytest` 仅用于开发自测、`pyinstaller`
  仅用于打包，**运行时不需要**）。
- **Python 版本**：推荐 **3.11 / 3.12**。PyQt5 5.15.11（当前最新版）官方支持到
  Python 3.12，更高版本虽可能装上但属未验证组合，不建议用于打包。

## 运行

```bash
python main.py
```

## 打包

```bash
python build.py
```

`build.py` 会自动按当前平台选择参数，产物路径如下：

| 平台 | 产物 |
| --- | --- |
| Windows | `dist/CanMsgParser/CanMsgParser.exe`（无控制台窗口，onedir 模式） |
| macOS | `dist/CanMsgParser.app`（双击运行，或 `open dist/CanMsgParser.app`） |
| Linux | `dist/CanMsgParser/CanMsgParser` |

`build.py` 会将上一版 `dist/`、`build/`、`.spec` 改名移入 `.build_trash/`（已被
`.gitignore` 忽略），不再保留编译产物备份。

> **跨平台说明**：原先字体只写了 Windows 字体，macOS 下曲线图中文会显示为方框
> （回落到不含中文字形的 DejaVu Sans）。现统一由 `utils/font_helper.py` 按平台
> 给出字体候选——Windows 用 Microsoft YaHei、macOS 用 PingFang SC、Linux 用
> Noto Sans CJK SC，三者互为兜底，`build.py` 的 Windows 专属参数
> （`--manifest` / `--noconsole`）也已在非 Windows 平台跳过。

## macOS 环境搭建与编译

> 适用：macOS 12+，Intel(x86_64) 与 Apple Silicon(arm64) 均可。

### 1. 确认架构与 Python 版本

```bash
uname -m            # arm64 = Apple Silicon；x86_64 = Intel
python3 --version
```

- 推荐 **Python 3.11 或 3.12**（PyQt5 5.15.11 官方支持到 3.12）。
- Apple Silicon 请务必使用**原生 arm64** Python。若 `uname -m` 输出 `arm64`
  而 Python 是 x86_64（Rosetta），PyQt5 与打包产物都会以转译模式运行，性能差
  且产物只能在该模式下启动。

用 pyenv 安装（推荐，便于锁定版本）：

```bash
brew install pyenv
pyenv install 3.12.6
pyenv local 3.12.6          # 在本项目目录下固定版本
```

也可直接用 Homebrew 的 Python：`brew install python@3.12`。

### 2. 创建虚拟环境并安装依赖

```bash
cd CanMsgParser
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

依赖均为跨平台包，无需额外编译工具链。两个可能卡住的点：

- `xlrd==1.2.0` 在 PyPI 上只提供源码包（无 wheel），安装时需要本地构建。
  若报错，先升级构建工具再重试：

  ```bash
  pip install --upgrade setuptools wheel
  pip install -r requirements.txt
  ```

- `PyQt5` 装不上，绝大多数情况是 Python 版本过高。请确认 Python ≤ 3.12
  （PyQt5 5.15.11 的官方支持上限）。

### 3. 以源码方式运行

```bash
python main.py
```

### 4. 编译为 .app

```bash
python build.py
```

产物为 `dist/CanMsgParser.app`：

```bash
open dist/CanMsgParser.app
```

### 5. macOS 常见问题

**Q1. 提示「无法打开，因为它来自身份不明的开发者」**

这是 Gatekeeper 对未签名 app 的拦截，与程序本身无关。二选一：

- 在访达中**右键**点击 `CanMsgParser.app` →「打开」，在弹窗中确认（只需一次）；
- 或清除隔离属性：

  ```bash
  xattr -cr dist/CanMsgParser.app
  ```

如需在本机长期免去该提示，可做 ad-hoc 自签名（仍非公证，分发给他人仍会被拦截）：

```bash
codesign --force --deep --sign - dist/CanMsgParser.app
```

**Q2. 曲线图 / 界面中文显示为方框**

字体已由 `utils/font_helper.py` 自动适配（macOS 首选 PingFang SC）。若仍显示
方框，通常是 matplotlib 字体缓存未刷新：

```bash
python -c "import matplotlib; print(matplotlib.get_cachedir())"   # 查看缓存目录
rm -rf <上面输出的目录>
```

**Q3. 连不上 CAN 设备 / 找不到 PCAN、Vector**

`python-can` 的 PCAN、Vector 等后端在 macOS 上没有官方驱动（SocketCAN 为 Linux
专有）。因此 **macOS 版适合离线分析**——加载 DBC、回放 BLF/ASC 日志、查看信号
曲线与位图、导出数据等功能均正常；**实时收发报文**（连接硬件、模拟上报）在
macOS 上不可用，除非设备厂商提供 macOS 驱动。

**Q4. 执行 `python` 提示 command not found**

macOS 自带的命令是 `python3`。请确认已激活虚拟环境
（`source .venv/bin/activate`），激活后 `python` 即指向 venv 内的解释器。
