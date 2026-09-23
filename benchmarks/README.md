# AEFlow Benchmark Suite (端到端具身基准测试集)

本目录包含了 **AgentEverywhereFlow (AEFlow)** 的自动化端到端高保真基准测试套件。  
所有用例均基于真实的操作系统原生窗口（X11 / Win32），模拟智能体完整的 **感知 (Perception) -> 决策 (Planning) -> 视口坐标投影 (Projection) -> 键鼠注入 (Driver Execution) -> 任务终止检测 (Verification)** 闭环。

---

## 📋 包含的基准测试场景 (Scenarios)

| 基准编号 | 模块 / 场景 | 核心验证能力 | 判定成功条件 |
| :--- | :--- | :--- | :--- |
| **Benchmark 1** | **企业多控件表单 (`run_benchmark_form`)** | 输入框焦点定位、文本键入、单选按钮 (Radiobutton)、复选框 (Checkbutton)、提交按钮点击与状态机反馈 | 表单成功提交且校验字段完全匹配 |
| **Benchmark 2** | **连续高精度计算器 (`run_benchmark_calculator`)** | 复杂网格紧凑按钮命中、算术表达式链式点击操作（`25 * 4 =`）、LCD 屏幕显示读取 | 最终 LCD 显示准确计算结果 `100` |
| **Benchmark 3** | **受控模式状态机 (`run_benchmark_guarded`)** | 结构化 JSON 动作（`click`, `type`, `finish`）、安全门禁流转与人机协同校验 | 严格模式下完成 payload 注入与确认 |
| **Autonomous Run** | **多步自主目标收敛 (`bench_autonomous.py`)** | 计数器动态递增、文本框同步输入与自主任务完成宣称 (`TASK_COMPLETED`) | 计数器达到目标值且返回成功标记 |

---

## 🚀 运行基准测试 (How to Run)

> **注意**：基准测试需要本地图形环境（Windows 桌面、Linux X11/XWayland 桌面或虚拟显示 `xvfb`）。

```bash
# 1. 运行三合一综合基准测试套件
uv run python -m benchmarks.bench_suite

# 2. 运行单用例自主目标收敛测试
uv run python -m benchmarks.bench_autonomous

# 3. 在无头服务器 (Headless Linux CI) 中借助 Xvfb 运行
xvfb-run -a uv run python -m benchmarks.bench_suite
```

---

## 🔍 调试与产物分析

基准测试运行过程中的每一步感知截图与决策日志均会按测试用例分目录保存在 `.aef_cache/` 下：
- `.aef_cache/bench_form/`
- `.aef_cache/bench_calc/`
- `.aef_cache/bench_guarded/`

可通过检查各步骤截图（`step_1_before.png`, `step_1_after.png` 等）直观复现 Agent 的动作轨迹。
