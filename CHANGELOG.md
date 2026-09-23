# 更新日志 (Changelog)

所有关于 **AgentEverywhereFlow (AEFlow)** 的重要变动都将记录在此文件中。  
本项目遵循 [Semantic Versioning (语义化版本 2.0.0)](https://semver.org/lang/zh-CN/) 与 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 规范。

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
