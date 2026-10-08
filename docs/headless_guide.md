# AgentEverywhereFlow (AEFlow) — 纯后台无感运行与无头隔离指南

本指南面向希望在**物理屏幕 100% 不受打扰、窗口不抢焦点、完全静默运行**的用户与开发者。

AEFlow 提供两套业界标准的系统级通用无头隔离方案：
- **方案 B：Windows 虚拟显示器（VDD）** —— 适合 Windows 宿主机日常办公与开发，保留原生软件完整生态与登录态。
- **方案 C：Docker + Xvfb 工业级无头容器** —— 适合 Linux 云服务器、24 小时无人值守部署与集群自动化。

---

## 方案 B：Windows 虚拟显示器（Virtual Display Driver）

### 1. 核心原理
通过 Windows 系统的 Indirect Display Driver (IddCx) 规范，开辟一块物理人眼看不见的虚拟屏幕（如 `Display 2 (1920x1080)`）。
- Windows 会将这块屏幕视为真实的硬件显示器，支持完整的 GPU 硬件加速与 DirectX 绘图；
- 目标应用（如微信、浏览器）运行在虚拟屏幕空间内，可以在该空间内自由置顶、获取焦点、点击与敲字；
- **您的物理主屏幕完全不显示任何弹出窗口，物理鼠标指针不受任何干扰**。

### 2. 1分钟安装虚拟显示器驱动
推荐使用 GitHub 广泛采用的成熟开源项目 [Virtual-Display-Driver](https://github.com/itsmikethetech/Virtual-Display-Driver)：
1. 下载最新的驱动发布包解压；
2. 右键管理员运行 `Virtual-Display-Driver-Control.exe` 或安装证书驱动；
3. 打开 Windows 设置 -> **系统** -> **屏幕**，即可看到多出了一块 **“显示器 2”**（分辨率推荐设为 1920×1080）。

### 3. 在 AEFlow 中调度与使用

#### 命令行一键迁移并执行
```bash
# 1. 查看当前所有物理与虚拟屏幕
aef list-targets

# 2. 将微信一键搬移到虚拟屏幕（Display 2）
aef move "微信" --to-display 2

# 3. 或者直接在启动任务时自动移动并执行
aef run "微信" --task "给文件传输助手发一条测试消息" --to-display 2
```

#### 通过 REST API 调度
```bash
# 调用接口将目标窗口一键移动到虚拟显示器
curl -X POST http://127.0.0.1:8000/api/v1/targets/微信/move \
     -H "Content-Type: application/json" \
     -d '{"display_id": "display:2"}'
```

---

## 方案 C：Docker + Xvfb 纯无头容器部署

### 1. 核心原理
利用 Linux 工业级虚拟显存 `Xvfb`（X Virtual Framebuffer），在容器内部模拟一块无物理显示器的独立 X11 图形环境。所有界面渲染、按键与鼠标事件均在内存显存中完成，对外仅提供 WebUI 与 HTTP 接口。

### 2. 一键启动无头容器
在 AEFlow 项目根目录下执行：
```bash
# 启动 AEFlow 无头容器
docker compose up -d
```

启动后：
- **WebUI 与 API 服务**：浏览器访问 `http://localhost:8000` 即可进入 AEFlow Studio。
- **实时 VNC 观测流（可选）**：如果想亲眼看看虚拟容器里的桌面画面，可使用任何 VNC Viewer 连接 `localhost:5900`（无密码）。

### 3. 环境变量配置 (`docker-compose.yml`)
| 变量名 | 默认值 | 作用说明 |
| :--- | :--- | :--- |
| `RESOLUTION` | `1920x1080x24` | 虚拟显存屏幕分辨率与色深 |
| `PORT` | `8000` | AEFlow WebUI 与 REST API 监听端口 |
| `ENABLE_VNC` | `true` | 是否启动 VNC 端口（5900）供开发者远程观摩 |
| `AEF_MODEL_NAME` | `deepseek-chat` | 调用的视觉语言模型名称 |
| `AEF_API_KEY` | - | 模型 API Key |
