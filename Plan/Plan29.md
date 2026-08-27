# Plan29：项目闭环优先的作品集版收口计划

日期：2026-08-27

状态：**已批准，当前主线；2026-08-27 按 workflow4 用户确认追加 Agent Runtime MVP 修订**。

## 决策

当前目标从“继续逐项达到生产级认证”调整为：

> 先把现有 Multi-Agent Harness 收回到一个边界清楚、能够从干净检出运行、能够演示、能够复现结果、能够诚实说明限制的完整项目。生产级可靠性、安全认证和运营体系保留为后续路线，不再阻塞当前项目完成。

本决策不删除、降级或重开已经完成的 Runtime、Persistence、Outbox、Coding、VisionForge、多模态和 `local_trusted_execution/v1` 资产，也不改写既有 VerificationReport。它只改变近期优先级和“项目完成”的定义。

## 当前完成口径

`MVP-CLOSE-01` 完成表示 **portfolio-complete / local demo ready**，不表示 production-ready、Runtime Acceptance 或任何生产安全认证。完成后项目应具备：

1. **一个权威入口**：README 只推荐一个默认离线入口；新用户不需要理解全部 PROD/INC 历史即可运行。
2. **一条端到端闭环**：固定输入进入现有 Harness，真正创建 AgentInstance/AgentSession，经 Mailbox、独立执行泳道和结构化 Handoff 产生 Artifact，经过 Runtime-owned 验证后输出结构化结果和失败原因。只把 DAG StageAudit 标成 Agent 不满足本条。
3. **默认离线与安全失败**：默认不读取真实密钥、不调用外网、不启动真实模型；需要本地子进程时必须使用现有显式批准和受控 Profile，缺失批准时在副作用前拒绝。
4. **结果可见**：CLI 至少输出 Thread、Agent ID/Role/Session 状态、创建/暂停/恢复/关闭生命周期、Mailbox 收发、Handoff、关键 Artifact/Verification、执行摘要、输出目录和已知限制；可选 Web 只展示已有稳定能力，不承诺完整 Thread/Agent 泳道。
5. **可复现**：从干净检出按 Quickstart 能在本地完成一次确定性 Demo；生成物落在明确目录，可重复运行或显式拒绝覆盖，不依赖未说明的宿主状态。
6. **合理质量门禁**：运行与闭环直接相关的定向测试、一次离线端到端 smoke、Python compile 和差异格式检查；真实 Browser、敌对进程矩阵、生产故障演练和全量安全认证不是 MVP 完成条件。
7. **项目说明完整**：README 包含定位、架构、Quickstart、Demo 输出、测试命令、已实现能力、明确未实现项和后续路线；简历描述只引用已经实现和验证的事实。
8. **发布检查点**：形成一个可识别的 release candidate commit；是否打 tag、发布或部署仍由用户另行决定。

## 实施批次

### MVP-CLOSE-01A：范围与入口冻结

状态：**已完成（2026-08-27）**。冻结结果见下文“01A 权威 Demo 合同”；本批没有创建入口或运行 Demo。

- 选定一个现有、依赖最少的确定性离线场景作为默认 Demo；优先复用 `core_coding_ablation_run.py` 的 scripted worker、固定 Coding suite 和现有 Artifact/Validator/Fix 闭环。若选择通用 DAG，只允许增加薄的 deterministic demo adapter，不把测试 Fake Model 冒充产品 Provider，也不新建第二套 Runtime。
- 冻结单一 Quickstart、输入、输出目录、退出码和结构化报告字段。
- 把生产路线、实验路线和历史安全证据移到“后续演进/深入阅读”，不放在新用户主路径上。

### MVP-CLOSE-01B：端到端 Demo 闭环

状态：**已完成（2026-08-27），但只作为 preview baseline，不满足真实 Agent Runtime 门槛**。证据见 Step Log `TRACE-168～170`。

- 补齐或收敛一个用户可直接执行的入口：`输入 → Harness 编排 → Agent/Worker 候选 → Artifact → Validator/Acceptance 摘要 → 最终报告`。
- 默认使用现有 scripted worker 或薄 deterministic demo adapter；测试 Fake Model 不冒充产品 Provider。真实 Provider 仅作为可选配置，不属于验收。
- 成功、输入非法、缺少执行批准和验证失败至少各有一个清晰、可自动检查的结果；不要求建立生产 Incident Ledger。

### MVP-CLOSE-01C：可见性与文档

状态：**已完成（2026-08-27），后续须按 Agent Runtime 实现再次校正文档**。证据见 Step Log `TRACE-171～173`。

- README 增加不依赖旧聊天的 Quickstart、示例输出、架构图和限制。
- 若已有 Web 路径能在不扩建 Runtime 的前提下稳定展示结果，则提供可选启动方式；否则以结构化 CLI 报告和生成 Artifact 为正式产品面。
- 保留 Coding 与 VisionForge 为已实现纵向切片；不为完成 MVP 强制实现完整 `CodingPlugin` 或通用 Thread Web。2026-08-27 用户后续明确要求作品集必须真正使用单机 Agent Runtime，因此“不强制 Agent 泳道”的旧口径由下节修订，不再作为发布依据。

## 2026-08-27 用户确认修订：发布前必须完成 Agent Runtime MVP

### 修订原因

`workflow4` 最后四轮先确认了当前主流程仍是临时 WorkerRegistry + DAG，再由用户明确要求：不需要生产级，但作品集必须真正使用“Agent 像线程一样存在”的设计。这里的 Agent 不是一条永久 Python 线程，也不是终端里显示的角色名称，而是有独立身份、Session、私有状态、Mailbox、生命周期和执行顺序语义的 Runtime 实体。

01B/01C 按当时权威合同正确完成了薄 scripted/offline Demo、报告和文档，因此成果保留；但其公开时间线来自 Trial/StageAudit 投影，没有创建 AgentInstance/AgentSession，也没有 Mailbox、Agent 私有状态或真实 Handoff。它只能作为后续接入的可复用 preview baseline，不能直接进入作品集发布检查。

### MVP-AGENT-RUNTIME-01 总边界

目标是一个 **单进程、SQLite 单机持久、共享线程池** 的可用 Agent Runtime MVP。它必须真正接入 Portfolio Demo，但明确不包含：

- 分布式队列、多机 Worker、Lease/Heartbeat/Fencing 和自动故障转移；
- 崩溃后恢复正在执行的 Agent、exactly-once、生产 Finalizer/Reaper 或生产 SLO；
- 完整持久 Thread Web、Browser E2E、真实模型调用或生产安全认证；
- 完整 PROD-01C～05 的全部契约。MVP 可以复用现有领域对象与 SQLite 基础，但不得把本轮完成冒称对应 PROD 批次完成。

冻结的最小语义：

1. `AgentManager` 能在一个 Thread 内创建、查询、暂停、恢复和关闭 AgentInstance/AgentSession；关闭后拒绝新消息和新工作。
2. AgentInstance、AgentSession、Mailbox 消息、消费游标和最小私有状态保存在 SQLite；进程重启后可查询已提交状态，但不承诺恢复崩溃时正在执行的调用。
3. 同一 Agent 的 Mailbox 按顺序消费；不同 Agent 可以通过共享线程池并行。pause 必须阻止领取新消息，resume 后继续，close 后不再调度。
4. Agent 私有状态按 `agent_instance_id`/`agent_session_id` 隔离，至少保存当前目标、步骤、收到/发出的消息引用和产生的 Artifact 引用；跨 Agent 直接读取默认拒绝。
5. Planner → Developer → Tester/Fixer 的工作必须通过结构化 Mailbox Message/Handoff 传递；Runtime-owned Validator 仍保持独立门禁，不伪装为 Agent。
6. Portfolio Demo 必须输出真实 Thread/Agent ID、Session 状态迁移、Mailbox 收发计数、Handoff、Artifact、并行/顺序证据和最终关闭状态。现有9 Trial矩阵与0真实模型调用可以保留，但不能再用 StageAudit 角色投影替代 Agent 生命周期。

### MVP-AGENT-RUNTIME-01A：Agent 实体与 SQLite Store

状态：**已完成（2026-08-27）**。实现与验证证据见 Step Log `TRACE-177～180`。

- 冻结最小 AgentManager/Store API，复用现有 AgentInstance/AgentSession 领域对象；不复制第二套领域模型。
- 建立 SQLite migration、创建/查询、状态迁移、私有状态隔离和重启后读取。
- 定向测试至少覆盖重复创建/idempotency或明确冲突、非法状态迁移、跨 Thread/Agent 读取拒绝、关闭后写入拒绝和事务回滚。

### MVP-AGENT-RUNTIME-01B：Mailbox 与独立执行泳道

状态：**已完成（2026-08-27）**。实现与验证证据见 Step Log `TRACE-181～183`。

- 实现持久 Mailbox send/receive/ack或消费游标、同 Agent FIFO、关闭后拒绝投递。
- 以共享线程池实现“同 Agent 串行、不同 Agent 可并行”；pause/resume/close 必须实际影响领取与调度。
- 定向测试使用确定性 barrier/event 证明顺序与并行，不依赖 sleep 猜测。

### MVP-AGENT-RUNTIME-01C：真实 Handoff 与 Portfolio Demo 接入

状态：**已完成（2026-08-27）**。实现、TDD、smoke、全仓回归与`code-review`双轴复审均已通过，证据见 Step Log `TRACE-184～187`。

- 让 Portfolio Demo 真正创建 Planner、Developer、Tester/Fixer Agent；Agent 通过 Mailbox/Handoff 传递任务、诊断和 Artifact 引用。
- 保留 ArtifactStore、PatchIntegrator、临时 Workspace 和 Runtime-owned Validator 作为既有可信边界，不把 Validator 改成能自证的 Agent。
- 扩展报告保存 Agent、Session、Mailbox、Handoff、生命周期和lane证据；保持 scripted/offline、0真实模型、无网络。

01C 将当前权威报告从下文的 preview baseline `portfolio-demo-report/v1`
升级为 `portfolio-demo-report/v2`。v2 保留 v1 全部既有字段和固定
3×3 矩阵，只新增顶层 `agent_runtime`；其中显式区分 21 条真实 stage work
Message 与 12 条跨 Agent Handoff，并保存 9 Thread、21 Agent/Session、
42 条 Mailbox bootstrap/work 的收发与消费、FIFO/并行证据、私有状态版本和最终关闭。
旧 v1 章节继续作为 `MVP-CLOSE-01B`薄 Demo 的历史合同，不再是 Agent Runtime
接入后的发布报告 schema。CLI 同时必须直接展示 Thread、Agent/Session
生命周期、Mailbox 收发、stage Message/Handoff、Artifact、FIFO/并行和关闭摘要，
不能只把这些事实藏在 JSON 中。

### MVP-AGENT-RUNTIME-01D：生命周期报告、回归、文档与独立 Review

状态：**已完成（2026-08-27）**。两份README、全量回归和独立审查均通过；初审唯一
Medium文档新鲜度问题已按`TRACE-189`修正，并由同一审查者窄复核确认关闭，最终0 finding。

- 自动验证 create/query/pause/resume/close、FIFO/并行、私有状态隔离、真实 Handoff、Artifact/Validator/Fix 与所有Agent最终关闭。
- 更新两份README，使示例输出来自真实Agent生命周期而非StageAudit投影。
- 运行定向回归、Python compile、diff-check和一次独立Review；完成后才允许进入下方 `MVP-CLOSE-01D`。

已有薄Demo和文档可复用；01A/01B完成后预计剩余 **5～7小时**；这是实现前估算，不是期限或完成承诺。

### MVP-CLOSE-01D：作品集版发布检查

状态：**进行中（2026-08-27）**。Agent Runtime 01A～01D前置已满足；候选manifest已在
`TRACE-194`冻结，正在创建本地release-candidate并执行干净检出复现。尚未通过最终独立Review。

- 从干净检出运行 Quickstart 和一次离线端到端 smoke。
- 运行与默认 Demo、Artifact、Validator、本地执行拒绝边界直接相关的定向回归；再运行 Python compile 与 `git diff --check`。
- 记录实际命令、结果和已知限制，形成一个最终独立 Review；不要求双 Review、完整故障矩阵、全量安全认证或生产 SLO。

## MVP-CLOSE-01A 权威 Demo 合同

### 用最直白的话说明

项目已有多条开发和实验入口。01A 的决定是：不让新用户挑路线，也不让他先配置模型；只新增一个很薄的作品集“正门”，把现成的离线 Harness 闭环用一条命令展示出来。旧入口继续保留，但不再作为 README 的默认 Quickstart。

### 唯一入口与输入

- 01B 将新增薄入口 `demo/portfolio_demo.py`；本文件在 01A 结束时尚未创建。
- 整体 CLI 合同 ID 固定为 `portfolio-demo/v1`；本 01A preview baseline 生成报告的 `schema_version` 固定为 `portfolio-demo-report/v1`，`demo_id` 固定为 `portfolio-demo`。两者用途不同，不得混用；Agent Runtime 01C 的发布报告已由上文明确升级为 v2。
- 从仓库根运行的唯一 Quickstart 已冻结为：

```bash
python3 demo/portfolio_demo.py --trusted-local-execution
```

- 公开参数只允许 `--trusted-local-execution` 和 `--help`。不允许从作品集入口选择 Provider、模型、Web、任意任务、任意 Suite 或协作策略。
- 输入固定为仓库内 `core-coding-eval-v1`：manifest SHA-256 为 `cea75c0ee1f8fafc4d4eebfabbe2ff8f18ee1f2624d3831e198cce984827ee91`，包含 `python-tax-rounding`、`python-user-payload`、`python-inventory-cli` 三个任务。
- 入口不读取 `.env`、不访问网络、不调用真实模型；scripted worker 只复用现有 `WorkerRegistry → ArtifactStore → PatchIntegrator → Runtime-owned Validator → Tester/Fixer` 控制流。
- `--trusted-local-execution` 只批准固定 Suite 已登记的本地 Python 验证命令。缺少批准时必须在 Suite 加载、Workspace 创建、Validator 启动和报告改写前拒绝。

选择这个入口而不选其他入口的原因：固定评测入口虽有内部评测 Artifact，但只校准 starter/reference solution 与 Validator，缺少 Worker 路由、Patch 集成和“失败→诊断→Fix”闭环；通用 Coding CLI 会读取 Provider 配置并走真实模型路径；现有 Web 没有提交执行批准字段且任务是内存态。它们都不适合作为本轮默认离线正门。

### 固定成功矩阵

同一组三个任务固定运行三种现有策略，共 9 个 Trial：

| 策略 | 每个任务的预期结果 | 作品集含义 |
|---|---|---|
| `single_agent` | 初次与最终验证失败，不交付 | 预期对照失败，不代表 Demo 失败 |
| `planner_developer` | 首轮验证通过并交付 | 展示计划与实现链 |
| `planner_developer_tester_fixer` | 首轮失败，Tester 诊断，Fixer 修复，最终通过并交付 | 展示完整失败恢复闭环 |

精确总结果是：9 个 Trial、6 个交付、3 个预期对照失败、3 个修复成功、21 次 scripted worker 调用、0 次真实模型调用。只有完整匹配该矩阵且没有 `UNKNOWN`、非预期 Validator 失败或安全不变量偏差，顶层 `status` 才能是 `passed`。

CLI 的主展示对象固定为 `python-inventory-cli + planner_developer_tester_fixer`：它包含多文件修改、build、unittest、CLI 正常/非法输入退出码、首次失败、诊断、修复和最终全绿。完整 JSON 仍保留三任务、三策略的全部 Trial/Audit。

### 输出与重复运行

- 固定输出目录：`demo/.runs/portfolio-demo/`；该目录已被现有 `demo/.gitignore` 的 `.runs/` 规则覆盖。
- 固定报告：`demo/.runs/portfolio-demo/report.json`。
- 写入必须使用临时文件加原子替换；重复运行只覆盖这一份专用完整报告，不修改源码、固定 Suite 或其他 Run。
- Trial Workspace 继续使用临时目录并在结束后清理；作品集交付物是结构化报告和可见摘要，不冒称保存了可继续开发的用户项目。
- stdout 使用一行稳定摘要：

```text
status=passed tasks=3 trials=9 delivered=6 expected_failures=3 repaired=3 external_model_calls=0 report=demo/.runs/portfolio-demo/report.json
```

可复现指语义结果、固定矩阵和 Suite digest 一致，不要求时间戳与耗时字段逐字节一致。

### 退出码

| 退出码 | 含义 |
|---|---|
| `0` | 完整运行且精确符合固定矩阵；三个 Single-Agent 失败是已声明的正常对照 |
| `1` | Runner 已形成完整结果，但应通过的验证失败、Trial 内被 Runner 捕获并写入报告的异常/`UNKNOWN`、矩阵不匹配或安全不变量偏差；必须写出 `status=failed` 报告 |
| `2` | 参数非法或缺少本地执行批准；必须在 Suite、Validator 和报告副作用前拒绝 |
| `3` | Runner 外的 setup/manifest 加载、序列化、原子写入或其他未形成完整 Trial 报告的异常；不得输出成功结论 |

### `portfolio-demo-report/v1` preview baseline 顶层字段

整体 CLI 合同 ID 是 `portfolio-demo/v1`。本段记录 01B 薄 Demo 的历史报告合同：报告内必须精确写入 `schema_version="portfolio-demo-report/v1"` 和 `demo_id="portfolio-demo"`；报告顶层固定为：

```text
schema_version
demo_id
status
mode
suite
execution
workflow
summary
verification
trials
output
limitations
```

- `mode` 明确写 `offline_scripted`、无网络、无真实 Provider；`execution` 记录批准来源、起止时间和 scripted/model 调用数。
- `suite` 记录 Suite ID、manifest hash 和三个 task ID。
- `workflow` 显示角色、阶段以及 `core:coding_requirement`、`core:source_snapshot`、`core:plan`、`core:patch`、`core:validator_feedback`、`core:test_diagnosis` 等关键 Artifact kind。
- `verification` 同时保存期望矩阵、实际矩阵和 mismatch；`trials` 复用现有 `CodingAblationReport` 的 Trial、StageAudit、Validator 和 failure reason。
- `output` 保存仓库相对报告路径和原子覆盖语义。
- `limitations` 必须说明 scripted worker 使用冻结 fixture/reference repair，只证明 Harness 编排、权限、Artifact、Validator 和 Fix 闭环，不证明 LLM 效果；同时说明临时 Workspace、无 Web、无真实 Provider、非生产认证。

### 01B 自动检查与最小文件范围

01B 至少自动证明四类结果：

1. 真实离线成功：退出 `0`，精确 9 Trial/固定矩阵/21 scripted calls/0 model calls；
2. 非法参数：退出 `2`，Suite loader、Runner 和报告写入均未发生；
3. 缺少批准：退出 `2`，本地命令与报告副作用均为零；
4. 注入一个“本应通过但失败”的测试报告：退出 `1`，报告保留 Trial、Validator 和 mismatch 原因；不为产品 CLI 增加公开故障开关。

01B 的默认最小代码范围只有：

- 新增 `demo/portfolio_demo.py`；
- 新增 `demo/tests/test_portfolio_demo.py`。

默认不修改 `CodingAblationRunner`、固定 Suite、Runtime、安全执行、Web 或模型代码。原 `core_coding_ablation_run.py` 保留为实验入口；根 README、Demo README、示例输出和架构说明统一留给 01C，避免在入口尚不可运行时提前发布 Quickstart。

## 明确后置的工作

以下能力全部保留在 Roadmap，但不再阻塞 `MVP-CLOSE-01`：

- `SEC-EXEC-01` 的 4 个真实 Browser E2E、Renderer/browser binary Profile、完整 target adversarial、最终安全 `KEEP`；
- `PROD-01B-3B-2` Transport publish/ACK/Receipt、完整 BudgetLedger、Runtime-only Acceptance writer 和跨领域查询/恢复；
- `PROD-01C` durable Invocation queue、lease/heartbeat/fencing、Watchdog/Reaper 和崩溃恢复；
- `PROD-01D` 的完整 Thread/Invocation 持久 Web 接入；MVP 只复用现有稳定入口，不冒称该批完成；
- `PROD-01E` Incident Ledger 与 Observe/Shadow；
- `PROD-02` 及之后的 Backend streaming、硬取消、生产隔离、完整协作控制面、Context/Memory 治理、容量和事故运营。

这些项目未来恢复时仍使用原 PROD/INC 契约和对应严格证据；不得因为 MVP 已完成而改写为已实现。

## 轻量证据规则

`MVP-CLOSE-01` 与 `MVP-AGENT-RUNTIME-01` 使用作品集版证据轨：

- 两条路线的每个 01A～01D 批次只需一条批次级 `PRE_REGISTER` 和完成时的 `ACTUAL/CHECKPOINT`，不为每次搜索、微小修正或单个测试建立独立哈希循环；
- 只有改变安全边界、真实外部副作用或最终发布候选时才要求独立 Review；最终 01D 保留一次独立 Review；
- 证据以 Git commit、精确测试命令、退出码、测试计数、Demo 输出和已知限制为主，不要求为作品集 Demo 生成生产 Incident、SLO、双审或完整 adversarial receipt；
- 默认只运行与变更和权威 Demo 相关的定向测试；全量回归在成本合理时执行，但不是因历史 PROD 口径自动强制；
- 任何失败仍如实保留，不得把 Fake/Mock、默认 skip 或未运行写成真实生产证据。

本轻量轨只适用于 `MVP-CLOSE-01` 与 `MVP-AGENT-RUNTIME-01`。恢复 PROD/INC、安全认证、真实 Provider、网络或其他高风险边界时，自动回到 `Plan26` 的严格证据协议。

## 完成后的诚实表述

可以表述为：

> 一个可本地运行、可复现、带受控工具执行、Artifact/验证链和 Coding/VisionForge 纵向切片的 Multi-Agent Harness 作品集项目；Runtime 已具备部分持久状态、Event/Outbox 和安全执行基础。

不得表述为：生产级分布式 Agent 平台、完整 durable execution、exactly-once、生产沙箱、自动事故治理、完整 Thread Web 控制面或已承受真实线上流量。

## 下一动作

`MVP-CLOSE-01A～01C` 的薄Demo baseline已保留；`MVP-AGENT-RUNTIME-01A～01D`已完成真实 Agent/Session、schema v5 Mailbox、同Agent串行/跨Agent并行lane、结构化Handoff、Portfolio Demo真实接入、两份README、33项定向、184项Runtime、579项非expected-red全仓回归、连续5次真实CLI smoke、compile/diff门禁和最终0 finding独立Review。下一批是仍暂停的`MVP-CLOSE-01D`，必须由用户另行启动；不得自动扩张到Web、真实模型、Browser、完整PROD-01C～05、stage/commit/push。
