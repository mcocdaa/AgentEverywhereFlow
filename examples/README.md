# AEFlow Examples (开发者调用样例)

本目录提供了 **AgentEverywhereFlow (AEFlow)** 的常用代码示例，帮助开发者快速将 AEFlow 集成进自己的 Agent 系统或自动化流水线中。

---

## 📂 样例列表 (List of Examples)

1. **[quickstart_summon.py](quickstart_summon.py)**:
   - 展示如何通过 Python API 枚举系统当前活动窗口与物理显示器。
   - 对指定目标进行零拷贝截图，并初始化 `AgentLoop` 运行环境。
2. **[custom_planner.py](custom_planner.py)**:
   - 展示如何通过 `planner_func` 自定义规划器钩子注入自己的本地模型（如 Ollama、vLLM、Qwen-VL、InternVL）、规则启发式算法或测试桩。

---

## 🚀 运行示例

```bash
# 运行快速启动枚举示例
uv run python examples/quickstart_summon.py

# 运行自定义规划器钩子示例
uv run python examples/custom_planner.py
```
