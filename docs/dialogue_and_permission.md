# 对话服务与权限管理体系 (Dialogue Service & Permission System)

AgentEverywhereFlow (AEFlow) 提供从**单次任务执行 (`aef run`)** 到**交互式多轮对话会话 (`aef chat`)** 以及**后台服务守护进程 (`aef serve`)** 的完整能力闭环，配合**全自动 / 全手动审批**双层权限安全栅栏，确保在复杂桌面交互场景下的可控性与安全性。

---

## 1. 核心架构与设计原则

```
                           ┌──────────────────────────────────────────────┐
                           │      Operator (User / Web UI / Client)      │
                           └──────────────────────┬───────────────────────┘
                                                  │
                         ┌────────────────────────┴────────────────────────┐
                         │                                                 │
                   [ aef chat ] (CLI REPL)                          [ aef serve ] (REST & WS)
                         │                                                 │
                         └────────────────────────┬────────────────────────┘
                                                  │
                                                  ▼
                                 ┌─────────────────────────────────┐
                                 │     ChatSession / Manager       │
                                 │  - Viewport Binding Target      │
                                 │  - Multi-Turn History Retention │
                                 │  - Sliding Window Context Prune │
                                 └────────────────┬────────────────┘
                                                  │
                                                  ▼
                                 ┌─────────────────────────────────┐
                                 │       PermissionGate            │
                                 │  - AUTO: Direct Execution       │
                                 │  - MANUAL: Operator Approval    │
                                 │  - Viewport Bounds Jail Check   │
                                 └────────────────┬────────────────┘
                                                  │
                                                  ▼
                                 ┌─────────────────────────────────┐
                                 │  Execution Engines & OS Drivers │
                                 │  - CodeAct REPL / Guarded JSON  │
                                 │  - Coordinate Projector (Local) │
                                 └─────────────────────────────────┘
```

---

## 2. 权限管理模式 (Dual Permission Modes)

AEFlow 实施清晰务实的两级权限管理：

| 权限模式 | 参数值 | 说明 | 适用场景 |
| :--- | :--- | :--- | :--- |
| **全自动执行 (Autonomous)** | `auto` | 模型规划的动作直接执行，不经过额外弹窗或确认，最高效率运行。 | 批处理测试、可信环境自动化、人机验证等自闭环任务 |
| **全手动审批 (Manual Approval)** | `manual` | **步步确认 (Human-in-the-Loop)**：每一个动作（点击、按键输入、快捷键、CodeAct 代码块）执行前，均暂停并展示详细动作参数与代码片段，等待操作员显式授权（Approve / Reject / 纠错反馈）。 | 涉及真实业务数据、生产环境、高敏感操作或需要实时监控干预的会话 |

### 审批反馈机制 (Rejection & Replanning Feedback)
当操作员在审批阶段选择拒绝（或提供文字纠错建议，如“*不要点击那个按钮，改点右上角搜索框*”）时：
- 该动作立即中止，不会下发给底层操作系统。
- 拒绝状态与操作员的纠错建议被回填至 LLM 的上下文消息中。
- Agent 立即读取操作员意图并重新制定替代动作规划。

---

## 3. 命令行交互式对话模式 (`aef chat`)

支持在终端内与目标窗口建立常驻连接，进行多轮自然语言交互与追问操作。

### 启动命令
```bash
# 1. 交互式选择目标窗口启动对话（默认全自动权限）
aef chat

# 2. 绑定指定窗口启动，并开启全手动审批模式
aef chat --target "hwnd:0x50914" --permission manual

# 3. 开启详细诊断模式
aef chat -t "notepad.exe" -p auto --debug
```

### 常用斜杠命令 (Slash Commands)
在 `aef chat` 交互提示符中，支持即时控制会话状态：
- `/help`：查看所有可用控制命令。
- `/perm`：查看当前权限等级；输入 `/perm manual` 或 `/perm auto` 随时动态切换。
- `/target [query]`：无缝将当前会话热切换到另一个窗口或屏幕，无需重启。
- `/status`：查看会话诊断数据（当前视口尺寸、已执行轮次、累计步数、当前状态）。
- `/clear`：重置对话上下文记忆，保留目标窗口绑定。
- `/exit` / `/quit`：优雅退出对话会话。

---

## 4. 后台对话服务守护进程 (`aef serve`)

基于 FastAPI 与 WebSocket 构建的工业级轻量化服务守护进程，对外开放标准的 RESTful API 与实时双向 WebSocket 流式通信，方便对接 Web 控制台、Electron 桌面悬浮窗或自动化编排系统。

### 启动服务
```bash
# 启动后台服务（默认监听 127.0.0.1:8000）
aef serve

# 自定义绑定地址与端口
aef serve --host 0.0.0.0 --port 9000
```
启动后自动生成交互式 Swagger 文档页面：`http://127.0.0.1:8000/docs`。

### 核心 REST API 概览

| 方法 | 端点 | 说明 |
| :--- | :--- | :--- |
| `GET` | `/api/v1/health` | 服务健康与活动会话状态检查 |
| `GET` | `/api/v1/targets` | 实时探测并列出当前物理屏幕与活动应用窗口 |
| `POST` | `/api/v1/sessions` | 创建并绑定新的对话会话 (`target_id`, `mode`, `permission_mode`) |
| `GET` | `/api/v1/sessions` | 列出所有活动会话列表 |
| `GET` | `/api/v1/sessions/{id}` | 获取特定会话的实时运行状态与诊断信息 |
| `POST` | `/api/v1/sessions/{id}/message` | 向会话发送执行指令（支持同步阻塞执行或异步后台执行） |
| `GET` | `/api/v1/sessions/{id}/approval` | 查询当前等待审批的高危/待办动作请求（手动审批模式） |
| `POST` | `/api/v1/sessions/{id}/approval` | 提交审批裁决（`approved: true/false`, 可选 `reason` 纠错说明） |
| `POST` | `/api/v1/sessions/{id}/permission` | 动态变更会话的权限模式 (`auto` / `manual`) |
| `POST` | `/api/v1/sessions/{id}/target` | 热切换当前绑定的目标窗口 |
| `GET` | `/api/v1/sessions/{id}/screenshot` | 实时获取目标窗口/视口的最新截图流 (image/jpeg) |
| `POST` | `/api/v1/sessions/{id}/reset` | 清除该会话的历史记忆 |
| `DELETE` | `/api/v1/sessions/{id}` | 关闭并注销会话 |

### 实时双向 WebSocket
- **端点**：`ws://127.0.0.1:8000/api/v1/sessions/{session_id}/ws`
- **下行事件流**：实时推送 `observe` (视口截图与尺寸), `reasoning` (模型思考过程), `action_proposed` (动作提议), `approval_required` (等待人工审批), `action_executed` (动作执行结果与物理输出), `task_completed`。
- **上行控制流**：客户端可直接通过 WebSocket 发送消息任务或提交操作审批。
