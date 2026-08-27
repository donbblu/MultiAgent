# Multimodal Multi-Agent Harness

这是一个以通用 **Multi-Agent Runtime** 为执行内核的、可交互且可长期演进的 **Multi-Agent Harness**。项目关注的不是堆叠 Agent 或模型数量，而是让不同专业 Agent 在明确上下文、权限、预算、生命周期和验收边界内协作，并让交付可追溯、部分状态可持久化、失败边界可观察。完整崩溃恢复和事故学习仍属于后续 Roadmap。

固定术语：

- **Harness 是项目本体**：组合角色与模型策略、任务拆分与路由、Context/Memory、工具、协作、评测、安全、事故学习和 Skill；
- **Runtime 是执行内核**：管理 Thread、Message、Invocation、AgentSession、状态、并发、取消/恢复、权限、预算、Event 和 Acceptance；
- **Plugin 是专业能力**：Coding、VisionForge 及后续场景通过受控协议接入；
- **Model/Backend 是可替换负载**：只能提交候选 Artifact/Evidence，不能拥有状态、扩大权限或自行宣告完成。

```text
Multi-Agent Harness
├── Durable Runtime Kernel
├── Agent Orchestration / Context / Memory / Tools
├── Eval / Security / Incident Learning / Skills
└── Coding / VisionForge / future Plugins
```

## Quickstart：默认离线作品集 Demo

> 当前是已完成本地发布检查的 **作品集 Agent Runtime MVP 候选**，但不是生产系统、
> Runtime Acceptance 或已对外发布版本。默认入口会
> 创建持久 Thread、AgentInstance 和 AgentSession，经 SQLite Mailbox 投递结构化 Message，
> 并在共享线程池的 Agent 泳道中执行现有 scripted Worker。Runtime 仍拥有 Validator 和
> 最终状态裁决权；模型、Worker 与 Agent 都不能自行宣告完成。

要求 Python 3.10+。从仓库根目录运行唯一推荐入口：

```bash
python3 demo/portfolio_demo.py --trusted-local-execution
```

`--trusted-local-execution` 只批准固定 Suite 已登记的本地 Python 验证命令。缺少该参数
时，入口会在加载 Suite、创建 Workspace、启动 Validator 和改写报告前以退出码 `2`
拒绝。这个 Demo 不读取 `.env`，不访问网络，不启动 Web/Browser，也不调用真实模型。

一次成功运行会展示 Runtime 汇总与公开执行时间线；它包含 Thread、Agent、Session
生命周期、Mailbox/Handoff、Artifact、Validator 和结果，不包含模型私有推理。以下
标识符使用占位符；每次运行会生成不同 ID，但冻结的语义结果不变：

```text
mode=scripted/offline network=false real_provider=false external_model_calls=0
runtime scope=portfolio-demo threads=9 agents=21 sessions_closed=21 mailbox_sent=42 mailbox_received=42 stage_messages=21 handoffs=12 fifo=true max_parallel_agents=3
role=Planner stage=plan Artifact=core:plan ArtifactRef=artifact://<artifact-id> Validator=none result=completed thread_id=portfolio-<run-id>-<task>-<strategy> agent_id=agent-<run-id>-<trial>-planner session_id=session-<run-id>-<trial>-planner session_state=closed lifecycle=created>paused>resumed>closed message_id=message-<run-id>-<n> handoff=false
role=Developer stage=implement Artifact=core:patch ArtifactRef=artifact://<artifact-id> Validator=none result=completed thread_id=<same-thread> agent_id=agent-<run-id>-<trial>-implementer session_id=session-<run-id>-<trial>-implementer session_state=closed lifecycle=created>paused>resumed>closed message_id=message-<run-id>-<n> handoff=true
role=Validator stage=initial_validation Artifact=core:validator_feedback ArtifactRef=none Validator=runtime-owned fixed suite result=failed thread_id=<same-thread> agent_id=none session_id=none session_state=runtime-owned lifecycle=runtime-owned message_id=none handoff=false
role=Tester stage=diagnose Artifact=core:test_diagnosis ArtifactRef=artifact://<artifact-id> Validator=none result=completed thread_id=<same-thread> agent_id=agent-<run-id>-<trial>-tester session_id=session-<run-id>-<trial>-tester session_state=closed lifecycle=created>paused>resumed>closed message_id=message-<run-id>-<n> handoff=true
role=Fixer stage=fix Artifact=core:patch ArtifactRef=artifact://<artifact-id> Validator=none result=completed thread_id=<same-thread> agent_id=agent-<run-id>-<trial>-fixer session_id=session-<run-id>-<trial>-fixer session_state=closed lifecycle=created>paused>resumed>closed message_id=message-<run-id>-<n> handoff=true
role=Validator stage=final_validation Artifact=core:validator_feedback ArtifactRef=none Validator=runtime-owned fixed suite result=passed thread_id=<same-thread> agent_id=none session_id=none session_state=runtime-owned lifecycle=runtime-owned message_id=none handoff=false
status=passed tasks=3 trials=9 delivered=6 expected_failures=3 repaired=3 external_model_calls=0 report=demo/.runs/portfolio-demo/report.json
```

三个 Single-Agent Trial 的失败是冻结的对照结果，不代表 Demo 失败。只有完整结果精确
匹配 9 个 Trial、6 个交付、3 个预期失败、3 个成功修复、21 次 scripted worker
调用、0 次真实模型调用，且没有 `UNKNOWN`、非预期 Validator 失败或安全偏差时，顶层
状态才是 `passed`。

完整 `portfolio-demo-report/v2` 报告写入 `demo/.runs/portfolio-demo/report.json`，使用
临时文件加原子替换；重复运行只覆盖这一份专用报告。Agent Runtime 状态保存在同目录的
`runtime.sqlite3`，每次运行以唯一 ID 追加证据，而不是替换数据库。该目录被
`.gitignore` 覆盖，Trial Workspace 也会在运行后清理，因此两者都是可复现的本地运行
产物，不是已提交的用户项目。`portfolio-demo-report/v1` 只代表历史 preview 契约。

## Demo 闭环与架构

```text
固定 Coding Suite（3 个任务）
        ↓
每个 Trial 创建持久 Thread、AgentInstance 和 AgentSession
        ↓
SQLite Mailbox 投递 bootstrap/work Message
        ↓
共享线程池的 AgentLaneRuntime：同 Agent FIFO、跨 Agent 并行
        ↓
WorkerRegistry 按 Role 路由 scripted Worker并提交结构化 Artifact
        ↓
ArtifactStore → PatchIntegrator → 临时 Workspace
        ↓
Runtime-owned build / unittest / CLI Validator
        ├─ 首轮通过 → 交付
        └─ 首轮失败 → Tester 诊断 → Fixer 局部修复 → 再验证
        ↓
结构化 Handoff + 公开时间线 + portfolio-demo-report/v2 JSON 报告
```

作品集 Demo 的正式产品面是 CLI 和结构化报告。仓库仍保留 Coding 通用 CLI、现有 Web
工作台与 VisionForge 场景，但它们不是默认 Quickstart：通用 CLI 可进入真实 Provider
路径，Web 任务索引仍为进程内状态，且尚未成为完整持久 Thread/Agent 泳道。

## 当前状态

项目目前是一个 **已完成本地作品集发布检查、但未达到生产级的原型**。`MVP-AGENT-RUNTIME-01A～01D` 已完成 Agent 实体与 SQLite Store、Mailbox 与执行泳道、真实 Handoff 和 Demo 接入；本地候选 `cbb35e3` 的干净检出 Quickstart、回归、compile、差异门禁和最终独立审查均通过，初审唯一文档新鲜度发现已关闭，最终 0 finding。默认入口精确得到 9 Trial、6 交付、3 个预期失败、3 个修复成功、21 次 scripted 调用和 0 次模型调用；这些结果只证明冻结场景下的 Runtime/Harness 控制流。

现有 Coding/VisionForge 纵向切片已经具备 DAG、Artifact、角色路由、受控工具、验证和局部修复资产；Runtime Kernel 已完成 `PROD-01A` 领域协议、`PROD-01B-1` SQLite Schema/Migration/UnitOfWork 事务底座、`PROD-01B-2` concrete Thread current-state + append-only RuntimeEvent 原子纵切、`PROD-01B-3A` durable Outbox intent 原子三写，以及 `PROD-01B-3B-1` 本地 claim/NACK/expiry-reclaim。`local_trusted_execution/v1` 的主体候选也已实现并提交，但没有完成生产安全认证。

当前不再让完整 `PROD-01B`、durable Invocation、Incident Shadow 或生产安全认证阻塞作品集 MVP。近期主线 [`MVP-AGENT-RUNTIME-01`](Plan/Plan29.md) 已把现有 AgentInstance/AgentSession 协议接入单进程、共享线程池和 SQLite 的创建、状态、Mailbox、私有数据、调度与 Handoff；生产增强继续保留在 Roadmap。

## 已实现能力与完成边界

当前已经具备：

- 一个固定、确定性、可重复运行的离线作品集入口；
- 默认不读取真实密钥、不调用外网或真实模型；
- Worker 路由、Artifact 接纳、Patch 权限、Runtime-owned Validator 和局部 Fix 闭环；
- 持久 Thread、AgentInstance/AgentSession、生命周期记录和 Agent 私有状态；
- SQLite Mailbox、结构化 Message/Handoff，以及同 Agent FIFO、跨 Agent 并行的执行泳道；
- Coding、VisionForge 与文本/图片/音频/视频证据处理纵向资产；
- SQLite 事务、部分 Runtime current-state/Event/Outbox 与本地执行安全基础；
- 可见的 Runtime/角色时间线，以及完整 Trial、Agent Runtime、StageAudit、Validator JSON 和明确失败原因。

当前明确不具备或不声称：

- 生产级分布式执行、完整 durable Invocation、exactly-once 或生产 SLO；
- Mailbox ACK/重试/崩溃重投、跨进程泳道协调、in-flight 恢复或 durable Turn Store；
- 完整持久 Thread Web 控制面、远程 Worker 或硬取消；
- 敌对多租户隔离、容器级生产沙箱、默认断网与完整资源配额；
- 自动事故治理、完整崩溃恢复或已承受真实线上流量；
- scripted Demo 所不能证明的 LLM 效果提升或多 Agent 普遍优越性。

## 测试与验证证据

默认 Demo 的定向回归不调用真实模型或网络：

```bash
cd demo
python3 -m unittest \
  tests.test_agent_runtime \
  tests.test_agent_mailbox \
  tests.test_coding_ablation \
  tests.test_portfolio_agent_runtime \
  tests.test_portfolio_demo
```

运行 Runtime 测试集：

```bash
cd demo
python3 -m unittest discover -s tests -p 'test_runtime*.py'
```

运行完整非预期红测集合：

```bash
cd demo
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest $(rg --files tests -g 'test_*.py' -g '!test_*expected_red.py' | sed 's#/#.#g; s#\.py$##')
```

`test_local_trusted_execution_expected_red.py` 与
`test_local_trusted_execution_behavior_expected_red.py` 是必须在独立新解释器中执行的历史
EXPECTED_RED 证据，因此不能混入普通 discover 进程；它们不是当前回归失败。

`PROD-01B` 的版本、环境、命令、计数、故障/并发结果、真实缺陷、修复与回归位置、未覆盖风险、独立 Review 和最终决策统一记录在 [VerificationReports/PROD-01B.md](VerificationReports/PROD-01B.md)。当前 `SEC-EXEC-01` 的 EXPECTED_RED、后续实现/攻击证据与决定单独记录在 [VerificationReports/SEC-EXEC-01.md](VerificationReports/SEC-EXEC-01.md)。Plan 定义契约，HANDOFF 只保存摘要；不要再从多个文档拼接测试结论。

当前 3B-1 已完成分层门禁、跨进程/强退恢复和独立 Review；首绿后挑战实际击穿并关闭 4 组产品缺陷，历史 EXPECTED_RED 只作为能力实现前的失败证据保留。精确计数、命令、哈希和限制只在上述 VerificationReport 维护。

## 后续路线

`MVP-AGENT-RUNTIME-01D` 已完成文档、全量回归和独立审查；`MVP-CLOSE-01D` 已从本地候选
`cbb35e3` 的干净检出复跑 Quickstart、定向/全量回归、Python compile和差异格式检查，
并完成最终独立Review。当前没有活动作品集批次；tag、push或部署需用户另行决定。生产级持久
Invocation、Transport publish/ACK、完整Thread Web、资源隔离、
容量与事故运营继续作为后续Roadmap。

## 权威文档

- [Plan/Plan29.md](Plan/Plan29.md)：当前作品集版项目闭环范围、批次和轻量验收口径；
- [HANDOFF.md](HANDOFF.md)：当前事实、边界、下一批与验证记录；
- [Plan/Plan26.md](Plan/Plan26.md)：Harness 产品定位、Runtime Charter 与后续 PROD 路线；
- [OPTIMIZATION_BACKLOG.md](OPTIMIZATION_BACKLOG.md)：生产演进 Backlog；
- [LEARNING_PATH.md](LEARNING_PATH.md)：开发与事故驱动学习路径；
- [SecurityProblem.md](SecurityProblem.md)：2026-08-25 `SEC-EXEC-01` 风险取舍与历史生产顺序证据（当前路线已由 Plan29 覆盖）；
- [VerificationReports/SEC-EXEC-01.md](VerificationReports/SEC-EXEC-01.md)：当前本地可信执行门禁的 EXPECTED_RED 与后续收口证据；
- [Plan/闭环覆盖范围.md](Plan/闭环覆盖范围.md)：事故学习闭环的覆盖边界与限制；
- [VerificationReports/PROD-01B.md](VerificationReports/PROD-01B.md)：已完成 01B 切片的历史开发验证与真实缺陷证据；未完成部分现为 Roadmap。
