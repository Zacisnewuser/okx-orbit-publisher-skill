# 本地状态与执行协议

## 数据位置

随包 `scripts/orbit_state.py` 是 Python 3.9+ 标准库工具，无第三方包、网络、浏览器或调度依赖。Agent 使用实际已安装 Python 路径调用 `python3 <Skill目录>/scripts/orbit_state.py <命令>`。内部命令不要强迫普通用户手工输入。

默认状态为当前用户 `~/Library/Application Support/okx-orbit-publisher/state.sqlite3`，不在 Skill 安装目录。可用 `--data-dir <目录>` 或 `OKX_ORBIT_DATA_DIR` 显式覆盖；不同 Agent 必须使用同一个目录。默认单账号，同一数据库不允许更换已绑定账号；多账号需要不同目录且分别验收。

第一次运行 `init` 默认暂停且 dry-run；已有状态时 init 只读取，不重置。`status` 返回设置、队列、暂停原因、配置版本、短期 preflight、浏览器会话租约与 24/72 小时检查。输出交给 Agent 整理，日志不包含凭据。

## 命令合同

| 命令 | 输入及行为 |
|---|---|
| `init / status / doctor / export` | 初始化或读取；doctor 只诊断本地；export 返回状态与审计 JSON 到 stdout |
| `configure --file PATH` | 深度合并配置 JSON；拒绝未知字段；不覆盖其他设置，改动撤销旧批准和 preflight |
| `pause --reason TEXT` | 立即阻止新 claim，清除 preflight；外部调度由 Agent 另行暂停 |
| `preflight --file PATH` | 记录 Agent 只读验收证据，5 分钟有效；不代替真实页面核验 |
| `resume --reason TEXT` | 记录用户恢复要求，需 confirm/auto、有效 preflight、没有未知提交；不启动调度 |
| `acquire` | 返回随机 owner，取得 120 秒浏览器会话租约；过期才可替换 |
| `renew / release --owner TOKEN` | 每 60 秒内续租；结束释放；续租失败立即停止新浏览器动作 |
| `enqueue --file PATH` | 保存草稿，生成唯一 ID；规范化文字去重及同类型同目标去重 |
| `approve --id ID --reason TEXT` | 写入具体用户批准或自动策略审核依据，绑定当前配置版本 |
| `cancel --id ID --reason TEXT` | 取消尚未尝试的 draft/approved 草稿；修改正文时取消旧稿再建立新任务，不可取消 executing/unknown |
| `claim --id ID --owner TOKEN` | 原子校验并标记 executing；返回 submit_once 才可点击一次 |
| `finish --id ID --result verified/unknown/not_submitted --reason EVIDENCE [--object-id ID]` | 保存结果；verified 必須稳定公开对象 ID；not_submitted 只在确定未点击时使用 |
| `resolve-verified --id ID --object-id ID --reason EVIDENCE` | 只读证据解决 executing/unknown，不再提交 |
| `resolve-absent --id ID --reason USER_CONFIRMATION` | 仅用户明确确认未提交后终结未知任务，不能把“查不到”当作证据 |
| `observe --id ID --file PATH` | 记录到期的 24/72 小时只读观察；JSON 包含 hours、evidence、metrics，缺失字段用 unavailable |
| `backup --output PATH` | SQLite 一致性备份，拒绝覆盖已有文件 |
| `restore --file PATH` | 恢复到指定的新数据目录，强制暂停并清除 preflight，拒绝覆盖现有目录 |

每个 SQLite 写入用事务串行化，executing/unknown 是持久化全局写入锁；不能因为会话租约过期就重新提交。工具没有长期常驻进程。acquire 用于短期协调浏览器页面，claim 用于持久化防重复提交，二者用途不同。

enqueue 自动将从未尝试且过期的草稿标为 expired，允许原帖重新核验后用新 ID 排队；cancelled/expired 保留历史，不再阻挡新草稿。已尝试任务及已发布历史不会自动过期。启动权限和过期检查在数据库锁取得后使用最新时间。

## 输入示例（Agent 内部使用）

配置补丁，仅写需要修改的字段：

```json
{"content":{"persona":"简短、自然，重视数据与复盘","comment_prompt":"回应一个真实细节"},"schedule":{"post_times":["20:00"]}}
```

完整可用字段见脚本 `DEFAULT`，`status.config` 是当前真实值。没有个人信息的默认值由 init 生成。`account_id` 为公开星球账号 ID，`allowed_orbit_prefixes` 从实际页面核验后配置，例如某个已确认的 `https://www.okx.com/zh-hans/orbit` 路径；例子不证明当前站点必定使用该路径。脚本只支持 Orbit 命名空间；页面使用其他路径时停下更新适配，不能放开整站。

preflight：

```json
{"account_id":"实际公开账号ID","url":"已验收的星球URL","browser_readable":true,"logged_in":true,"no_challenge":true,"dry_run_passed":true,"evidence":"工具名、读取时间、账号与模拟结果"}
```

若配置绑定旧项目，还必须完整阅读相关历史队列、已发布记录、未知提交和个人政策，在重复检查时联合查询旧历史。完成后填写 `legacy_history_checked: true`。此字段是 Agent 的证据声明，不能假装已自动迁移了旧数据。

任务：

```json
{"kind":"comment","target_id":"原帖ID","author_id":"作者ID","url":"已核验的原帖URL","text":"草稿完整正文","source":"risk_discipline","tag":"specific_detail","ttl_hours":24}
```

post 的 target_id 使用当前公开账号 ID，URL 使用其已核验主页；关系动作 target_id 和 author_id 均为对方公开账号 ID。kind 支持 post/comment/like/follow_back/unfollow。配置里默认关闭后两者。

claim 检查配置版本、暂停/模式、启用功能、URL、黑名单、取关保护、任务有效期、preflight、全局未知提交、额度和间隔。额度按配置时区计算，为避免崩溃漏计，所有已 claim 尝试都占用当日额度和冷却，不只统计成功。成功数单独从 verified 计算。Agent 还需检查近似语义重复、每日单作者限制、关系证据、内容格式和调度活跃窗口。

## 其他本地记录

schedule.post_times 必须是 HH:MM 列表，active_hours 是 [["09:00","21:00"]] 形式的起止时间列表（可跨午夜），registrations 每项至少有非空 provider/id/function/status；时间窗口由 Agent/调度器实际执行，存储工具只验证格式。

发现断点、原始指标与通知去重可保存为同一数据目录内的 JSON，写入使用临时文件和原子替换。登记任务放 `config.schedule.registrations`，每项保存 provider/id/function/time/timezone/status/verified_at。`enabled` 只是用户意图，外部状态以调度工具验收为准。

SQLite 保存已验证任务 pending 检查；Agent 只读追踪后用 observe 保存观察，自动保留实际时间与延迟；账号级快照另存指标文件。工具不自动读取网页，也不实现后台调度。使用侧文件备份时与数据库一起备份。

凭据不进入配置/任务/日志。用户想接独立模型时使用宿主的密钥管理，不能在聊天索要粘贴到本地明文 JSON。
