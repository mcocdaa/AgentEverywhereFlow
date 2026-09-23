# 贡献指南 (Contributing Guide)

感谢你关注并愿意为 **AgentEverywhereFlow (AEFlow)** 贡献代码与想法！  
本项目属于 [**\*Flow 生态**](https://github.com/mcocdaa) 的一部分，致力于提供轻量、高可靠、跨平台的具身桌面智能体基础设施（"Agent on Everywhere"）。

---

## 目录 (Table of Contents)

1. [行为准则 (Code of Conduct)](#行为准则-code-of-conduct)
2. [提交前准备 (Getting Started)](#提交前准备-getting-started)
3. [本地研发环境配置 (Development Setup)](#本地研发环境配置-development-setup)
4. [规范与代码风格 (Coding Standards)](#规范与代码风格-coding-standards)
5. [测试与基准验证 (Testing & Benchmarks)](#测试与基准验证-testing--benchmarks)
6. [提交信息规范 (Commit Conventions)](#提交信息规范-commit-conventions)
7. [Pull Request 提交流程 (PR Workflow)](#pull-request-提交流程-pr-workflow)

---

## 行为准则 (Code of Conduct)

我们致力于营造一个友善、包容且专业的开源协作社区。请对所有协作者保持互相尊重，专注建设性反馈。

---

## 提交前准备 (Getting Started)

1. **查阅 Issue / Discussion**：
   在开始开发重大特性、重构或修改核心协议之前，请先提交 Issue 说明背景与设计方案，避免重复劳动。
2. **规范指引**：
   - 熟悉核心架构理念：请阅读 [docs/architecture.md](docs/architecture.md)。
   - 面向编码智能体的指南：请阅读 [AGENTS.md](AGENTS.md)。
   - 严禁在代码中硬编码任何私有凭证、API Key 或生产敏感数据。

---

## 本地研发环境配置 (Development Setup)

本项目推荐使用现代极速包管理器 [`uv`](https://github.com/astral-sh/uv) 进行虚拟环境与依赖管理。

```bash
# 1. 克隆代码仓库
git clone https://github.com/mcocdaa/AgentEverywhereFlow.git
cd AgentEverywhereFlow

# 2. 创建并激活虚拟环境 (推荐 Python 3.11+)
uv venv
source .venv/bin/activate  # Linux / macOS
# 或在 Windows PowerShell:
# .\.venv\Scripts\Activate.ps1

# 3. 安装开发与平台依赖
# Linux 环境:
uv pip install -e ".[dev,linux]"

# Windows 环境:
uv pip install -e ".[dev,windows]"

# 4. 配置本地环境变量
cp .env.example .env
```

---

## 规范与代码风格 (Coding Standards)

我们使用 [`ruff`](https://github.com/astral-sh/ruff) 保证代码整洁度与高效导入排序，使用 [`mypy`](https://mypy-lang.org/) 进行静态类型检查。

```bash
# 代码检查与快速自动修复
uv run ruff check --fix .

# 代码格式化排版
uv run ruff format .

# 类型检查
uv run mypy agenteverywhereflow
```

### 核心编码原则：
- **无破坏性修改**：对已有的 API、CLI 命令或执行契约保持向后兼容。
- **跨平台鲁棒性**：底层操作系统调用（Win32 / X11 / Wayland / Quartz）必须封装在平台特定的模块（`capturer/` 与 `actions/driver.py`）中，上层使用抽象接口（`BaseCapturer`, `InputDriver`），严防跨平台导入崩溃。
- **纯净依赖**：避免引入沉重且非必要的第三方库；优先利用标准库与轻量依赖。

---

## 测试与基准验证 (Testing & Benchmarks)

任何提交合并前必须通过现有单元测试与基准测试：

```bash
# 运行单元测试
uv run pytest tests/ -v

# 运行全链路高保真基准套件 (需要图形环境或虚拟显示)
uv run python -m benchmarks.bench_suite
```

---

## 提交信息规范 (Commit Conventions)

推荐使用 [Conventional Commits](https://www.conventionalcommits.org/) 格式：

```text
<type>(<scope>): <subject>
```

- **feat**: 新增功能（如添加新的捕获后端、新的执行模式）
- **fix**: 修复 Bug（如修复窗口句柄坐标偏移、按键映射异常）
- **docs**: 文档变更（如补充快速上手、API 架构图）
- **test**: 测试相关（新增测试用例、基准测试优化）
- **refactor**: 代码重构（不增加新功能或修复 Bug 的结构调整）
- **style**: 代码格式变动（空格、标点、导入排序等，不改变代码逻辑）
- **chore**: 构建系统、依赖更新或辅助工具变动

示例：
```bash
git commit -m "feat(capturer): support XWayland rootless window rect offset"
git commit -m "fix(driver): map space character to XK keysym correctly"
```

---

## Pull Request 提交流程 (PR Workflow)

1. 从 `main` 分支拉取最新代码并创建特性分支：
   ```bash
   git checkout -b feat/your-feature-name
   ```
2. 编写代码、测试用例并确保本地全部通过：
   ```bash
   uv run ruff check .
   uv run pytest tests/
   ```
3. 提交代码并推送分支至你的 Fork 仓库。
4. 在 GitHub 发起 Pull Request，按模板填写变动内容、测试验证结果及关联的 Issue 编号。
5. 关注 GitHub Actions CI 流水线状态，根据 Review 反馈及时迭代。
