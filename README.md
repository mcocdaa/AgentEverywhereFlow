<div align="center">

# 🌐 AgentEverywhereFlow (`AEFlow`)

**Agent on Everywhere**: Instant screen & window casting for embodied desktop agents.  
任何页面与窗口皆可一键投屏召唤的具身智能体基础设施。

[![Family: *Flow](https://img.shields.io/badge/family-*Flow-8A2BE2.svg)](https://github.com/mcocdaa)
[![CI Status](https://github.com/mcocdaa/AgentEverywhereFlow/actions/workflows/ci.yml/badge.svg)](https://github.com/mcocdaa/AgentEverywhereFlow/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/Python-3.11%2B-brightgreen.svg)](pyproject.toml)
[![Code Style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

[English](README.md) | [简体中文](README_CN.md)

</div>

---

<div align="center">

### 🎬 Real-World Autonomous Demos

#### 🐱 Scenario 1: 9-Grid Visual CAPTCHA Solving & Spatial Grounding
<img src="docs/assets/demo_captcha.gif" alt="AEFlow 9-Grid Captcha Demo" width="820">

*Zero-shot multimodal spatial grounding: Agent targets browser window, identifies all 3 cat tiles in a 9-grid challenge, clicks each with visual checks, and passes verification.*

```bash
# Reproduce in Chrome or Edge (try it yourself with examples/captcha_demo.html):
aef run --target "Chrome" --task "在当前九宫格人机验证中，找出所有包含猫咪的方格依次点击选中，然后点击右下角的验证按钮"
```

<br/>

#### 📊 Scenario 2: Office Spreadsheet Automation & Formula Calculation
<img src="docs/assets/demo_excel.gif" alt="AEFlow Excel Summation Demo" width="820">

*Desktop productivity: Agent targets Microsoft Excel window, locates target Total cell, injects <code>=SUM(D2:D6)</code> formula, and calculates total sales.*

```bash
# Reproduce in Excel or WPS (try it yourself with examples/sales_demo.xlsx):
aef run --target "Excel" --task "在销售额列下方的总计空白单元格点击，输入求和公式计算总金额并按回车"
```

</div>

## 💡 Why AgentEverywhereFlow?

Existing computer-use and GUI automation agents suffer from three major roadblocks:
1. **Unfocused Global Viewports**: Grabbing the entire desktop leads to visual noise, popup interference, and negative/distorted coordinates across multiple monitors.
2. **Lack of Instant Targeting**: There is no lightweight experience akin to Zoom / Tencent Meeting screen-sharing—allowing users to freely cast either an **entire monitor** or a **specific application window** (VS Code, Chrome, Terminal, etc.) to the agent.
3. **Execution Rigidity vs. Safety**: Either agents are constrained to sluggish step-by-step clicks or given unconstrained bash access that risks destructive missteps.

**AgentEverywhereFlow (AEFlow)** solves this by introducing **Screen-Cast Style Viewport Isolation** for autonomous GUI agents:
- 🎯 **Screen-Cast Target Picker**: Discover and bind agents to any physical display or active application window with zero-copy capture and precise viewport coordinate projection.
- ⚡ **Instant Summon (`aef summon`)**: Cast and summon an agent in seconds via an interactive terminal UI or global shortcut.
- 📐 **Explicit Resolution Contract**: Native pixel-space calibration `(0, 0) -> (width, height)` paired with optional coordinate grids and Set-of-Mark (SoM) indicators.
- 🛡️ **Dual-Mode Execution Architecture**:
  - **Minimal Mode (CodeAct REPL)**: High-speed, pythonic chaining of actions (`click`, `type_text`, `press`, `wait`, `hotkey`) with multi-block sequence merging.
  - **Guarded Mode (JSON Schema)**: Strict atomic action schemas with safety policy filters and human-in-the-loop confirmation gates.
- 🎯 **Recursive Sub-Widget Hit Testing**: Deep X11 and Win32 tree inspection ensuring clicks reliably trigger nested buttons, inputs, and controls without event dropping.

---

## 🏗️ Architecture Blueprint

```mermaid
flowchart TD
    subgraph UI["1. Target Discovery & Summoning"]
        CLI["CLI: aef summon / aef run"] --> Selector["Screen-Cast Selector (Displays & Windows)"]
        Selector --> Target["TargetInfo (HWND / XID / Rect)"]
    end

    subgraph Perception["2. Viewport Capture & Geometry"]
        Target --> CapturerRouter{"OS Dispatch"}
        CapturerRouter -->|Windows| WinCap["Win32 DWM / PrintWindow (PW_RENDERFULLCONTENT)<br/>+ Per-Monitor DPI-v2 Awareness"]
        CapturerRouter -->|Linux| LinCap["X11 Drawable Capture (XGetImage)<br/>+ mss multi-monitor fallback"]
        CapturerRouter -->|macOS| MacCap["ScreenCaptureKit (Planned)"]
        WinCap --> ViewportImg["Target-Relative Viewport Image"]
        LinCap --> ViewportImg
        MacCap --> ViewportImg
    end

    subgraph Calibration["3. Spatial Calibration & Vision"]
        ViewportImg --> Calib["Resolution Contract: (0,0) -> (W,H)"]
        Calib --> Grid["Optional Grid Ruler & SoM Markings"]
        Grid --> VLM["Vision-Language Model (GPT-4o, Claude 3.5, Qwen2-VL)"]
    end

    subgraph Execution["4. Dual-Mode Execution & Event Injection"]
        VLM --> Router{"Execution Mode"}
        Router -->|Minimal / CodeAct| REPL["Python REPL Sandbox (click, type, press, wait)"]
        Router -->|Guarded| GuardedEngine["JSON Schema Guarded Engine (Safety Intercepts)"]
        REPL --> Projector["CoordinateProjector (Local -> Screen / Window)"]
        GuardedEngine --> Projector
        Projector --> Driver["InputDriver (Win32 SendInput / X11 XSendEvent)"]
        Driver --> HitTest["Recursive Child Hit-Testing (_find_x11_child_at)"]
    end
```

---

## ⚖️ Dual-Mode Execution Comparison

| Feature | ⚡ Minimal Mode (CodeAct REPL) | 🛡️ Guarded Mode (Structured JSON) |
| :--- | :--- | :--- |
| **Protocol** | Python code block (```python ... ```) | JSON Schema (```json ... ```) |
| **API** | `click(x, y)`, `type_text()`, `press()`, `wait()` | `{"action": "click", "x": 100, "y": 200}` |
| **Throughput** | High (multi-action chaining in single LLM turn) | Deterministic (strict atomic step verification) |
| **Safety Net** | Restricted sandbox built-ins | Policy interceptors & Human confirmation prompts |
| **Best For** | Prototyping, web browsing, form filling | Financial operations, sensitive infrastructure, production |

---

## ⚡ Quick Start

### Installation

AEFlow is built using modern Python packaging with [`uv`](https://github.com/astral-sh/uv):

```bash
# Clone the repository
git clone https://github.com/mcocdaa/AgentEverywhereFlow.git
cd AgentEverywhereFlow

# Install dependencies based on your operating system
# On Linux:
uv pip install -e ".[linux]"

# On Windows:
uv pip install -e ".[windows]"

# Developer / Contributor setup:
uv pip install -e ".[dev,linux]"
```

### Configuration

AEFlow supports multiple flexible configuration methods with automatic cascading fallbacks:

#### Method 1: Instant CLI Configuration (Recommended)
Persist credentials globally to `~/.aef/.env` without editing files:
```bash
aef config --set-key "sk-your-openai-api-key"
aef config --set-model "gpt-4o"

# Optionally set a custom base URL (e.g. DeepSeek, OpenRouter, Qwen):
aef config --set-base "https://api.openai.com/v1"
```

#### Method 2: Standard Environment Variables
AEFlow automatically falls back to standard OpenAI environment variables:
```bash
export OPENAI_API_KEY="sk-your-api-key"
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_MODEL_NAME="gpt-4o"
```

#### Method 3: Local `.env` File
You can also place a `.env` in the current working directory:
```ini
AEF_MODEL_NAME=gpt-4o
AEF_API_KEY=your_api_key_here
AEF_BASE_URL=https://api.openai.com/v1
AEF_DEFAULT_MODE=minimal
```

---

## 🕹️ CLI Usage

```bash
# 1. Interactive screen-cast target picker and agent summoner
aef summon

# 2. List all available physical displays and active application windows with Target IDs
aef list-targets

# 3. Summon agent by Window Title substring
aef run --target "Chrome" --task "Search for GitHub Trending repositories"

# 4. Summon agent by Target ID or Native HWND (Precision targeting)
aef run --target hwnd:0x409dc --task "帮我过一下人机验证"

# 5. Summon agent onto an entire physical display
aef run --target display:1 --task "Organize desktop icons"

# 6. Run with step diagnostics and debug logging
aef run --target "Excel" --task "Calculate total" --debug

# 7. View current configuration
aef config

# 8. Check system info & installed version
aef version
```

---

## 💻 Programmatic Usage

You can embed AEFlow directly into your Python workflows:

```python
from agenteverywhereflow.capturer import get_capturer
from agenteverywhereflow.agent.loop import AgentLoop
from agenteverywhereflow.config import AppConfig, ExecutionMode

# 1. Discover target windows
capturer = get_capturer()
targets = capturer.list_targets()
target = targets[0]  # E.g. VS Code, Terminal, or Display 1

# 2. Initialize Agent Loop
loop = AgentLoop(app_config=AppConfig(default_mode=ExecutionMode.MINIMAL_PYTHON))

# 3. Run autonomous task
loop.run(target=target, user_task="Fill the form and click submit")
```

See [examples/](examples/) for more scripts, including custom model planner callbacks and interactive captcha demos.

---

## 🧪 Comprehensive Benchmarks

AEFlow includes a high-fidelity end-to-end benchmark suite testing real GUI windows and multi-step agent actions:

```bash
# Run comprehensive multi-scenario suite (Form, Calculator, Guarded Mode)
uv run python -m benchmarks.bench_suite

# Run autonomous goal convergence benchmark
uv run python -m benchmarks.bench_autonomous

# Run standard unit tests
uv run pytest tests/ -v
```

See [benchmarks/README.md](benchmarks/README.md) for benchmark specifications and results.

---

## 📂 Repository Layout

```text
AgentEverywhereFlow/
├── .github/                    # CI/CD workflows, issue templates, dependabot
├── agenteverywhereflow/        # Core package (CLI: aef)
│   ├── actions/                # InputDriver & CoordinateProjector
│   ├── agent/                  # AgentLoop, Prompts & VisionPipeline (SoM)
│   ├── capturer/               # Win32, X11 & interactive TargetSelector
│   ├── engine/                 # Minimal (CodeAct REPL) & Guarded engines
│   ├── cli.py                  # Typer CLI application
│   └── config.py               # Pydantic v2 application configuration
├── benchmarks/                 # Multi-scenario autonomous benchmark suite
├── docs/                       # Architecture, vision pipeline, and engine docs
├── examples/                   # Developer invocation examples & interactive demo assets
├── tests/                      # Unit tests (pytest)
├── pyproject.toml              # Build & dependency declarations
├── CONTRIBUTING.md             # Developer contribution guide
├── SECURITY.md                 # Security reporting policy
├── AGENTS.md                   # AI Coding Agent invariants & guidelines
└── CHANGELOG.md                # Release version history
```

---

## 🗺️ Roadmap

- [x] **v0.1.0 ~ v0.1.3 (Foundation & Ergonomics)**:
  - [x] Viewport abstractions: Display & Window enumeration with Target ID precision.
  - [x] Windows native DWM / `PrintWindow` capture with Per-Monitor DPI-v2 awareness.
  - [x] Linux X11 native drawable window capture & child hit-testing (`_find_x11_child_at`).
  - [x] Hardware-level Win32 mouse/keyboard injection & clipboard Unicode typing.
  - [x] Dual-mode execution engines (CodeAct REPL & Guarded Action Engine).
  - [x] Multi-block CodeAct sequence extraction & execution.
  - [x] Global configuration (`~/.aef/.env`) & `aef config` CLI tool.
  - [x] Real-world benchmark suite (Form, Calculator, Guarded) & interactive showcases.
- [ ] **v0.2.0 (Interactive HUD & Hotkeys)**:
  - [ ] Transparent floating HUD showing real-time agent reasoning steps.
  - [ ] Global hotkey summoning (`Win+Shift+A` / `Ctrl+Shift+A`) and instant emergency stop (`ESC`).
- [ ] **v0.3.0 (Ecosystem Expansion)**:
  - [ ] Linux Wayland / PipeWire portal integration.
  - [ ] macOS ScreenCaptureKit integration.

---

## 🤝 Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md) before submitting pull requests.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
