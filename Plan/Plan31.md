# Plan31：CLI-first Agent Backend、显式状态与 Session 恢复策略

## 日期

2026-09-01（归档截至北京时间 2026-09-01 00:06 的新增策略讨论；Track 对应 2026-08-31）

## 讨论主题

确定首版 Multi-Agent Coding Harness 的 Agent 执行后端、状态真相、工具环境隔离、Backend Session 连续性与失效恢复策略。

## 目标与背景

原路线倾向直接接模型 API，但用户希望首版尽快获得成熟 Coding Agent 的工具循环，同时仍由本项目 Runtime 控制多 Agent 的任务分解、RoleAssignment、通信、权限、状态、收敛与验收。另一个关键问题是：CLI Session 可以提升连续对话效率，却不能成为任务恢复的唯一真相；Session 失效时也不能因猜测 stderr 而静默重复调用。

## 候选方案对比

| 决策点 | 候选方案 | 核心思路 | 优点 | 缺点、成本与风险 | 适用条件 |
|---|---|---|---|---|---|
| 首个 Agent Backend | API-first | Runtime 自建模型调用与工具循环 | 控制粒度高，协议直接 | 首版需补齐大量成熟 Agent loop 能力，交付慢 | 需要高度定制推理/工具编排时 |
| 首个 Agent Backend | CLI-first | 用成熟 Codex CLI 承担单 Agent 工具循环，通过统一 Executor 接入 | 更快获得可工作的 Coding Agent；可复用订阅认证 | CLI 协议、错误语义、Session 和资源成本受外部实现约束 | 当前本地单机学习型产品首版 |
| 业务状态 | 依赖 CLI Session 隐式保存 | 把上下文与进度交给供应商 Session | 实现简单、Prompt 较短 | Session 丢失即失去真相，难审计、迁移和重放 | 仅可作为非关键优化 |
| 业务状态 | Runtime 显式持久化 | Task/Snapshot/Permission/Artifact/Recovery Context 均由 Runtime 持有 | 可恢复、可审计、Backend 可替换 | 需要不可变 Schema、digest 和迁移维护 | 本项目 Harness 核心路径 |
| 工具环境 | 继承核心环境并逐项排除 | `inherit=core` 加秘密变量过滤 | 工具兼容性较好 | 新变量可能默认穿透，黑名单难证明完整 | 低风险、受信环境 |
| 工具环境 | 默认拒绝后最小放行 | `inherit=none`，只设置固定安全 PATH | 边界清晰，新变量不会自动泄漏 | 某些工具需逐项补充 allowlist | 当前安全优先的本地 Agent |
| Session 失效恢复 | stderr/自然语言匹配 | 根据错误文本猜测 Session 不存在 | 可自动恢复，开发快 | 易把认证、配置、启动失败误分类；文本不稳定且可能泄密 | 不满足当前安全门槛 |
| Session 失效恢复 | 启动前失败统一自动 fallback 一次 | 无 JSONL 事件时自动新建 Session | 用户体验连续，恢复延迟低 | 认证或配置错误也会多调用一次，可能增加费用与副作用 | 用户明确接受误分类成本时 |
| Session 失效恢复 | fail-closed 人工确认 | 首次失败持久暂停，用户确认后用权威 Context 新建 Session | 不静默重试；费用、副作用和误分类可控 | 多一次用户交互，需持久确认状态 | 当前证据不足且安全优先时 |
| 系统自身演进 | Agent 自动采纳修改 | Agent/Reviewer 评测通过后自动改 Prompt、Role、Policy 或系统代码 | 自动化程度高 | 权限扩大、目标漂移和不可逆治理风险 | 当前禁止 |
| 系统自身演进 | 精确 ChangeProposal 用户批准 | 每个不可变 change digest 逐次审阅批准 | 边界明确、可追踪、可撤销 | 增加审批与持久状态实现成本 | 后续开放系统自写前的前置条件 |

## 最终选择

1. 首版采用 **CLI-first**，首个成熟 Backend 为 Codex CLI；DeepSeek 等 API Backend 保留为供应商无关的 RawModelBackend 对照，不删除。
2. 采用 **协议无状态、业务状态显式化**：CLI 每次调用独立自描述，Runtime 持有 Task、Snapshot、Permission、Artifact、结果和恢复上下文；Backend Session 只作为私有、可替换的连续性优化。
3. Agent 工具环境采用 **默认拒绝**：`inherit=none` 加固定安全 PATH，Runtime 只公开白名单布尔观察，不公开环境值或原始工具输出。
4. Backend Session 按 Scope/Thread/Agent/Backend 私有持久绑定；恢复上下文和恢复链 append-only，普通失败、timeout、stderr 文本或损坏 JSONL 均不得触发恢复。
5. 用户已选择 **fail-closed 人工确认恢复**：旧 Session 失败后先暂停并持久化等待状态，只有绑定当前 Invocation 与状态版本的一次性用户确认才能从权威 Context 新建 Session。
6. 产品禁止自主进化。Agent 只能提交绑定精确 digest 的 ChangeProposal；Prompt、Role、权限、Skill、路由、验收和系统代码等变更必须逐次由用户明确批准。

## 选择理由

CLI-first 能缩短首版可用 Coding Agent 的实现时间，同时统一 Executor 保留未来替换 Backend 的能力。显式 Runtime 状态避免把供应商 Session 当成任务真相，使重放、审计和恢复可验证。真实无效 Session 探针只得到非零退出与 stderr 存在，没有结构化 stdout 事件；因此自动判断 Session 失效缺乏可靠证据。人工确认虽然增加一次交互，但不会把认证、配置或启动失败误判成可恢复错误，也不会静默增加调用、费用或副作用。

放弃 API-first 作为首发路线，不代表放弃 API；其主要原因是首版还需自行补齐单 Agent 工具循环。放弃黑名单式环境继承，是因为两次真实复验均不能关闭泄漏风险。放弃 stderr 匹配与默认自动 fallback，是因为当前 CLI 没有稳定公开错误合同，无法安全区分失败类型。放弃自主进化，是因为 Agent、投票或 Validator 均不能代替用户对系统治理面的授权。

## 架构或流程

```text
User / Web
  → Runtime：Task、RoleAssignment、Permission、Message、Acceptance
  → AgentExecutionRuntime：核对显式状态信封与持久 Authority
  → AgentExecutor：调用 Codex CLI（单 Agent 工具循环）
  → 私有 Backend Session 绑定与脱敏 Result

Session 恢复：
resume 失败
  → 无可靠结构化失效证据则 fail-closed
  → 持久化 awaiting_user_confirmation
  → 用户对当前 Invocation/State 一次性确认
  → 从不可变 Recovery Context 新建 Session 一次
  → 持久化结果或稳定失败状态
```

## 执行步骤

1. 以 TDD 增加“resume 失败后只调用一次并持久等待确认”的公共行为测试。
2. 增加 append-only 恢复请求与用户决定记录，绑定 Invocation、State digest、Recovery Context digest 和当前私有 Session。
3. 实现跨 Runtime 重建后的确认接口；正确确认只允许一次无旧 Session ID 的新调用。
4. 覆盖拒绝、过期/错误确认、重复点击、第二次恢复失败和普通启动失败等零额外调用路径。
5. 在单独授权下验证真实有效 resume 与人工确认恢复，不把 Fake 或 CLI 退出码当作产品验收。
6. 再继续本地 API/Web、第二 Agent 协作、Mailbox ACK/重投和精确 ChangeSet 批准门。

## 约束与风险

- Runtime 独占多 Agent 路由、权限、状态、终止、审计和 Acceptance；CLI 不得私下调度其他 Agent。
- Session ID、认证材料、stderr、原始工具输出、Workspace 路径和私有推理不得进入 Message、公开事件或前端。
- 当前真实 Codex 短任务约消耗三万输入 Token、延迟约 17～23 秒，首版需继续监控成本与延迟。
- 自动恢复缺少可靠错误分类；人工确认接口必须防跨 Invocation、跨 Agent、跨 Thread、跨 Backend 和跨状态版本复用。
- Backend 成功但持久提交前崩溃的 claim/fencing 窗口尚未关闭，不能宣称 exactly-once。
- 真实调用、网络、额度、push、tag 与 deploy 均需各自授权，本文不扩大权限。

## 待验证事项

- 当前 Codex CLI 的真实有效 Session resume 是否稳定，并能否提供可版本化的失败信号。
- 人工确认恢复能否在进程重启后保持幂等、私密且只调用一次新 Session。
- 默认安全 PATH 是否覆盖后续 Coding 任务所需工具；新增变量必须逐项最小放行。
- 显式 Context 重建的 Token 成本、延迟与结果质量是否可接受。
- Backend 完成与结果提交间的崩溃恢复、并发 claim 和 fencing 语义。

## 待办事项

- 完成 `PRODUCT-01C-MANUAL-SESSION-RECOVERY-CONFIRMATION` 的 TDD 纵切。
- 实现精确 ChangeSet 的 `PROPOSED → USER_APPROVED → APPLIED` 门禁。
- 建设本地 Product API/Web 入口和公开状态投影。
- 后续实现 Mailbox ACK、失败重投、崩溃恢复、取消与完整收敛/评测。
- 保留 API Backend 适配层，并在首版稳定后验证 DeepSeek、Qwen、Kimi 的一致合同。
