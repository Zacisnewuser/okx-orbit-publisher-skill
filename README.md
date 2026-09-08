# OKX Orbit Publisher · 星球运营 Skill

让你的 Agent 通过聊天，带你准备内容、配置并管理 OKX 星球运营。

这是面向 macOS 的开源 Agent Skill，附带一个仅使用 Python 标准库的本地状态工具。浏览器控制和定时调度由用户的 Agent 提供或按需安装，项目不捆绑第三方 Skill，不需要 OKX 交易 API Key。

**当前版本：2.0.1，早期社区预览。** 本地状态逻辑有离线回归测试；不同 Agent、浏览器工具与真实网页仍需逐项接入验收，不承诺安装后即可无人值守运行。本项目非 OKX 官方产品。

## 开始使用

1. 下载本仓库（GitHub 的 Code → Download ZIP），或克隆到本机。
2. 将 `skill/okx-orbit-publisher` 整个文件夹连同根目录 `LICENSE` 交给自己的 Agent，或按该 Agent 的本地 Skill 安装方式安装（请保留 MIT 许可证）。用下方构建命令生成的 Skill ZIP 已包含许可证。
3. 对 Agent 说：

   > 读取这个文件夹里的 SKILL.md，带我开始使用。先准备一份草稿，检查需要哪些工具。

Agent 会了解你的账号风格和目标，保存配置，并展示草稿。需要发布时，它会检查浏览器能力，提示你在 Chrome 登录星球、完成人脸/验证码并核对账号。默认暂停且处于模拟模式，由你决定何时开启确认模式或自动模式。

可随时说：“评论短一点”“以后晚上八点发”“暂停运营”“检查为什么没运行”“看看最近的效果”。

## 需要什么

| 功能 | 所需能力 |
|---|---|
| 聊天准备文案 | Agent 本身的文本能力；无需额外模型 Key |
| 保存设置、队列和历史 | 本地文件访问与 Python 3.9+（无需 pip 安装运行依赖） |
| 发布、互动、读取网页指标 | 用户选择的、可控制已登录可见 Chrome 的工具 |
| 定时运行 | 用户 Agent 的调度能力或用户另外安装的兼容工具 |

Codex 可使用 `agents/openai.yaml` 显示信息；Hermes、OpenClaw 等可读取通用 `SKILL.md`，具体工具按实际能力验收。没有浏览器仍可写稿，没有调度仍可手动触发。Skill 本身不提供常驻后台进程。

## 功能与边界

- 发帖/评论支持固定文案、AI、混合模式，提示词分别配置。
- 本地保存个人设置，换 Agent 后使用同一状态目录继续。
- 修改设置后旧批准失效；操作前检查额度、冷却、有效期和重复风险。
- 同一状态库只有一个执行中的公开任务；结果不明锁定，禁止自动重试。
- 浏览器失效时继续提供草稿和本地历史；恢复前核验时效，不补发积压任务。
- 本地自检、任务审计、备份恢复、24/72 小时观察记录。
- 粉丝关系管理默认关闭；名单不完整时不判断掉粉。

只操作星球内容区域，不进入交易/资产区域，不处理交易凭据，不绕过登录、人脸或验证码。提交最多点击一次，成功需公开证据。每日数量是上限，不为完成配额而发送无关内容。

本地工具管理状态，不是浏览器安全沙箱：页面事实、用户授权、语义相关性和外部工具行为仍由 Agent 核验。请按平台规则使用；不保证曝光、粉丝或收益。

## 本地数据

默认保存于 macOS 当前用户的 `~/Library/Application Support/okx-orbit-publisher/`，位于 Skill 目录之外。可用 `OKX_ORBIT_DATA_DIR` 或 `--data-dir` 指定路径；同一账号的多个 Agent 必须共享同一个目录。

安装和升级不包含作者账号或运营数据，也不自动启用调度。备份包含你自己的草稿、公开账号标识和历史，提交 Issue 时先脱敏。状态工具没有网络请求；你的 Agent/模型/浏览器对数据的处理由所选外部工具决定。

## 开发与验证

在仓库根目录执行：

```bash
python3 -m unittest discover -s tests -v
python3 tools/check_release.py
python3 tools/build_release.py
```

测试全部使用临时目录，不连接 OKX、不提交公开动作。构建生成仅含 Skill、辅助脚本、通用说明和 MIT 许可证的 ZIP，以及 SHA-256 校验文件。构建不包含个人数据、Git 历史或测试缓存。

- [Skill 入口](skill/okx-orbit-publisher/SKILL.md)
- [贡献指南](CONTRIBUTING.md)
- [安全问题报告](SECURITY.md)
- [发布检查结果](docs/PRE_RELEASE_REVIEW.md)
- [版本变更](CHANGELOG.md)

欢迎贡献浏览器/Agent 适配经验、离线失败样例、引导文案和回归测试。请先用 Issue 描述问题或建议，修复通过 Pull Request 提交。

## License

[MIT](LICENSE) © 2026 Zacisnewuser and contributors.
