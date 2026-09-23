# AgentEverywhereFlow (AEFlow) — Instructions for AI Coding Agents

本文件面向在 **AgentEverywhereFlow** 仓库中工作的 AI Coding Agent（包括 Claude, Antigravity, Copilot, Trae, Cursor 等）。  
普通开发者及用户请参阅 [README.md](README.md) 与 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## 1. Project Mission & Invariants (核心理念与不可破坏的契约)

AgentEverywhereFlow 是 `*Flow` 生态下的开源桌面级具身智能体基础设施。其核心差异化在于 **"投屏式选择与精准视口隔离"**（如同在会议软件中选择特定窗口或全屏进行操作）。

### 核心契约与原则：
1. **视口隔离 (Viewport Isolation)**：
   - 绝不随意将全局屏幕像素直接作为目标窗口的坐标。
   - 所有感知与操作基于 `TargetInfo`（包含 `Rect(x, y, width, height)`、`native_handle` 和 `TargetType`）。
   - 截图只返回目标视口范围；坐标由 `CoordinateProjector` 负责在视口局部坐标系与物理操作系统坐标系之间转换。
2. **分辨率契约 (Resolution Contract)**：
   - 传递给 VLM 的截图严格标明视口尺寸 `(width, height)`，`top-left=(0, 0)`，`bottom-right=(width, height)`。
   - 依赖 VLM 原生像素理解，不引入不稳定的启发式 OpenCV 轮廓探测。
3. **分层执行双引擎 (Dual-Mode Execution)**：
   - **Minimal Mode (CodeAct)**：生成 Python 代码并在受控 REPL 中执行（`click(x, y)`, `type_text()`, `press()`, `wait()`）。
   - **Guarded Mode**：返回结构化 JSON 工具调用（`{"action": "click", "params": {...}}`），经过安全策略检查后执行。
4. **跨平台输入注入安全与健壮性**：
   - 在 X11 / Linux 环境下，直接通过 `Xlib` 发送原生按键/鼠标事件到具体目标窗口，支持递归子控件命中检测 (`_find_x11_child_at`)。
   - 对特殊按键（如空格 `' '` -> `XK.string_to_keysym("space")`）必须保证映射完备。
5. **仓库整洁性与规范 (Repo Cleanliness)**：
   - **严禁**在仓库根目录下堆放随意的测试脚本、临时图片（如 `test_*.png`、`temp.py`）。
   - 单元测试一律放入 `tests/`，端到端高保真基准测试一律放入 `benchmarks/`。
   - 临时文件必须生成到 `.aef_cache/` 或系统的临时路径下，并在 `.gitignore` 中配置。

---

## 2. Repository Layout (项目结构速查)

```text
AgentEverywhereFlow/
├── .github/                    # CI/CD 流水线、Issue/PR 模板、Dependabot
├── agenteverywhereflow/        # 核心 Python 包
│   ├── actions/                # 键鼠驱动 (driver.py) 与坐标转换 (coords.py)
│   ├── agent/                  # 感知-决策-执行循环 (loop.py)、提示词与视网膜标尺 (vision.py)
│   ├── capturer/               # 跨平台屏幕/窗口零拷贝捕获 (windows.py, linux.py, selector.py)
│   ├── engine/                 # 极简模式 (python_repl.py) 与受控模式 (guarded.py) 执行引擎
│   ├── cli.py                  # Typer CLI 入口 (`aef` 命令)
│   └── config.py               # Pydantic 全局配置
├── benchmarks/                 # 自动化端到端高保真基准测试集
├── docs/                       # 架构设计、执行引擎及视觉管线文档
├── examples/                   # 开发者调用示例
├── tests/                      # 单元测试 (pytest)
├── pyproject.toml              # 项目构建与依赖声明
├── CONTRIBUTING.md             # 开发者贡献指南
├── SECURITY.md                 # 安全与漏洞报告规范
└── CHANGELOG.md                # 版本更新日志
```

---

## 3. Essential Commands (核心研发与验证命令)

```bash
# 1. 运行所有单元测试
uv run pytest tests/ -v

# 2. 运行自动化端到端高保真基准测试
uv run python -m benchmarks.bench_suite

# 3. 代码质量与导入排序检查
uv run ruff check .

# 4. 自动修复可纠正的代码质量问题
uv run ruff check --fix .

# 5. 代码排版格式化
uv run ruff format .

# 6. 类型静态检查
uv run mypy agenteverywhereflow

# 7. CLI 命令自检
uv run aef --help
uv run aef version
```

---

## 4. Coding Agent Verification Checklist (变更交付自检清单)

在完成代码修改并提交前，必须逐项自检：
- [ ] 运行 `uv run pytest tests/ -v` 全部通过（零失败）。
- [ ] 运行 `uv run ruff check .` 无任何警告与错误。
- [ ] 运行 `uv run ruff format --check .` 保持格式对齐。
- [ ] 根目录下无任何未跟踪的临时文件、临时日志或图片缓存。
- [ ] 新增或重构的核心接口具有完整的类型提示与简明 Google/PEP 257 风格文档注释。
- [ ] 若新增依赖，在 `pyproject.toml` 的对应分组中正确声明。
- [ ] Git 提交信息遵循 Conventional Commits 规范（如 `feat(capturer): ...`, `test(bench): ...`）。
