# 更新日志 (Changelog)

所有关于 **AgentEverywhereFlow (AEFlow)** 的重要变动都将记录在此文件中。  
本项目遵循 [Semantic Versioning (语义化版本 2.0.0)](https://semver.org/lang/zh-CN/) 与 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 规范。

---

## [0.1.3] - 2026-09-24

### 真实场景演示与资产 (Visual Showcases & Demos)
- **README 首屏加入真实运行双场景动图**：
  - **九宫格人机验证破解 (`demo_captcha.gif`)**：零样本多模态视觉空间定位，自动识别 3 张猫咪图片并点击打勾，成功通过验证。
  - **Excel 表格自动公式统计 (`demo_excel.gif`)**：投屏绑定 Excel 窗口，自动定位销售额求和空白单元格，注入 `=SUM(D2:D6)` 公式并回车完成计算。
- **开箱即用演示页面与数据套件**：
  - 新增交互式 9 宫格安全人机验证演示页面（`examples/captcha_demo.html`）。
  - 新增销售报表 Excel 生成与演示套件（`examples/generate_excel_demo.py`, `examples/sales_demo.xlsx`）。

### 核心引擎强化 (Core Engine Enhancements)
- **多代码块 CodeAct 序列合并执行**：
  - 引入 `extract_codeact_blocks`，自动收集同一回复轮次中模型输出的所有有效 Python 代码块并顺序合并执行，彻底解决复杂思考过程中分块输出导致后续动作（如 `press("enter")`）被遗漏的问题。
- **终端转义隔离与实时上屏保护**：
  - 动作日志 Console 明确绑定底层物理终端 `sys.__stdout__`，彻底杜绝 Rich ANSI 控制码污染代码执行缓冲区导致的终端乱码现象。
  - 仅在用户代码主动调用 `print` 时安全转义并打印 `Output:`。
- **全局环境配置与 CLI 便捷命令 (`aef config`)**：
  - 支持 `~/.aef/.env` 全局配置文件与多层级配置级联加载。
  - 自动向下兼容标准 `OPENAI_API_KEY`、`OPENAI_BASE_URL` 与 `OPENAI_MODEL_NAME` 环境变量。
  - 新增 `aef config --set-key`、`--set-base`、`--set-model` CLI 配置命令与缺失密钥友好引导面板。
- **历史截图上下文滑动窗口裁剪**：
  - 自动修剪历史多轮截图为文本占位标记，保护上下文 Token 额度。
- **Windows 控制台编码容错**：
  - 提供 UTF-8 控制台重配置与 emoji 降级支持，防止 Windows GBK/charmap 代码页编码崩溃。

---

## [0.1.2] - 2026-09-23

### 修复与强化 (Fixed & Enhanced)
- **Windows 真实输入系统原生加固**：
  - **硬件级鼠标点击注入**：采用 `SetCursorPos` + `mouse_event(MOUSEEVENTF_LEFTDOWN/UP)` 物理事件取代顶层窗体 `WM_LBUTTONDOWN` 投递，彻底解决现代 Windows 11 记事本、VS Code、Chrome 等多层子控件不响应点击的问题。
  - **Win32 焦点锁定穿透 (AttachThreadInput)**：引入线程输入挂载技巧，强制突破 Windows 系统的 `SPI_SETFOREGROUNDLOCKTIMEOUT` 前台切换限制，确保最小化或失焦目标窗口被物理唤醒置顶。
  - **Unicode / 中文无损注入与防输入法劫持**：引入系统剪贴板直通注入（Clipboard Injection + `Ctrl+V`），彻底规避 `pyautogui.write` 丢失非 ASCII 汉字以及中文输入法（IME）候选词弹窗拦截的问题。
- **目标精准定位与歧义消除 (Target ID & Index Resolution)**：
  - `aef list-targets` 表格新增专属 **Target ID** 列（如 `hwnd:0x1b0a4`, `display:1`）。
  - CLI `--target` / `-t` 支持五级解析策略：Target ID 精确匹配 -> 16进制/10进制原生 HWND 句柄 -> 表格序号（如 `1`, `2`）-> 进程名（如 `notepad.exe` / `notepad`）-> 窗口标题模糊匹配。
  - 核心逻辑抽象为独立的 `resolve_target` 工具并接入全套单元测试。

---

## [0.1.1] - 2026-09-23

### 优化与新增 (Optimized & Added)
- **CLI 启动性能提升 45 倍**：重构 `cli.py` 与 `agent/loop.py`，采用延迟惰性导入（Lazy Import），避免在执行 `aef --help` 或 `aef version` 时加载重型依赖，冷启动耗时从 9.1s 暴降至 ~0.2s。
- **Windows 原生进程名与状态发现**：
  - 采用 `QueryFullProcessImageNameW` 获取目标窗口准确进程名（如 `Code.exe`, `WindowsTerminal.exe`）。
  - 接入 `GetWindowPlacement` 准确识别最小化窗口并在捕获与激活时自动还原。

---

## [0.1.0] - 2026-09-23

### 新增 (Added)
- **跨平台视口捕获抽象与发现 (Viewport Engine)**：
  - Windows 原生 DirectComposition / PrintWindow (`PW_RENDERFULLCONTENT`) 后台窗口截取与 DWM Cloaked 窗口过滤。
  - Per-Monitor DPI Aware v2 支持与客户区坐标矩阵校准。
  - Linux X11 独立窗口 Drawable 原生捕获与 `mss` 多显示器全屏截取回退。
  - 交互式终端投屏选择器 (`aef summon` / `TargetSelector`)，支持像会议软件投屏一样选择目标屏幕或特定窗口。
- **高精度输入驱动与坐标投影 (Actions Engine)**：
  - `CoordinateProjector`：实现目标局部视口坐标到操作系统全局物理坐标的高精度无畸变变换。
  - `InputDriver`：支持直接向 Linux X11 目标窗口及特定子控件分发事件（`_find_x11_child_at` 递归命中检测），解决根窗口与子组件事件穿透难题。
  - 完备的键盘事件映射，包括空格键与复合修饰键。
- **双模式执行引擎 (Dual-Mode Execution Architecture)**：
  - **极简模式 (Minimal CodeAct REPL)**：支持模型直接生成 Python 工具代码（`click`, `type_text`, `press`, `wait`）并在受限沙箱中运行。
  - **控制模式 (Guarded Action Engine)**：提供结构化原子级动作字典解析，支持操作安全策略拦截与确认门禁。
- **视觉增强与标定体系 (Vision Pipeline)**：
  - 提供坐标网格辅助标尺（Grid Ruler Overlay），在复杂密集 UI 场景下辅助模型校准空间感知。
  - 提供 Set-of-Mark (SoM) 候选交互区域轮廓聚集与数字标签生成。
- **端到端高保真基准测试集 (Autonomous Benchmark Suite)**：
  - 表单自动化基准（输入框、单选框、复选框、提交校验）。
  - 计数器自动化基准（多步骤连续运算点击与结果校验）。
  - 受控模式状态机全闭环测试。
- **工程化基础设施**：
  - 采用现代极速打包工具 `uv` 与 `pyproject.toml` 现代化声明。
  - 接入 `ruff`、`mypy`、`pytest` 全流程质量门禁。
  - 建立完善的 GitHub Actions CI 工作流、开源协作文档与 Issue/PR 模板。
