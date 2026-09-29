---
name: aeflow-dev
description: >-
  Complete operational procedures, development workflows, debugging runbooks, and architectural invariants for AgentEverywhereFlow (AEFlow) - including CLI commands (aef summon/run/chat/serve/workflow/update), viewport isolation contracts, WebUI integration, multi-target coordination, and troubleshooting.
---

# AgentEverywhereFlow (AEFlow) Development & Operations Runbook

This skill equips coding agents with end-to-end knowledge and runbooks for developing, running, debugging, and testing **AgentEverywhereFlow (AEFlow)** and its companion **AgentEverywhereFlow-WebUI**.

---

## 1. Core Architectural Invariants (不可破坏的契约)

1. **视口隔离 (Viewport Isolation)**:
   - **绝不**直接使用全局屏幕物理坐标作为操作目标。
   - 所有感知输入和执行输出基于 `TargetInfo`（`x, y, width, height`，`native_handle`）。
   - 截图左上角严格为 `(0, 0)`，右下角严格为 `(width, height)`。
   - 坐标由 `CoordinateProjector` 负责在视口局部坐标系与物理操作系统坐标系之间进行精准映射。
2. **分层执行双引擎 (Dual-Mode Execution)**:
   - **Minimal Mode (CodeAct)**: 生成轻量 Python 代码块并在受控 REPL 中执行（`click(x, y)`, `type_text()`, `press()`, `wait()`, `hotkey()`）。
   - **Guarded Mode**: 返回结构化 JSON 工具调用（`{"action": "click", "params": {...}}`），经过安全策略过滤和人工审批门禁后执行。
3. **无头核心与独立客户端完全解耦**:
   - `AgentEverywhereFlow` 核心库绝不引入 Electron、Qt 或 Node 等重型 GUI 依赖，通过 `aef serve` 输出标准 REST 与 WebSocket 接口。
   - `AgentEverywhereFlow-WebUI` 为纯独立前端项目（React 19 + TypeScript + Vite + Tailwind CSS v4）。
4. **仓库整洁性**:
   - 严禁在仓库根目录下堆放测试脚本或临时截图。临时产物必须生成在 `.aef_cache/` 或系统临时目录中。

---

## 2. Essential Commands Cheatsheet

### 2.1 Backend / CLI Commands

| 操作 | 命令 | 说明 |
| :--- | :--- | :--- |
| **交互式投屏召唤** | `uv run aef summon` | 终端交互式列表选择物理屏幕或窗口进行投屏 |
| **单任务执行** | `uv run aef run --target "Chrome" --task "..."` | 按窗口标题关键字或句柄执行任务 |
| **多窗口协同对话** | `uv run aef chat --target "Chrome,Excel" --permission manual` | 连续多轮交互，跨窗口协同，开启审批门 |
| **常驻对话服务** | `uv run aef serve --host 127.0.0.1 --port 8000` | 启动 FastAPI + WebSocket 后台服务（Swagger: `/docs`） |
| **工作流回放** | `uv run aef workflow play ~/.aef/workflows/flow.yaml [--speed 1.5]` | **0 LLM API 调用**，自适应视口确定性快速重放 |
| **断点续跑** | `uv run aef chat --resume latest` | 从历史会话断点继续对话 |
| **在线自更新** | `uv run aef update` | 自动检查并升级至最新 GitHub Release 版本 |
| **环境与配置诊断** | `uv run aef config` / `uv run aef version` | 打印当前配置与版本信息 |

### 2.2 WebUI Commands

```bash
cd AgentEverywhereFlow-WebUI
pnpm install       # 安装依赖
pnpm dev           # 启动开发服务器 (http://localhost:5173)
pnpm lint          # 静态检查 (Oxlint)
pnpm build         # TypeScript 编译与生产打包
```

---

## 3. Verification & Testing Runbook

在提交代码前，必须执行以下测试与质量自检流水线：

```bash
# 1. 运行核心单元测试
uv run pytest tests/ -v

# 2. Linux 无头 X11 虚拟显示器测试（模拟真实 X11 环境）
xvfb-run -a uv run pytest tests/ -v

# 3. 运行端到端自动化高保真基准测试集
uv run python -m benchmarks.bench_suite

# 4. 代码质量与导入排序检查
uv run ruff check .
uv run ruff format --check .

# 5. 类型静态检查
uv run mypy agenteverywhereflow
```

---

## 4. Common Troubleshooting Runbooks

### 4.1 Linux X11 权限与无头测试
- **现象**: `Xlib.error.DisplayNameError: Bad display name ""`
- **原因**: 运行在无物理显示器环境（如 CI 或 SSH 会话）缺少 `$DISPLAY`。
- **方案**:
  - 安装虚拟帧缓冲：`sudo apt-get install -y xvfb`
  - 使用 `xvfb-run -a <command>` 执行，自动分配虚拟 X 服务器。

### 4.2 Windows DPI-v2 与非客户区缩放
- **现象**: 鼠标点击位置存在微小像素偏差（偏离 20~40px）。
- **方案**:
  - 确保初始化时激活了 Per-Monitor DPI-v2 感知（`ctypes.windll.shcore.SetProcessDpiAwareness(2)`）。
  - 针对 Windows 10/11 阴影边框，优先使用 `DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)` 获取真实绘制矩形。

### 4.3 Windows 控制台 GBK 解码异常
- **现象**: `UnicodeDecodeError: 'gbk' codec can't decode byte 0x...`
- **方案**:
  - 所有 subprocess 调用必须显式指定 `encoding="utf-8", errors="replace"`。

### 4.4 子控件递归命中检测
- **现象**: 在 Linux X11 下点击窗口有效，但内部嵌入式子按钮（如 Chrome 标签栏按钮、QT 按钮）未触发点击。
- **方案**:
  - `ActionDriver._find_x11_child_at` 会自顶向下递归检测最底层可见叶子节点 XID，并向该 XID 发送 `ButtonPress`/`ButtonRelease`，最后刷新事件队列 `display.sync()`。

---

## 5. WebUI & REST/WebSocket Integration Protocol

- **健康探测**: `GET /api/v1/health` -> `{"status": "ok", "version": "..."}`
- **目标列表**: `GET /api/v1/targets` -> `[TargetInfo]`
- **会话创建**: `POST /api/v1/sessions`
- **视口截图**: `GET /api/v1/sessions/{id}/screenshot` -> `image/jpeg` 二进制流
- **WebSocket 流**: `ws://127.0.0.1:8000/api/v1/sessions/{id}/ws`
  - 监听事件: `turn_start`, `reasoning`, `action_proposed`, `approval_required`, `action_executed`, `task_completed`
  - 发送操作: `{"action": "message", "instruction": "...", "max_steps": 50}`
  - 审批确认: `{"action": "approval", "approved": true, "reason": null}`
