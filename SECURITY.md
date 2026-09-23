# 安全策略 (Security Policy)

## 支持的版本 (Supported Versions)

我们仅针对最新主分支 (`main`) 及发布的稳定版本提供安全更新。

| 版本 (Version) | 支持状态 (Supported) |
| :--- | :--- |
| `0.1.x` | :white_check_mark: 当前主线支持 |
| `< 0.1.0` | :x: 不再维护 |

---

## 漏洞报告 (Reporting a Vulnerability)

如果您在 **AgentEverywhereFlow** 中发现了任何安全漏洞、越权风险或执行沙箱逃逸问题，请**不要**公开发布 Issue。

请通过以下安全途径直接向项目维护团队报告：
1. **GitHub Private Vulnerability Reporting**：在仓库顶部的 **Security** -> **Advisories** -> **Report a vulnerability** 提交加密安全报告。
2. **Email 联系**：通过维护者公开主页邮箱或私信联系 (`mcocdaa` / `2021137961@qq.com`)。

### 报告时请尽可能包含以下信息：
- 漏洞类别及潜在危害评估（如任意代码执行、权限绕过、越界键鼠注入等）。
- 受影响的模块、操作系统（Windows / Linux / macOS）以及重现版本。
- 最小可复现步骤 (Minimal Reproducible Example / PoC)。
- 任何修复建议或临时缓解方案。

### 响应承诺
- 维护团队将在 **48 小时** 内完成初步审阅并给出评估反馈。
- 修复补丁将在独立私有分支上验证完毕后发布正式安全更新，并公开致谢报告者。
