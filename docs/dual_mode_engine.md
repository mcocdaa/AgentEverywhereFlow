# 双模式执行引擎与安全体系 (Dual-Mode Execution & Safety)

## 1. 为什么需要双模式？

在具身桌面智能体的实际落地中，存在两种截然不同的典型诉求：
1. **追求极致效率与复杂逻辑组合**：
   开发者希望 Agent 能直接写一段代码，连续执行诸如“点击输入框 -> 键入文本 -> 按下回车 -> 等待 1 秒 -> 滚动页面”这样的一气呵成的复合行为（CodeAct 模式）。
2. **追求可控合规与高安全性**：
   在企业内部或敏感桌面环境下，任何可能具有破坏性的行为（关闭窗口、执行终端命令、删除文件）必须经过结构化参数解析、人机协同确认或安全策略拦截。

为了满足不同层次的生产要求，**AgentEverywhereFlow** 提供了内建的 **双模式执行架构**。

---

## 2. 模式对比一览

| 维度 | 极简模式 (Minimal CodeAct REPL) | 受控模式 (Guarded Action Engine) |
| :--- | :--- | :--- |
| **交互协议** | Markdown Python 代码块 (```python ... ```) | 严格 JSON Schema (```json ... ```) |
| **主要方法** | `click(x, y)`, `type_text()`, `press()`, `wait()`, `hotkey()` | `{"action": "click", "x": 100, "y": 200}`, `{"action": "type", ...}` |
| **执行载体** | 受限沙箱 Python REPL (`agenteverywhereflow.engine.python_repl`) | 状态机分发器 (`agenteverywhereflow.engine.guarded`) |
| **执行延迟** | 极低（单次网络调用即可完成多个顺序原子动作） | 确定（严格逐动作用例流转与检查） |
| **安全机制** | 限制内置函数，禁用危险 `os`/`sys` 挂钩 | 敏感动作拦截器、人机确认门禁 (`require_human_confirmation`) |
| **适用场景** | 快速原型、日常网页搜索、高频连续点击表单填写 | 金融交易、企业办公敏感系统、生产运维管控 |

---

## 3. 受控模式 JSON Schema 规范

在受控模式下，模型必须输出符合以下 JSON Schema 的动作字典：

### 3.1 鼠标点击
```json
{
  "action": "click",
  "x": 240,
  "y": 180,
  "button": "left"
}
```

### 3.2 文本键入
```json
{
  "action": "type",
  "text": "Hello World"
}
```

### 3.3 按键与快捷键
```json
{
  "action": "press",
  "key": "Return"
}
```

### 3.4 滚轮与等待
```json
{
  "action": "scroll",
  "delta": -3
}
```

### 3.5 任务完成宣告
```json
{
  "action": "finish",
  "message": "User goal accomplished successfully."
}
```
