# Plan26：交互式多模态 Multi-Agent Harness 产品定位与 Runtime Charter

日期：2026-08-23

讨论主题：纠正 Coding 场景对产品边界的过拟合，冻结“Multi-Agent Harness 是项目本体、通用 Multi-Agent Runtime 是执行内核、专业能力以 Plugin 接入”的领域、验收与增量迁移路线。

## 当前状态

状态：**PROD-00 文档冻结完成，Runtime 行为未修改**。

2026-08-23，项目产品中心由“面向 Coding 的专用 Multi-Model Agent Harness”纠偏为：

> 一个以通用、可持久化 Multi-Agent Runtime 为执行内核的多模态 Multi-Agent Harness。用户可以在 Thread 中持续发送文本、图片、音频和视频；Agent 可以独立判断、并行工作、按依赖交接、使用受控工具并接受人工介入。Coding 和 VisionForge 是可插拔的专业能力，不是 Harness Core 的默认目的。

2026-08-25 术语澄清：2026-08-23 的实质决议是从 Coding 专用链路泛化到长期、多场景 Multi-Agent 系统；“Runtime 是产品本体”的旧简称被本节取代，但既有领域模型、实现状态和 PROD 顺序不变。

本计划用于冻结新的 Harness 产品边界、Runtime 内核领域语言、增量迁移路线、验收口径和事故联动。它不删除现有 Coding/VisionForge 能力，不修改当前 Runtime，不调用模型，不访问网络，也不读取外部仓库。

固定术语契约：

- **Multi-Agent Harness** 是项目与产品本体，组合并治理 Runtime、Agent 编排、Context/Memory、Model/Tool Adapter、Eval、Incident/Security 和 Plugin；
- **Runtime Kernel** 是 Harness 内的执行与控制内核，负责持久状态、生命周期、并发、取消/恢复、运行时验权、事件和 Acceptance 的强制执行，不代表整个产品；
- **Plugin** 是受 SPI、Grant 和 AcceptancePolicy 约束的专业能力，不能拥有或放宽 Runtime Kernel/Harness Core 的真相、权限与验收；Coding/VisionForge 是当前例子；
- **Model/Backend** 是经 Adapter 调用的可替换执行负载，只能产出候选 Artifact/Evidence，不拥有状态、权限或验收权。`AgentInstance` 也不等于某个具体 Model。

## 为什么需要纠偏

此前为了得到客观、确定性的验收结果，项目先选择了代码 Bug、固定测试和视觉页面作为纵向切片。这些切片随后反向塑造了默认 CLI、Workflow、Web 文案、Role 和评测，使“Agent 可以 Coding”逐渐变成“项目只负责 Coding 修复”。

当前过拟合主要存在于 Composition Root、默认工作流、产品页面和评测，而不是所有基础模块：

- `coding_agent_cli.py` 默认创建 Python 标准库项目并运行 unittest；
- `dag_runner.py` 默认围绕 Planner、Implementer、Tester 和 Fixer 收敛；
- 固定 Coding 评测主要从失败 starter 出发；
- Web 首页使用 Coding 与修复语义；
- 多模态 Intake 的当前下游是文本 Coding Planner。

这些实现继续作为已验证的 Coding 场景资产保留，但不再定义 Core 的业务边界。

## 候选方案对比与最终选择

| 方案 | 核心思路 | 优点 | 缺点、成本与风险 | 适用条件 |
|---|---|---|---|---|
| 继续以 Coding 专用 Harness 为 Core | Thread、Agent、验收和 Web 继续围绕代码修改与测试组织 | 现有纵向链路成熟，短期改动最小 | 普通交互、多模态分析和其他工具场景被迫套用 Coding 语义，Core 持续过拟合 | 产品只服务代码任务时 |
| 通用多模态 Multi-Agent Harness，以 Runtime 为执行内核，Coding 场景插件化 | Runtime Kernel 负责 Thread、Agent 协作、Invocation、Artifact、权限和场景化 Acceptance；Harness 组合路由、Context/Memory、工具、评测和事故学习 | 可复用到非 Coding 场景，并保留现有 Coding/VisionForge 资产 | 需要新增通用领域协议、兼容层和渐进迁移；过早重写或插件化可能造成双真相源 | 目标是长期、多场景 Agent Harness 时 |

最终选择第二种方案。原因是当前目标已经超出代码修复：Scope、Thread、Invocation、Artifact、权限和 Acceptance 属于可复用的 Runtime Kernel，角色/模型策略、Context/Memory、工具、评测和事故学习共同构成更完整的 Harness。通过单向兼容和分批迁移，可以避免删除已验证资产或一次性重写 Runtime。第一种 Coding 专用方案被放弃为产品定位，但继续作为目标 Coding Plugin 的专业能力保留。

## 产品边界

### Harness Core 产品能力

Core 首先提供长期交互和协作运行能力：

1. 用户创建或恢复一个 Thread；
2. 用户持续发送文本或媒体消息；
3. Runtime 将消息、媒体和工具结果保存为受控 Artifact；
4. 一个或多个 Agent 根据 Role、能力、上下文和策略被创建 Invocation；
5. Agent 可以独立工作、并行工作或提交结构化 HandoffProposal；
6. Runtime 校验路由、权限、预算、依赖和循环后创建后续 Invocation；
7. 用户可以暂停、取消、补充信息、批准高风险动作或改变方向；
8. Runtime 根据本次交互或插件声明的 AcceptancePolicy 生成判定；accepted/rejected 结束当前工作对象，needs_input 等待用户，unknown 可以在策略允许时调度下一次 Invocation。缺少能力、依赖或权限导致的 blocked 属生命周期状态，不是 Acceptance Outcome。

Core 不要求每个 Thread 都产生代码、Patch、测试或 Fixer，也不要求每次对话都形成复杂 TaskGraph。

### 第一阶段业务模式

第一阶段同时保留四种业务模式，不能再由其中一种代表整个产品：

- **持续交互**：用户与一个长期 Agent 多轮交流，Session 可恢复但不是事实源；
- **协作分析**：多个 Agent 独立分析、质疑、交接并形成带证据的结论；
- **多模态理解**：图片、音频或视频先转成结构化 Evidence，再交给被授权 Agent；
- **工具与专业能力**：Agent 按需调用 Coding、浏览器、文档或其他插件能力。

目标 Coding Plugin 的业务链路仍可包含需求分析、代码修改、测试、Review 和 Fix。当前代码尚无 `CodingPlugin`；Coding 仍是 Composition Root/包内纵向切片，VisionForge 是直接注册到 Core Registry 的独立 `visionforge:web_visual` Scenario Plugin，并复用 Coding 能力。Plugin SPI 当前不支持插件嵌套，后续不得用“位于 Coding Plugin 之上”掩盖这一事实。

### 非目标

第一阶段不实现自由无限 A2A、所有 Agent 永久在线、敌对多租户、任意远程插件、开放网络、复杂向量数据库、微服务拆分或让模型自行决定权限和完成状态。

## 统一领域模型

### `Scope`

权限、保留策略和数据隔离的最上层边界。第一阶段可以只有一个本地默认 Scope，但不能用可选 Project 或 Workspace 代替它。每个持久实体必须直接携带 `scope_id`，或通过不可变外键唯一归属一个 Scope；所有父子关系、Artifact 引用、Acceptance Evidence 和因果引用必须属于同一 Scope，跨 Scope 引用一律 fail-closed。

### `Thread`

用户可见的长期协作空间。Thread 保存身份、参与者、消息顺序、当前状态、项目/工作区关联和策略版本。Thread 不等于 Task、Invocation 或模型 Session。

### `Message`

人类、Agent 或 Runtime 在 Thread 中发布的结构化表达。Message Store/Journal 独占正文、顺序、sender、recipient、parent、causation、类型、可见性和时间；正文可以引用 Artifact。Artifact 不保存另一份 Message 状态，原始内部推理也不进入 Message。

### `AgentProfile` 与 `AgentInstance`

`AgentProfile` 组合 Role、Backend/Model Policy、Tool Capability、Context Policy、Output Contract 和预算。`AgentInstance` 是 Thread 中长期可寻址的协作者身份，关联 Mailbox 和 Runtime 自有 AgentSession，但不等于持续占用的模型进程或供应商 Session。

### `Invocation` 与 `Attempt`

Invocation 是一次具体 Agent、模型或工具执行；Attempt 是 Invocation 的一次尝试。每次 Invocation 获得不可变输入快照、独立授权、预算和版本，完成后提交候选消息或 Artifact。为某个任务临时创建的 Specialist 不是新的长期 Thread 或常驻模型进程，而是当前 Scope/Thread 下带 `parent_invocation_id`、可选 `route_edge_id` 和因果引用的 ChildInvocation；只有用户或产品策略确实需要独立保留、权限和参与者边界时才创建新 Thread。

Invocation 必须分别记录执行状态与清理状态：

```text
execution_state: CREATED → QUEUED → CLAIMED → RUNNING
                                      ↓
                     SUCCEEDED / FAILED / CANCELLED / TIMED_OUT

cleanup_state: ALLOCATED → ACTIVE → DRAINING → TERMINATING
                                                   ↓
                              REAPED / TERMINATION_FAILED
```

`SUCCEEDED` 只表示候选结果和终态意图已经可靠记录，不表示资源已经释放，也不等于 `Outcome.accepted`。只有执行状态已经终止、`cleanup_state=REAPED`、活动 Grant/Lease/ChildInvocation 为零且 fencing 已阻止迟到结果和副作用时，Invocation 才能视为 closed。清理失败必须显式进入 `TERMINATION_FAILED/RECOVERY_REQUIRED` 并留下事故证据，不能静默宣称执行域已经结束。

所有成功、失败、超时、取消、租约失效和启动异常路径最终都经过同一个幂等 Finalizer：先停止新副作用并撤销权限，再级联取消子 Invocation，可靠保存候选结果/用量/终态意图，终止 Backend 或进程，释放 SessionBinding、ExecutionEnvironment、Workspace、端口、预算预留和临时资源，最后记录 reaped。Watchdog/Reaper 负责重试未完成清理；旧 Attempt 的迟到事件、Artifact 和副作用请求必须由 lease 与 fencing token 确定性拒绝。

### `AgentSession` 与 `SessionBinding`

`AgentSession` 是 Runtime 自有的 Thread-Agent 连续性实体，保存 AgentInstance、Thread、事件游标、摘要引用、状态和版本，属于 PROD-01。它不等于模型进程，也不拥有业务事实。

`SessionBinding` 是 AgentSession 到供应商 Session 外部引用的可替换映射，属于 PROD-02。推荐隔离键：

```text
agent_session_id + execution_scope_id? + backend_id
```

`execution_scope_id` 只在场景使用受控资源环境时存在；目标 Coding Plugin 可以把它绑定到 ProjectWorkspace。供应商 Session 只帮助上下文连续性，不拥有任务状态、权限或事实。PROD-02 先从 Message、Artifact 和 RuntimeEvent 恢复；PROD-05 的 ContextManifest 可用后再增强恢复质量，不能成为早期正确性的前置条件。

### `Task` 与 `ScenarioRun`

Task 是 Thread 中需要明确交付或验收的一段工作；普通交流不必强制创建 Task。ScenarioRun 是插件对一组 Task、DAG 和 AcceptancePolicy 的受控装配。现有 Coding DAG 和 VisionForge Run 迁移为 ScenarioRun，而不是产品顶层入口。

### `Turn` 与 `Outcome`

Turn 是由一个用户 Message 或显式 Runtime 触发器开启的一轮交互边界，可以产生多个 Agent Invocation、Message 和 Artifact。Outcome 是 Turn、Task、ScenarioRun 或外部动作的阶段性验收结果，状态为 `unknown / needs_input / accepted / rejected`，必须绑定 subject version 和 AcceptanceRecord。`continue` 不是第五种 Outcome：它表示 Outcome 仍为 unknown 且 Runtime 按策略调度下一次 Invocation；`blocked/cancelled` 属工作对象生命周期。Thread 可以包含许多 Turn 和 Outcome，本身不因某轮 accepted 而结束。

Core 不另建与它们重叠的 `InteractionRequest`：`Message + Turn` 就是通用交互请求，现有 `TaskSpec` 继续承载需要结构化执行的可选 Task；`CodingRequirement` 只作为 Coding 场景扩展。

### `RouteEdge` 与 `HandoffProposal`

Agent 只能提交 HandoffProposal，包含目标 Role、原因、必要 Artifact、期望输出和未解决问题。Runtime 校验身份、权限、链深、预算、重复、资源冲突和停止条件后才能创建 RouteEdge 与新 Invocation。自由文本 `@mention` 不作为可信控制协议。

### `Artifact`、`ContextBundle` 与 `ContextManifest`

Artifact 保存媒体、文档、代码、工具结果、附件、大正文和消息产出的结构化对象或证据。Message 只引用 Artifact，Artifact 不拥有 Message 的顺序、sender、parent 或投递状态。ContextBundle 是为单次 Invocation 生成的最小不可变上下文；ContextManifest 记录加入、排除、裁剪和拒绝的内容及原因，使一次判断可以追溯。

### `CapabilityGrant`、`Approval` 与 `BudgetLedger`

每个 Invocation 使用短期 CapabilityGrant。Agent 只能提出工具或路由请求，不能扩大权限。高风险副作用需要 Approval。所有模型、工具、媒体处理和外部副作用先预留预算，再结算到 BudgetLedger。

### `RuntimeEvent`

记录 Thread、Message、Invocation、Attempt、Session、Route、Artifact、权限、工具、预算、用户介入和 Acceptance 的不可变事件。事件是审计与恢复基础，不保存大正文或原始敏感媒体。

PROD-01 采用 **状态表为当前业务真相源、Journal 为不可变审计记录、Snapshot 为兼容恢复检查点** 的模式，不在此阶段实施完整 Event Sourcing。一次关键状态更新、对应 RuntimeEvent 和待发布 Outbox 必须在同一个 SQLite 事务中提交；Outbox 只负责可靠发布，消费者不能借它反向改写业务状态。

### `AcceptancePolicy` 与 `AcceptanceRecord`

`AcceptancePolicy` 是由 Runtime 或可信插件注册的版本化协议，至少包含 `policy_id/version/hash`、`subject_type`、必需证据、证据新鲜度与绑定规则、是否需要独立 Evaluator，以及允许的 Outcome。Agent 可以请求应用某个 Policy，不能创建或放宽它。

`AcceptanceRecord` 至少保存 `record_id`、`subject_type/id/version`、Policy ID/版本/hash、Outcome、Evidence 引用、`issued_by=runtime`、提供评估证据的 `evaluator_principal_ids` 和时间。可接受对象只能是 Turn、Task、ScenarioRun 或外部动作；Evaluator/Agent 只能提供 Evidence，不能签发 Record。长期 Thread 没有通用 accepted 状态，只能保持 open、paused 或由用户/生命周期策略 archived。

## Agent 执行与同步模型

AgentInstance 采用 Actor 风格：长期存在的是身份、Mailbox、AgentSession 和事件游标，模型调用仍然是短生命周期 Invocation。同一 AgentSession 严格串行；不同 Agent 可以并行；不同供应商 Session 是否并行由 Backend 能力和配额决定。

每个 Invocation 基于固定 `thread_version`、`context_version` 和可选 `workspace_version` 开始。其他 Agent 的新结果先由 Runtime 验证并追加到事件流，再通知相关 Mailbox。普通更新在下一次 Invocation 通过增量上下文消费；需求变更、取消、安全事件或使当前输入失效的更新会触发安全检查点和重新装配，不能热修改正在生成的 Prompt。

并行结果不会直接写共享可变对象。Agent 只提交不可变候选 Artifact；Runtime 根据版本、资源范围和 AcceptancePolicy 接纳、拒绝、标记过期或交给 Integrator。

## 协作与收敛

协作消息至少支持：`proposal`、`question`、`challenge`、`response`、`handoff`、`review_blocked`、`review_approved`、`human_required` 和 `runtime_decision`。

结构化交接包含 What、Why、Tradeoff、Open Questions 和 Next Action，并引用证据。`Why` 是可公开审计的简短理由，不是模型原始思维链。

Runtime 使用 DiscussionPolicy 控制最大轮数、链深、并发、重复消息、语义无进展、Token、费用和人工升级。简单交互不强制多 Agent 讨论；只有歧义、高风险、独立 Review、证据冲突或重复失败等策略条件触发额外协作。

最终收敛不是多数投票。Agent 可以提出、反驳和 Review；Runtime 根据场景 AcceptancePolicy、阻断问题、证据和用户决定生成 ConvergenceReport。

## Context 与共享记忆

共享记忆不是所有 Session 的复制。Invocation 私有信息和 Agent Session 保持隔离；通过 Runtime 验证的需求、Message、Artifact、Decision、工具结果和事实才能进入 Scope 内的 Thread/Project 共享视图。

Context、Memory 和任何检索先按 Scope fail-closed，再按权限、Thread/Project、Role、Task/Route 依赖、实体关联、证据等级、版本、新鲜度和 Token 预算生成 ContextBundle。每次调用保存 ContextManifest，能够回答 Agent 当时看到了什么、遗漏了什么及原因。

Memory 生命周期保持 `candidate → verified → active → stale → superseded/invalidated`。Agent 只能提交候选记忆；Runtime 和人工策略决定是否共享或晋升。代码和媒体只是可能的实体类型，Memory Core 不依赖仓库路径或测试结果。

## 权限、安全与隔离

有效权限为：

```text
RolePolicy
∩ WorkerCapabilities
∩ Thread/Task Policy
∩ Invocation CapabilityGrant
∩ RuntimePolicy
```

所有 Artifact、Memory、Workspace、Tool 和 Route 入口统一验权。Agent 的 Session 不携带永久权限；每次 Invocation 使用新 Grant。Thread、Session、Project、Workspace、媒体和秘密默认隔离。工具默认无网络、无秘密、无共享写入，高风险操作需要短期凭据和人工批准。

目标 Coding Plugin 继续实施 Producer/Reviewer/Validator principal 分离、Patch 集中合并和 Workspace 保护；其他插件可以声明自己的职责隔离，但不能放宽 Core 不变量。

## 场景化 Acceptance

Core 不再把 build/test 视为所有任务的统一完成条件，而是要求 Scenario 或交互类型显式注册 AcceptancePolicy：

| 场景 | 典型接受证据 |
|---|---|
| 普通交互 | 消息持久化并成功送达；无待处理人工请求 |
| 协作分析 | 必需参与者已响应；冲突和未解决问题显式记录；结论引用证据 |
| 多模态理解 | 输出绑定正确媒体、时间/区域和哈希；不确定性保留 |
| Coding | Patch 安全接纳；构建/测试/Review 等冻结 Validator 通过 |
| 外部副作用 | Capability、Approval、工具回执和幂等证据齐全 |

Acceptance 的对象是某次 Turn、Task、ScenarioRun 或外部动作的 `Outcome`，不是自动关闭长期 Thread。普通交流确认本轮 Message 已提交和送达后，Thread 仍保持开放，只有用户或明确的生命周期策略才能归档它。

任何场景都必须遵守 `unknown != accepted`。`VerificationOutcome.passed` 只是 AcceptancePolicy 可以引用的一种证据。`Invocation.completed` 只表示一次执行技术上结束；`Outcome.accepted` 表示 AcceptancePolicy 的证据已经满足；`Thread.archived` 是另一个生命周期动作。三者不能互相推导，Agent 的成功声明也不能改变它们。

## Harness Core 与插件迁移边界

### 直接保留为 Harness Core 通用能力

- Lifecycle、TaskGraph 的通用 DAG 与并发基础；
- WorkerRegistry、Role-first 路由和 principal provenance；
- ArtifactStore、Claim、VerificationRecord；
- ModelClient 能力协议和预算；
- SQLite Snapshot/Checkpoint、Memory 的通用元数据；
- PluginRegistry 与 ScenarioRuntime；
- 多模态 Evidence 引用和失败关闭原则。

### 需要泛化后保留

- `TaskContext`：从 Coding 主对象降为 Thread 中可选 Task；
- `Role/Capability`：保留 Role-first 路由机制，新增不依赖代码读写的通用 AgentProfile/Role 协议；现有 Planner/Developer/Tester/Reviewer/Fixer 与代码权限迁入目标 Coding Plugin；
- `RoleMemoryView`：演进为通用 ContextBundle/Manifest；
- `event_listener`：演进为持久 EventSink；
- Dispatcher/Executor：从进程内执行演进为 durable Invocation 控制面；
- Web：从 Coding 修复入口演进为 Thread、Agent、消息、Artifact 和工具工作台。

### 目标 Coding Plugin 拥有

- `CodingRequirement`、RepositoryScope 和代码验收 Profile；
- ProjectWorkspace、PatchIntegrator、源码读取和路径权限；
- build/test/CLI/browser 等 Coding Validator；
- Planner、Developer、Tester、Reviewer、Fixer 的 Coding Profile；
- `coding_eval`、代码消融实验和现有 Coding CLI；
- Coding 专用 Role/Capability 与 Composition Root。

VisionForge 当前保持独立 `visionforge:web_visual` Scenario Plugin，并复用上述能力；只有在 Plugin SPI 增加显式依赖协议后，才评估是否声明对 Coding Plugin 的依赖。

第一阶段不做破坏性的包目录大迁移。通过新 Composition Root、通用协议和插件注册先纠正依赖方向，待兼容测试和真实使用证明边界稳定后再决定是否重命名 `coding_workflow`。

## 产品 Web 形态

默认 Web 首页应以 Thread 为中心，而不是以修复表单为中心。最小布局包含：

- Thread 与用户输入区，支持持续文本和受控媒体附件；
- Agent 列表与泳道，展示并行、等待、交接和 Invocation；
- 结构化讨论流，展示结论、异议、证据和下一步；
- Artifact/Context/权限抽屉，解释每次 Agent 看到了什么和能做什么；
- Convergence 面板，展示接受、拒绝、未解决问题和 Runtime 决定；
- 暂停、取消、批准、补充信息和人工接管。

当前 Coding Web 作为兼容入口保留，不能继续被描述为产品最终形态。

## 评测与防过拟合

评测必须覆盖产品的四种业务模式，不能用一个 Bug 或一个网页场景代表 Core：

1. Thread 连续交互、恢复、取消和跨 Thread 隔离；
2. 两个或多个 Agent 的独立判断、并行/顺序交接、冲突与停止；
3. 图片、音频和视频的引用完整性、事实约束和上下文投影；
4. 插件/工具任务；Coding 是首个代表，并覆盖新功能、Bug 修复和行为保持重构。

每个场景分别报告可靠性、事实错误、人工介入、成本和延迟，不汇总成掩盖差异的单一分数。固定样例与隐藏保留样例分离；只改善一个 fixture 的规则进入场景策略或插件，不能进入 Core。第二个真实仓库延后到 Coding 插件化后的 Canary/dogfood，不再是 PROD-00 完成条件。

### Harness Evolution Protocol（Evo-Bench-inspired）

Harness 的实现与优化采用评测驱动演进，而不是凭直觉累加 Agent、模型、Memory、Prompt 或协作机制。本文的 Harness Evolution Protocol 指 `Baseline → 失败证据 → 可证伪假设 → 单一实验变更 → Validation → Held-out → KEEP/ROLLBACK/INCONCLUSIVE` 的项目内部开发与证据协议，不是当前插队的新生产子系统，也不等于允许 Agent 自主修改生产。它借鉴 Evo-Bench（<https://github.com/RUCAIBox/Evo-Bench>），但当前没有运行或复现其正式 Benchmark；它也不等于、依赖或授权安装外部 `evo-hq/evo`（<https://github.com/evo-hq/evo>）。后者若未来采用，只能作为外部候选实验执行器。当前生产顺序仍为 `PROD-01B → 01C → 01D → 01E`。

每个会改变 Agent 行为、路由、协作拓扑、Prompt、Context、Memory、重试、停止、工具或 Acceptance 行为的修改，必须建立版本化实验记录并在运行前冻结：

- 可复现失败、真实工作负载或确定性故障假设及 Evidence 引用；
- Baseline、强单 Agent/当前 Harness 对照和一个可证伪的 Harness 机制假设；
- `mutation_axis` 与本轮唯一主要 Mutation；`harness_only` 实验固定每个 Role 的 Backend/Model manifest，模型或路由实验则预注册 baseline/candidate assignment，不能表述为“模型不变的 Harness 收益”。若同时改变模型、Prompt、拓扑、Context、预算或内部反馈 Validator，必须拆分或做消融；
- workload/manifest hash、Policy Model/版本或预注册 assignment、Prompt/协议/策略版本、环境、权限、预算、工具、最终 EvalOracle/EvalAcceptancePolicy/HiddenValidator、随机种子、重复次数、主次指标、停止条件和排除规则；
- `promotion_rule`、最小效果阈值、允许成本/延迟上限、不确定性或重复判定方法、最小样本量和 `heldout_query_budget`；这些都必须在看结果前冻结；
- development/calibration、validation 与 sealed held-out 的任务家族隔离；同源或近重复变体不得跨集合泄漏；
- 全部已启动 Trial、失败和缺失数据、重试、Token/费用、延迟、人工介入、配置 hash 与原始 Evidence 引用；
- Validation、Held-out、正常路径、事故/故障负向路径和回归结果；
- `lifecycle_status=PROPOSED/RUNNING/FROZEN/COMPLETED/INVALID`、`decision=KEEP/ROLLBACK/INCONCLUSIVE`、回滚条件与批准者，以及 Regression、Detector、Policy、Validator、Adapter、Runbook、Skill 或后续实验的正确落点。

必须明确区分“被测 Harness 层”和“不可变 Eval 层”。候选 Harness 的内部 Prompt、路由、Context、协作、重试、停止、工具选择、内部 acceptance/gating、反馈策略或内部 Validator 可以在白名单 `mutation_axis` 中演化，但不能兼任最终 Oracle。最终 `EvalOracle/EvalAcceptancePolicy/HiddenValidator`、Runtime 验收权、权限/沙箱硬边界、实验 BudgetLedger、计分与完整分母永远由独立 Eval Runtime 冻结；Evolver、Policy Agent 和候选 Harness 都不能修改或放宽它们。

验证集迭代结束后选择历史最佳候选并冻结，而不是默认采用最后一轮；随后由独立 Eval Runtime 执行 held-out。`heldout_query_budget` 默认是每个内部 Harness Evolution Experiment 对一个最终冻结候选查询一次；逐题反馈、聚合分数和派生诊断均不得返回本轮 Evolver。任何 held-out 结果一旦暴露给人工或 Agent Evolver，该 cohort 对后续调参即退役，只能在新版本、从未暴露的 cohort 上重新开始。Suite/version、访问 principal、查询次数和退役原因必须审计；任何泄漏、按保留集调参、运行后改阈值/样本、删除失败 Trial 或配置漂移都会使本轮结论 `INVALID`。

Policy Agent 只接收完成当前任务所需的公共输入；Evolver 只接收获准的 validation 轨迹与汇总；Eval principal 独占 sealed cohort 和最终 Oracle。三者使用不同 principal，涉及 Agent 连续性时还必须使用不同 AgentSession。当前 `coding_eval/v1` 的 Runtime 私有隐藏 Validator 只对 Policy Agent 隐藏，仓库开发者和人工 Evolver 可以读取，因此只能用于管线校准和 Agent-side hidden checks，不能充当对人工 Evolver 密封的 held-out。

Agent 行为、智能效果和可泛化收益声明必须使用隔离 Held-out。事务、状态机、权限等确定性正确性变更若不主张统计泛化，可以把 Held-out 标为“不适用”，但必须使用独立冻结的故障矩阵、正常路径对照和回归证明声明范围，且不得外推为模型或产品效果提升。

安全正确性使用字典序硬门禁，不与平均效果加权抵消：`false accepted=0`、跨 Scope/Thread/Session 污染=0、未授权或重复副作用=0、cancel/fence 后迟到结果接纳=0、预算硬限制突破=0、评测泄漏/篡改=0。只有通过硬门禁的候选才能按预注册 promotion rule 比较 safe acceptance、恢复率、每次 safe accepted 的成本、Token、延迟、人工介入、Generalization Gap 和相对强 Baseline 的边际收益；只有达到预注册最小样本量才报告 p95，否则展示逐 Trial/原始分布。未达到最小效果阈值、超过成本上限或不确定性不足时，决策只能是 `ROLLBACK` 或 `INCONCLUSIVE`，不能看完结果再调整标准。

演进分为三层：

1. `L1 人工评测驱动演进` 是当前默认开发方法；人分析失败、提出 Hypothesis 和受限候选变更，由独立验证边界评测。当前尚无通用独立 Eval Runtime，现阶段只能生成 Verification Evidence，不能冒充 Runtime `AcceptanceRecord`。
2. `L2 Agent 辅助评测驱动演进` 只允许 Agent 生成 ChangeProposal/候选 Patch。外部 Codex/Claude 等离线辅助可以先使用版本化文件 Bundle，并由人负责隔离与评测；作为 Runtime 一等能力时，持久实验索引依赖 PROD-01B，Full/Raw Backend 依赖 PROD-02，受控执行与权限隔离依赖 PROD-03，ChildInvocation/Handoff 依赖 PROD-04。候选发布验证还依赖 INC-03，且仍须经过 Offline Eval、独立 Review、Shadow、人工批准和可回滚 Canary。
3. `L3 生产自主 Harness 演进` 当前不实施，也不因 Agent 能生成 Patch 而视为具备。依赖顺序固定为：INC-03 提供 ChangeSet、VerificationRun、Shadow/Canary/Rollback；INC-04 提供 Learning/Guardrail 的审批、替代与退役；INC-05 提供运营、Game Day 和长期复发评价。三阶段成熟后仍需重新立项，不能自动解锁。

当前已有固定 Coding 任务、对 Policy Agent 隐藏的 Runtime 私有 Validator、任务校准和三方案消融管线，但没有对人工 Evolver 密封的 held-out，固定 Coding 三方案的真实模型效果对照也尚未完成，3 个任务不足以形成泛化结论。脚本/Fake Model 结果只能验证控制流；示例指标必须标为“示例（非实测）”。只有绑定版本化 Run、Trial、manifest 和 Evidence 的结果才能进入验收、Handoff 或简历结论。

后续适用的 PROD/INC Plan 固定追加：

```text
### Harness Evolution 实验
- scope_id / experiment_id / version：
- lifecycle_status：PROPOSED | RUNNING | FROZEN | COMPLETED | INVALID
- decision：KEEP | ROLLBACK | INCONCLUSIVE
- Evolver / Policy / Eval principal：
- 失败/工作负载与证据引用：
- suite/split manifest、完整分母与 run/trial refs：
- Baseline / candidate ChangeSet hash 与强对照：
- 可证伪假设：
- mutation_axis / 单一 Mutation / 白名单范围：
- 固定项与 manifest/hash：
- Validation / Held-out 隔离、访问审计与 query budget：
- promotion rule / 最小效果 / 成本上限 / 不确定性方法：
- 安全硬门禁：
- 效果、成本、延迟、人工介入：
- Generalization Gap / 回归：
- Incident / Regression 落点：
- invalidation_reason / 决策批准者与时间 / 回滚 / next_action：
```

纯协议/文档批次可以将 Harness Evolution 实验标为“不适用”，但必须说明当前没有可运行行为、已有的负向与正常对照，以及由哪个后续批次完成真实行为验证。未严格复现官方 Evo-Bench 的完整任务、角色、轮次、预算、隔离和计分协议时，本项目只能称为“内部 Harness Evolution Experiment/Pilot”或“评测驱动演进实验”，不宣称完成官方 Evo-Bench 或取得其榜单成绩。

## 生产边界与模块形态

第一阶段保持 self-hosted、单组织/单信任域、单机优先的 production-shaped modular monolith。模块边界按 Thread/Message、Invocation/Session、Event/Incident、Artifact/Context、Capability/Tool、Scenario/Plugin 和 Web/API 划分；SQLite 继续作为本地模式。

只有真实锁竞争、吞吐、可用性、数据规模或隔离证据达到冻结阈值时，才引入 PostgreSQL、外部队列、对象存储或远程 Worker。微服务数量不是成熟度指标。

## SLI / SLO

Core 硬目标：跨 Scope、Thread、AgentSession、Artifact 或 ExecutionEnvironment 的污染为 0，供应商 SessionBinding 串线为 0；未授权副作用为 0；已 committed 的 Message 和已确认 RuntimeEvent 永久丢失为 0；未收到 DeliveryAck 却记录为 delivered 为 0；重试导致的重复可见或不可逆副作用为 0；预算硬限制突破为 0；无匹配 Acceptance 证据的 false accepted 为 0。

临时投递失败、重试次数和送达延迟属于运行指标，不承诺永不发生。交互指标还包含首响应延迟、Invocation 成功/取消/恢复、Session 恢复率、Handoff 有效率、无进展轮次、人工接管率和每个成功交互的成本。多模态按来源绑定正确率、结构化事实准确率和未审范围统计。Coding 指标继续作为目标插件指标，不能代表全产品。

RPO/RTO 的数值目标将在 PROD-01 结合 Journal 和故障注入冻结；当前不以未实现机制承诺具体生产数值。

## INC 联动

- 对应阶段：`INC-00` 已完成；`PROD-01A` 开始提供 `INC-01` 协议前置。
- 当前状态：`INC-00` 文档冻结完成；RuntimeEvent/Acceptance/Invocation 值协议与同步不变量已实现，Detector、持久 Journal、Ledger、Replay 和运营能力尚未实现。
- 新增风险与事故目录：Thread/Session 串线、Message 丢失/重复/乱序、Route 循环、用户介入丢失、Context 污染、媒体绑定错误、错误 Acceptance、权限和预算越界。
- RuntimeEvent / Detector / Invariant：定义通用 `false_acceptance`；现有 Coding `false_completed` 仅作为历史名称映射到 Coding Plugin 的 acceptance 子类，不进入 Core 新协议；后续补 Thread/Message/Session/Route 事件。
- Evidence / 脱敏 / 审计：对话和媒体只保存受控引用、hash、时间/区域和脱敏摘要；不复制私有 Session 或原始思维。
- 止损 / 恢复 / 人工权限：跨边界、权限、重复副作用和 false acceptance 必须 fail-closed；开放域语义冲突转人工。
- Replay / Fault Injection：本批只冻结目录，实际 Event Journal 和 Replay 分别由 PROD-01 与后续 INC 阶段实现。
- 事故用例与正常对照：每条通用规则必须同时覆盖交互或插件合法路径，避免 Coding 规则误伤普通 Thread。
- SLI/SLO 与覆盖率：沿用 detected/prevented/missed/escaped/false-positive/recurrence、MTTD/MTTC/MTTR，并按 Core/Plugin 分层报告。
- 本批完成门禁：Plan25 与覆盖说明完成泛化；Backlog、Learning Path、HANDOFF 与本 Charter 一致。
- 剩余缺口及后续归属：Journal/Ledger 属 PROD-01/INC-01；PROD-01 只额外建立四组 Observe/Shadow 信号并将 INC-02 标为“部分 Shadow”；完整 Detector、Replay、Learning 和运营按 INC-02～INC-05 推进。
- 需要同步的文档：`Plan/Plan25.md`、`Plan/闭环覆盖范围.md`、`OPTIMIZATION_BACKLOG.md`、`LEARNING_PATH.md`、`HANDOFF.md`。

## 生产批次

### PROD-00：Harness 产品定位与 Runtime Charter

本计划及同步文档即为验收物。只冻结边界，不修改 Runtime 行为。

这是唯一的 Charter 例外批次：完成门禁是代码事实核对、跨文档一致性、`git diff --check` 和现有回归保持通过，不要求为未实现行为伪造故障演练。从 PROD-01 起故障注入必须匹配已实现边界：纯协议使用确定性负向构造，Store/进程批次再执行事务中断、重复投递和 `kill -9`。

### PROD-01：Durable Thread、Message、Invocation 与 Event Journal

为满足“一次只实施一小批”，PROD-01 固定拆为：

1. **PROD-01A 领域协议与迁移骨架（已完成）**：实现最小 Scope、Thread、Turn、Message、通用 AgentProfile/Role、AgentInstance、AgentSession、Invocation/Attempt、Outcome、AcceptancePolicy/Record 和 RuntimeEvent 协议；明确 Message/Artifact 唯一真相源与 Coding 兼容映射。Invocation 本批冻结 `input_refs + input_digest + policy_snapshot_ref + budget_reservation`，以及 parent/child、执行/清理双状态轴、终止原因、deadline、lease、fencing 和资源引用协议；不假装已经有 PROD-03 的完整 Grant 或 PROD-05 的 ContextManifest。
2. **PROD-01B 状态 Store、Journal 与 Outbox（当前下一批）**：SQLite 状态表是当前业务真相源，Journal 是不可变审计，Snapshot 是兼容检查点；状态、Event、Outbox 和最小 BudgetLedger 预留/结算同事务提交，并增加持久查询。Provider/Tool 细分策略分别在 PROD-02/03 扩展，容量分析归 PROD-06。
3. **PROD-01C Durable Invocation**：durable enqueue、幂等、claim/lease/heartbeat、fencing、watchdog、孤儿识别、级联取消、幂等 Finalizer/Reaper、重启恢复和取消意图持久化；进程内执行路径只能承诺逻辑失权和拒绝迟到结果，Backend 请求与进程的物理硬取消归 PROD-02。
4. **PROD-01D 兼容接入与 Web 查询**：把现有 TaskGraph/ScenarioRuntime/Coding 纵向切片作为 Thread 中可选工作接入，保留回归；Web 先支持持久 Thread/Invocation 查询，不在本批实现完整 Agent 泳道。
5. **PROD-01E INC-01 与首批 Shadow**：完成 INC-01 Observe-only；再建立 false acceptance、消息完整性、Thread/Session 错绑、取消/迟到/孤儿/清理失败四组 Observe/Shadow 信号。消息状态不一致是硬错误，正常离线重试不算事故，只有超过冻结时间窗才是 delivery SLO breach。此时 INC-02 只标记为“部分 Shadow”。

PROD-01B 的权威 Store 必须在同一事务中校验同 Thread/Turn/AgentSession 绑定、Runtime-only Acceptance 签发、Event ID/序号唯一与 append-only，并以 Attempt/Child/Lease/Grant/Resource 索引二次核对 `Invocation.closed`。状态、Event、Outbox 和最小 BudgetLedger 必须原子提交；这些都不能由 01A 的单个 DTO 伪装为已完成。

PROD-01A 是纯领域契约批次。它只保存 Backend、Capability、Context、Mailbox 等对象的可选或不透明版本引用，明确不实现 Mailbox 队列/投递、SessionBinding、模型调用、CapabilityGrant/Gateway、Context Compiler/Manifest、SQLite Store/Journal、调度、Web 或现有 Runtime 执行接入。Coding 兼容只做协议映射和往返测试，运行接入留给 PROD-01D。

#### PROD-01A 验收结论

- 新增独立于 Coding 的 `runtime_domain`：所有持久实体和嵌套引用按 Scope fail-closed，协议使用严格版本和 JSON 往返；Message 保存正文与 Artifact 引用，不复制附件内容。
- Invocation/Attempt 已冻结不可变输入内容哈希、策略和预算引用、parent/child、执行/清理双状态轴、终止记录、deadline、lease、单调 fencing 和资源引用；`closed` 是派生不变量，执行成功不推出资源已回收或 Outcome 已接受。
- late result 采用纯 admission 协议：旧/未来 token、取消后、终态后、过期 lease/deadline、错误 Scope/Thread/Attempt/输入/策略均确定性拒绝；完全相同的幂等重复只返回 no-op。
- AcceptancePolicy 要求匹配 subject、版本/时效和可选独立 evaluator；模型或旧 Coding 的 passed/verified/completed 只能成为 Evidence，不能生成 accepted。真正的 Runtime 独占写入权由 PROD-01B Store 事务边界实现。
- RuntimeEvent 只允许小型、深冻结 JSON 元数据和受控引用，不保存 Message 正文、Prompt、Completion、原始媒体或 bytes；append-only、序号唯一和持久授权仍属于 PROD-01B。
- Coding 通过单向兼容适配器映射 Role、Worker binding、完整 TaskSpec 快照、Artifact 内容哈希和 Verification Evidence；Core 不反向依赖 Coding，旧执行器行为未改变。

验收证据：64 项 PROD-01A 定向协议测试通过；默认全量共执行 277 项，其中 273 项通过、4 项需要真实浏览器的测试按设计跳过、0 failure、0 error；Python compileall 与 `git diff --check` 通过。纯协议批次没有进程、SQLite 事务或副作用可供 `kill -9`，因此本批故障证据是跨 Scope/Thread、伪造 Acceptance、错误状态迁移、取消后扩权、过期 lease/fence、迟到结果、Event 敏感载荷和内容漂移的确定性负向用例；事务中断、重复投递和进程恢复演练由 PROD-01B/01C 承接。无需用户手动检验。

### PROD-02：Agent Backend v2、Session 与 Streaming

建立 Raw Model/Full Agent Backend、统一流式 AgentEvent、硬取消、SessionBinding、usage/finish reason、供应商错误、受控 fallback 和 canary。

### PROD-03：Capability、Tool Gateway 与执行隔离

建立 InvocationGrant、Artifact/Memory/Workspace/Tool Gateway、Secret Broker、默认断网、资源配额、高风险 Approval 和副作用审计。

### PROD-04：交互式 Multi-Agent 协作控制面

实现 Mailbox、结构化 Handoff、RouteEdge、并行/顺序协作、独立 Review、用户介入、循环终止，以及 Thread/Agent 泳道和讨论流。只有 PROD-01～03 的父子 Invocation、取消、fencing 和资源回收语义可用后，才开放动态 Specialist 与多级 Handoff。

### PROD-05：Context、共享记忆与多模态工作区

实现 Context Compiler/Manifest、版本/TTL/ACL、Session 压缩、共享记忆治理、检索评测和图片/音频/视频附件到通用 Thread 的产品链路；同步扩展媒体绑定 Detector。

### PROD-06：插件产品化与效果/容量验证

将当前 Coding 纵向切片封装为显式插件，并保持 VisionForge 独立 Scenario Plugin；只有 Plugin SPI 具备依赖协议后才评估二者依赖。建立交互、协作、多模态和插件/工具任务分层评测；加入背压、公平性、配额、压力与 soak，再以证据决定存储和队列演进；同步启动 INC-05 的容量与事故运营指标。

### PROD-07：迁移与事故运营

完成 Schema/Prompt/Plugin/Model 迁移、golden trace replay、canary、回滚、备份恢复、Game Day、Runbook、人工接管和 Incident Operations。

## PROD-00 验收结论

- 产品中心已从 Coding 专用 Harness 纠偏为以通用 Multi-Agent Runtime 为执行内核的交互式多模态 Multi-Agent Harness；
- Coding 纵向切片和独立 VisionForge Scenario Plugin 保留，但不再定义 Core；CodingPlugin 尚未实现；
- Scope、Thread、Turn、Message、AgentInstance、AgentSession、SessionBinding、Invocation、Route、Context、Capability 和 Acceptance 的边界已冻结；
- 共享记忆、权限、并行和事故闭环已纳入通用领域；
- 第二个真实代码仓库已从 PROD-00 完成条件移到 Coding 插件化后的 Canary/dogfood；
- 本批没有代码行为、模型、网络、媒体、外部仓库或不可逆副作用。

验收证据：`git diff --check` 通过；默认单元回归 213 项通过、4 项真实浏览器测试按设计跳过；Python compileall 通过。无需用户手动检验。

当前下一批固定为 **PROD-01B：状态 Store、Journal 与 Outbox**。

## 待验证事项

- 状态表、Journal、Outbox 和预算账本能否在 SQLite 单事务内保持原子、一致且可恢复。
- 现有 Coding/Scenario 执行接入 Thread 后，能否保持回归通过且不把 build/test 规则泄漏到普通交互。
- 真实多模型、长会话和多 Agent 并发是否能达到冻结的可靠性、成本与延迟目标；外部调用需届时重新授权。

## 待办事项

- 实施 PROD-01B 的持久 Store、Journal、Outbox、BudgetLedger 和查询协议。
- 随后实施 PROD-01C 的 durable Invocation、Finalizer/Reaper、fencing、取消与恢复。
- 按 PROD/INC 双轨补齐对应事故事件、负向用例、正常对照和覆盖指标。
- 对每个适用的行为修改同步填写 Harness Evolution 实验模板；PROD-01B 使用确定性轻量轨，只填写 Baseline、单一变更、故障矩阵、正常对照、固定门禁、回归和决策，Evolver/principal、样本量、Validation/Held-out cohort、query budget、统计效果等字段统一标为 `N/A`，不得因此阻塞基础设施开发，也不得把该证据外推为模型智能或 Held-out 效果结论。
