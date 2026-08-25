# Plan26：交互式多模态 Multi-Agent Harness 产品定位与 Runtime Charter

日期：2026-08-23

讨论主题：纠正 Coding 场景对产品边界的过拟合，冻结“Multi-Agent Harness 是项目本体、通用 Multi-Agent Runtime 是执行内核、专业能力以 Plugin 接入”的领域、验收与增量迁移路线。

## 当前状态

状态：**PROD-00 文档冻结已完成；实现已推进至 PROD-01B，其中 01B-3A 与 01B-3B-1 已完成/KEEP，完整 01B-3 仍进行中**。

2026-08-23，项目产品中心由“面向 Coding 的专用 Multi-Model Agent Harness”纠偏为：

> 一个以通用、可持久化 Multi-Agent Runtime 为执行内核的多模态 Multi-Agent Harness。用户可以在 Thread 中持续发送文本、图片、音频和视频；Agent 可以独立判断、并行工作、按依赖交接、使用受控工具并接受人工介入。Coding 和 VisionForge 是可插拔的专业能力，不是 Harness Core 的默认目的。

2026-08-25 术语澄清：2026-08-23 的实质决议是从 Coding 专用链路泛化到长期、多场景 Multi-Agent 系统；“Runtime 是产品本体”的旧简称被本节取代，但既有领域模型、实现状态和 PROD 顺序不变。

本计划最初用于冻结新的 Harness 产品边界、Runtime 内核领域语言、增量迁移路线、验收口径和事故联动；`PROD-00` 文档批次本身当时不修改 Runtime、不调用模型、不访问网络，也不读取外部仓库。后续实现状态以本文生产批次与 VerificationReport 为准。

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
2. `L2 Agent 辅助评测驱动演进` 只允许 Agent 生成 ChangeProposal/候选 Patch。外部 Codex/Claude 等离线辅助可以先使用版本化文件 Bundle，并由人负责隔离与评测；作为 Harness 一等能力时，持久实验索引依赖 PROD-01B，Full/Raw Backend 依赖 PROD-02，受控执行与权限隔离依赖 PROD-03，ChildInvocation/Handoff 依赖 PROD-04。候选发布验证还依赖 INC-03，且仍须经过 Offline Eval、独立 Review、Shadow、人工批准和可回滚 Canary。
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

- 对应阶段：`INC-00` 已完成；`PROD-01A` 提供 `INC-01` 值协议前置，`PROD-01B-2` 已提供首个持久 Thread Event 原子纵切。
- 当前状态：`INC-00` 文档冻结完成；RuntimeEvent/Acceptance/Invocation 值协议、同步不变量、concrete Thread+RuntimeEvent 持久化、01B-3A durable Outbox intent，以及 01B-3B-1 本地 claim/NACK/expiry-reclaim 生命周期已实现。完整跨领域 Journal、Transport publish/ACK/Receipt、Detector、Incident Ledger、Replay 和运营能力尚未实现，`INC-01` 仍待开始。
- 新增风险与事故目录：Thread/Session 串线、Message 丢失/重复/乱序、Route 循环、用户介入丢失、Context 污染、媒体绑定错误、错误 Acceptance、权限和预算越界。
- RuntimeEvent / Detector / Invariant：定义通用 `false_acceptance`；现有 Coding `false_completed` 仅作为历史名称映射到 Coding Plugin 的 acceptance 子类，不进入 Core 新协议；后续补 Thread/Message/Session/Route 事件。
- Evidence / 脱敏 / 审计：对话和媒体只保存受控引用、hash、时间/区域和脱敏摘要；不复制私有 Session 或原始思维。
- 止损 / 恢复 / 人工权限：跨边界、权限、重复副作用和 false acceptance 必须 fail-closed；开放域语义冲突转人工。
- Replay / Fault Injection：01B-1/01B-2 已增加 SQLite 事务、旁路、进程退出、并发和腐败注入的开发 Verification；生产 Event emission、Incident Replay 与运营演练仍由后续 PROD-01/INC 阶段实现。
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
2. **PROD-01B 状态 Store、Journal 与 Outbox（进行中）**：`PROD-01B-1` 已完成组件级 SQLite Schema、Migration 与 RuntimeUnitOfWork，`PROD-01B-2` 已完成 concrete Thread current-state 与 append-only RuntimeEvent 的原子纵切，`PROD-01B-3A` 已完成显式 Policy、Schema v3、真实 v2→v3 迁移和 durable Outbox intent 原子三写，`01B-3B-1` 已完成本地 claim/NACK/expiry-reclaim；下一步是 `01B-3B-2` Transport publish/ACK/Receipt。完整 01B 仍要求状态表作为当前业务真相源、Journal 作为不可变审计、Snapshot 作为兼容检查点，并将状态、Event、Outbox 和最小 BudgetLedger 预留/结算同事务提交。Provider/Tool 细分策略分别在 PROD-02/03 扩展，容量分析归 PROD-06。
3. **PROD-01C Durable Invocation**：durable enqueue、幂等、claim/lease/heartbeat、fencing、watchdog、孤儿识别、级联取消、幂等 Finalizer/Reaper、重启恢复和取消意图持久化；进程内执行路径只能承诺逻辑失权和拒绝迟到结果，Backend 请求与进程的物理硬取消归 PROD-02。
4. **PROD-01D 兼容接入与 Web 查询**：把现有 TaskGraph/ScenarioRuntime/Coding 纵向切片作为 Thread 中可选工作接入，保留回归；Web 先支持持久 Thread/Invocation 查询，不在本批实现完整 Agent 泳道。
5. **PROD-01E INC-01 与首批 Shadow**：完成 INC-01 Observe-only；再建立 false acceptance、消息完整性、Thread/Session 错绑、取消/迟到/孤儿/清理失败四组 Observe/Shadow 信号。消息状态不一致是硬错误，正常离线重试不算事故，只有超过冻结时间窗才是 delivery SLO breach。此时 INC-02 只标记为“部分 Shadow”。

PROD-01B 的权威 Store 必须在同一事务中校验同 Thread/Turn/AgentSession 绑定、Runtime-only Acceptance 签发、Event ID/序号唯一与 append-only，并以 Attempt/Child/Lease/Grant/Resource 索引二次核对 `Invocation.closed`。状态、Event、Outbox 和最小 BudgetLedger 必须原子提交；这些都不能由 01A 的单个 DTO 伪装为已完成。

#### PROD-01B-1：SQLite Schema、Migration 与 RuntimeUnitOfWork

状态：**已完成（2026-08-25）**。本切片只建立 Store 的事务底座；完整 `PROD-01B` 仍为进行中，不得宣称 State Store、Journal、Outbox、BudgetLedger 或 Runtime-only Acceptance 已完成。

`InvariantCard INV-PROD-01B-1-UOW-ATOMICITY-v1`：

- **scope**：本地文件型 SQLite；组件名固定为 `runtime_kernel`，使用组件级 schema metadata/migration ledger，与旧 `runtime_snapshots`、Memory 和 Scenario 表共存，不占用 DB-global `PRAGMA user_version/application_id`；
- **schema**：未安装组件 schema 的空库或只含未纳管兼容表的数据库可原子初始化到 v1；重复初始化为只读 no-op；未来版本、metadata/ledger 缺口或 checksum 漂移在修改前 fail-closed；
- **transaction**：只有显式 `commit()` 成功才可持久化；显式 rollback、正常退出但未 commit、异常退出和 commit 前故障均必须在重开后不可见；commit 成功后全部写入在重开后可见，禁止部分可见；
- **connection**：每个 UoW 连接都启用 foreign keys、固定 busy timeout 和 `synchronous=FULL`，文件数据库必须使用 WAL；
- **lifecycle**：UoW 不可嵌套、跨线程或复用；commit/rollback/close 后不能形成隐式第二事务；异常不得被吞掉；
- **fault points**：只提供可回滚线性化点 `migration_before_commit`、`uow_after_begin`、`uow_before_commit`；不提供可抛异常的 after-commit hook，以免制造“调用方收到失败但数据已提交”的歧义；
- **evidence**：fresh/reinitialize/future/corruption、migration fault、显式 commit/rollback、无 commit、异常、FK、WAL、busy timeout、非法状态、commit 前/后进程退出、重开与 integrity check；
- **nonclaims**：本切片不实现领域 Repository、真实 state+event mutation、Journal append/query、Outbox 投递/Ack、Budget、权威关系/Acceptance、Web、锁竞争容量、掉电保证、lease/fencing/cancel/Reaper、Detector/Incident Store 或 PostgreSQL；
- **INC**：只生成记录在 VerificationReport 中的合成 fault-injection 测试证据，作为 `INC-01` 的事务前置；本切片没有生产故障证据对象、RuntimeEvent emission、Journal、Replay 或 Incident Store。Detector 数仍为 0，`INC-01` 保持待开始，不报告 detected/missed/MTTD/MTTR。

实现摘要（详细、权威证据见 [`VerificationReports/PROD-01B.md`](../VerificationReports/PROD-01B.md)；这是开发 Verification，不是 `AcceptanceRecord`）：

- 新增 `runtime_persistence` 包，仅纳管 `runtime_schema_metadata` 与 `runtime_schema_migrations` 两张表；旧 `SQLiteRuntimeStore` 仍只是兼容 Snapshot Store。UoW 采用显式 commit、WAL、foreign keys、`synchronous=FULL`、固定 busy timeout、线程归属和 fail-closed 状态机；业务 UoW 禁止事务控制 SQL、Schema DDL、ATTACH/DETACH、可变 PRAGMA 与受管 metadata DML。
- 预切片 Baseline 为 Runtime 64/64、全量 277 项执行（273 通过、4 跳过）。本切片冻结单一 mutation：引入组件级 v1 Migration 与显式 RuntimeUnitOfWork，不增加领域 Repository、Event Journal 或 Incident 能力。
- 开发中先后由负向测试与独立 Review 暴露并关闭：原始 connection/SQL commit 绕过、ALTER/DDL authorizer 参数绕过、结果迭代器泄漏 cursor、`INSERT OR ROLLBACK` 终止外层事务、rollback failure 被隐藏、WAL/Schema 检查时序与 REAL `1.5` 版本被整数强转等缺陷；每条均形成回归或故障注入用例。
- 冻结证据绑定 `HEAD=12f315e103bb3fd4d8879feb9331bb605ea51a64` 的 dirty 工作区，以及实现 hash `52aaad07318ed17415bde9686ada2a6fd9b5effe29938beb271f199e7679ba59`、测试 hash `f1ef68b22517bca828f0a5063e297dfad545de345ef1a4db68682afc8416e13e`。Python 3.9.6、SQLite 3.51.0；01B-1 专项 32/32、Runtime 96/96；默认全量执行 309 项，305 通过、4 个真实浏览器 E2E 按设计跳过、0 failure/error；compileall 与 `git diff HEAD --check` 通过。两个独立 Review 结论分别为 `APPROVE` 与 `APPROVE WITH NOTES`。
- 合成故障矩阵覆盖 migration/commit 前回滚、进程在 commit 前/后退出、重开 all-or-none、future/corrupt schema、锁忙、跨线程、事务边界逃逸与双异常链；正常对照覆盖 fresh/reinitialize、旧 Snapshot 共存、显式 commit、全量回归和 integrity check。无需用户手动测试。

确定性 Harness Evolution 轻量记录：`lifecycle_status=COMPLETED`，`decision=KEEP`。Baseline、单一 mutation、固定故障矩阵、正常对照、独立 Review 与回归均已绑定上述证据；Evolver、真实模型、Validation/Held-out、query budget、样本量和统计效果全部为 `N/A`。该决定只保留 01B-1 事务底座，不构成 Runtime Acceptance，也不支持智能效果或生产可靠性外推。

01B-1 收口后的下一动作已经完成：`PROD-01B-2` 按 EXPECTED_RED → 最小实现 → 故障/回归门禁收口；Outbox、Budget、权威关系/Acceptance 和更完整的查询/恢复继续分后续切片，不在 01B-2 偷跑。

#### PROD-01B-2：Thread 当前状态与 RuntimeEvent 原子提交

状态：**已完成（2026-08-25）；契约、实现与开发 Verification 已收口。** 本切片选择真实 `Thread` 作为第一个状态纵切，并复用既有 `RuntimeEvent` 值协议；不建立可容纳任意 JSON 的通用 aggregate blob，也不把一个业务场景重新冒充 Harness 的全部状态模型。

`InvariantCard INV-PROD-01B-2-THREAD-EVENT-ATOMICITY-v1`：

- **single mutation**：一次 `ThreadEventMutation` 只提交一个 post-state `Thread` 与恰好一个 `RuntimeEvent`；二者由调用方显式 `RuntimeUnitOfWork.commit()` 共同线性化，任一校验、写入、故障或 commit 失败均不得留下 orphan state 或 orphan event；
- **identity/version**：Thread 身份固定为 `(scope_id, thread_id)`。create 只接受 `expected_version=0`、`Thread.version=1`、`state=open`；update 要求当前版本等于 expected、post version=`expected+1`、`scope_id/thread_id/created_at` 不变且 `updated_at` 严格前进。`archived` 为终态；允许 `open→open/paused/archived` 与 `paused→paused/open/archived`，同状态更新必须至少改变 title、participants 或 policy；
- **event binding**：Event 的 `scope_id`、`aggregate_ref`、`aggregate_version` 和 `thread_ref` 必须等于 post-state Thread 的引用。create 使用 `core:thread_created`；普通字段更新使用 `core:thread_updated`；暂停、恢复和归档分别使用 `core:thread_paused`、`core:thread_resumed`、`core:thread_archived`。payload 的 `state` 必须等于 post-state，update 的 `previous_state` 必须等于 pre-state；
- **three versions**：`aggregate_version` 是 post-state Thread 版本；`sequence_no` 是该 `(scope_id, aggregate_type, aggregate_id)` Journal 的连续序号；`event_version` 恒为 1。三者语义独立，不用偶然相等推导彼此；首个 sequence 为 1，后续必须严格 `last+1`；
- **idempotency/conflict**：`event_id` 与 Event 表内 `idempotency_key` 均全局唯一；aggregate sequence 按 `(scope_id, aggregate_type, aggregate_id, sequence_no)` 唯一。Journal 持久保存 canonical Event digest、result Thread digest 与 mutation digest。完全相同的历史成功 mutation 即使 Thread 已继续前进也返回 `ALREADY_COMMITTED` 且零写入；同 event ID、idempotency key 或 sequence 对应不同内容分别返回 typed conflict；duplicate 判断先于 expected-version 判断；
- **append-only/CAS**：Thread update 使用 `WHERE version=? AND last_sequence_no=?`；Thread head 通过 deferred composite foreign key 绑定同 aggregate/version/sequence/result digest 的 last Event。公共 UoW SQL 不得直接 INSERT/UPDATE/DELETE 受管 Thread/Event 表；Event 另有持久 collision INSERT、UPDATE、DELETE 拒绝 trigger，并以 `WITHOUT ROWID` 消除隐式 rowid 的 `REPLACE` 改写路径。它们是 Harness 单信任域内的应用边界，不宣称能抵御拥有 SQLite 文件写权限的管理员；
- **schema/migration**：Schema v2 新增 concrete `runtime_threads` 与通用 envelope `runtime_events`，保留 v1 名称和 checksum；初始化器按连续 ledger prefix 在同一 `BEGIN EXCLUSIVE` 中原子执行 fresh v1→v2 或既有 v1→v2，reinitialize 为 no-op，future/gap/checksum/必需 DDL 漂移均 fail-closed；旧 `runtime_snapshots`、Memory、Scenario 与其他未纳管表不回填、不双写、不删除；
- **reads/integrity**：最小查询只包含 Thread current by scoped ID、Event by global event ID、按 aggregate/sequence 有序读取；读回必须以既有 `Thread.from_dict()` / `RuntimeEvent.from_dict()` 重建，并复核 canonical digest、投影列与 state↔last-event 链。跨 Scope 搜索、通用分页、Replay 与事件重建状态不在本片；
- **fault points**：只新增 `state_event_after_state_write` 与 `state_event_after_event_append` 两个可回滚点，并复用 `uow_before_commit`；禁止可抛 after-commit hook。异常必须终止并回滚该 UoW，调用方不能 catch 后提交半包；
- **required red/green gates**：fresh v2、精确 v1→v2、迁移故障 rollback、旧 Snapshot 共存；create/update/reopen；wrong binding/transition/version/sequence；exact retry after later commits；event/idempotency/sequence/stale conflict；公共 SQL 绕过和 raw Event rewrite/delete；两个 fault window、commit 前/后子进程退出、锁竞争；digest/JSON/link corruption fail-closed；Runtime 子集、全量、compileall 与 diff check；
- **nonclaims**：本切片不实现 Scope/Turn/Message/AgentSession/Invocation Repository、Outbox、BudgetLedger、Acceptance writer、producer authorization、delivery/Ack、Incident/Detector/Replay、旧 Executor/Web 接线、容量/soak、掉电保证或 PostgreSQL，因此不得称完整 State Store、完整 Journal 查询、完整 PROD-01B 或 INC-01 已完成；
- **INC / Harness Evolution**：故障注入只形成开发 Verification 证据，Detector、IncidentSignal/Ledger 和 Replay 数仍为 0，不报告 detected/missed/MTTD/MTTR。冻结时的确定性轻量轨记录为 `lifecycle_status=FROZEN`、`decision=INCONCLUSIVE`，Baseline=`b864b20093f20077424fc81a564ecffecbf7ecb0` clean；真实模型、Evolver、Validation/Held-out、query budget、样本量和统计效果均为 `N/A`。最终 `COMPLETED/KEEP` 结果与证据见下方 VerificationReport；

冻结时的关键取舍：Plan25 中省略 Scope 的 aggregate sequence 示例在本实现按 `ScopedRef` 语义具体化为 scope-scoped sequence；`event_id` 和事件命名空间的 `idempotency_key` 仍保持全局唯一。Event causation/parent 引用的持久存在性、其他 aggregate 的 typed State Table 与生产者授权留后续切片，不能由 v2 表结构暗示已经具备。

实现摘要（详细、权威证据见 [`VerificationReports/PROD-01B.md`](../VerificationReports/PROD-01B.md)；这是开发 Verification，不是 `AcceptanceRecord`）：

- Schema v2 新增 `runtime_threads` 和 `runtime_events`，并将 v1-only 初始化器升级为连续 migration registry；已发布 v1 migration 名称、checksum 与 DDL hash 被测试常量独立锚定。fresh v2、真实 v1→v2、reinitialize、迁移故障回滚、旧 Snapshot/未纳管表共存和必需 DDL 漂移拒绝均已自动验证。
- 新增 `ThreadEventMutation`、`SQLiteThreadEventStore`、typed conflict/corruption errors 和 `APPLIED/ALREADY_COMMITTED` 结果。create/update 使用 CAS；Event、结果 Thread 与 mutation 分别保存 canonical digest；查询会重建协议对象并复核投影、digest、当前 head 与 last Event 链，完整性扫描还会反向拒绝 orphan/落后 Thread Event。
- EXPECTED_RED 首证据为新测试在导入阶段因 01B-2 API 不存在而失败；实现后专项 68/68、Runtime 132/132、默认全量执行 345 项，其中 341 通过、4 个既有真实浏览器 E2E 按设计跳过、0 failure/error。`PYTHONPYCACHEPREFIX=/private/tmp/multiagent-pycache python3 -m compileall -q coding_workflow tests` 与 `git diff --check` 通过，无需用户手动测试。
- 最终证据绑定 `HEAD=b864b20093f20077424fc81a564ecffecbf7ecb0` 的 dirty 工作区、Python 3.9.6、SQLite 3.51.0，以及实现 hash：`sqlite.py=4e052962d0047b90d0872136044ca4c5d80dadaad3c7e854910ab1bd145b497d`、`state_event.py=ba1a6974b067666b6eb12b7f41431861c8ea672645e301dbbd3d1f5628c26a2c`、`runtime_persistence/__init__.py=41b0fc9d1e5de90206370452d0891588acbb36d9908f67bd60a797e2e8867f41`、`coding_workflow/__init__.py=5a3ff4ff3358b5046aecb1a8cf90e92dd6b62ded314fe0d0c7851fe0eeb8180d`；测试 hash：`test_runtime_sqlite_uow.py=e1e07c5c47c33112f0f9a35ac73e188a8b6ad491f7390f37daa5327eca8416fd`、`test_runtime_thread_event_store.py=c1c2e700283b48e77c80ac15ab25da7a9d08bd4ae55eaa4c2f989f1bfc7b7f2c`。两个独立只读 Review 均为 `APPROVE`；它们是审查建议，不签发 Runtime Acceptance。
- 红绿过程中实际暴露并关闭了：`INSERT OR REPLACE` 绕过 UPDATE/DELETE trigger、隐式 rowid collision 改写历史 Event、Store 与异库 UoW 静默误接、跨线程 abort 覆盖 typed error、幂等快路径在历史 Event/当前 Thread head/最新 head Event 损坏时误报成功，以及完整性扫描漏掉 orphan Thread Event。每条均有回归，证明本切片不是“想到功能就加”，而是用反例修改实现与边界。
- INC 联动只增加 orphan state/event、历史改写、幂等冲突、sequence/version drift、跨 Scope/跨数据库误绑和持久数据腐败的开发期预防/故障证据；没有 Detector、IncidentSignal/Ledger、Outbox、Replay 或生产事件观察，`INC-01` 仍为待开始，不报告 detected/missed/MTTD/MTTR。

确定性 Harness Evolution 轻量记录：`lifecycle_status=COMPLETED`、`decision=KEEP`。保留范围仅是 01B-2 的 Thread+RuntimeEvent 原子纵切；真实模型、Evolver、Validation/Held-out、query budget、样本量和统计效果均为 `N/A`，不能外推为完整 Journal、完整 PROD-01B、模型智能或生产可靠性。

01B-2 收口后的下一动作已经执行：`PROD-01B-3` 的 Event+Outbox InvariantCard 已冻结，结构与显式 Policy 两张 EXPECTED_RED 卡已先建立，随后 `01B-3A` 生产实现与独立挑战、`01B-3B-1` 本地 claim/NACK 生命周期均已完成。下一动作是 `01B-3B-2` Transport publish/ACK/Receipt。

#### PROD-01B-3：Event+Outbox 可靠发布边界

状态：**契约已冻结（2026-08-25）；`01B-3A=COMPLETED/KEEP`、`01B-3B-1=COMPLETED/KEEP`，完整 `01B-3=IN_PROGRESS/INCONCLUSIVE`。** 3A 已把现有 concrete Thread+RuntimeEvent mutation 扩展为可恢复的 durable intent；3B-1 已实现本地 claim/NACK/expiry-reclaim，3B-2 的 Transport publish/ACK/Receipt 尚未实现。本切片不建立任意命令队列、真实消息 Broker 或最终用户消息投递系统。

`InvariantCard INV-PROD-01B-3-EVENT-OUTBOX-ATOMICITY-v1`：

下列 InvariantCard 描述完整 01B-3；其中 claim/NACK/expiry-reclaim 已由 3B-1 实现，Transport publish/ACK/Receipt 仍是 3B-2 契约与验收目标，不是 3A/3B-1 已实现事实。

- **single mutation**：Schema v3 生效后，现有 `SQLiteThreadEventStore.apply()` 的每个新成功 mutation 必须在同一 `RuntimeUnitOfWork` 中提交一个 post-state Thread、一个 RuntimeEvent 和恰好一个 Outbox intent；不得保留仍能公开提交 Event-without-Outbox 的旧写入口。state 后、event 后、outbox 后或 commit 前任一失败，重开均为 none；commit 成功后重开必须为 all；
- **schema/current truth**：v3 新增 `runtime_outbox` 和 `runtime_outbox_receipts`，并在不改变 v2 migration/checksum 的前提下新增 `UNIQUE runtime_events(event_id, scope_id)` 父键索引；否则 SQLite 不能建立下述复合 FK。`runtime_outbox` 同时保存不可变 intent identity 与当前发布状态；`runtime_outbox_receipts` 保存不可变 `OutboxPublishAck` 证据。两表均 `WITHOUT ROWID`，纳入 managed-table authorizer、必需 DDL 校验和正反向 integrity scan；
- **frozen schema**：`runtime_outbox` 固定非空 `TEXT`：`delivery_key PK`、`source_event_id UNIQUE`、`scope_id`、固定 `destination`、`event_digest(64)`、`created_at`、`intent_digest(64)`、`policy_version`、`policy_digest(64)`、`state`、`updated_at`；固定精确 `INTEGER`：`claim_generation>=0`、`attempt_count>=0`；可空 `TEXT`：`available_at`、`claim_token`、`publisher_id`、`claim_expires_at`、`last_error_code`、`suppress_reason`、`published_at`、`receipt_id UNIQUE`。以 `(source_event_id, scope_id)` 复合 FK 引用 `runtime_events(event_id, scope_id)`。`runtime_outbox_receipts` 固定非空 `TEXT`：`receipt_id PK`、`delivery_key UNIQUE`、`destination`、`source_event_id`、`event_digest(64)`、`claim_token`、`publisher_id`、`sink_id`、`ack_id`、`acked_at`、`ack_digest(64)`；`claim_generation` 为精确正 `INTEGER`，并有 `UNIQUE(receipt_id, delivery_key)` 与 `UNIQUE(sink_id, ack_id)`。Receipt 的 `delivery_key` FK 指向 Outbox；Outbox 的 `(receipt_id, delivery_key)` 以 deferred composite FK 反向绑定同一 Receipt，确保 ACK 事务可原子插入 receipt 再链接 Outbox；
- **state checks**：在状态相关的可空字段中，`LEGACY_SUPPRESSED` 必须 `generation=attempt=0`、仅 `suppress_reason='pre_outbox_cutover'` 非空，available/claim/error/published/receipt 全空，且 `updated_at=created_at=Event.recorded_at`；新建 `PENDING` 必须 `generation=attempt=0`、`available_at=updated_at=created_at=Event.recorded_at`、`last_error_code=NULL`，其余 suppress/claim/published/receipt 全空；NACK 后的 `PENDING` 必须 generation=attempt>=1、available 与非空 typed error 存在、updated 为 failure time；`CLAIMED` 必须 available/suppress/error/published/receipt 全空，generation=attempt>=1 且 token/publisher/expiry 全非空；`PUBLISHED` 必须 available/suppress/claim/error 全空，generation=attempt>=1 且 published/receipt 全非空。所有时间字段必须是包含时区的 ISO-8601；Clock 产生的时间统一规范为 UTC `+00:00` 与六位微秒。Trigger/CHECK 必须拒绝非法组合、identity 字段改变、Receipt UPDATE/DELETE、碰撞 INSERT 与 `REPLACE`；
- **immutable intent**：01B-3 只允许固定 destination `core:runtime_events`。Intent 的 `created_at` 精确复用 source Event 的 `recorded_at`，从而使迁移与重试确定；它绑定 `scope_id + source_event_id + event_digest + destination + delivery_key + created_at + policy version/digest + intent_digest`，不复制第二份 `event_json`。Publisher 必须从 append-only Journal join、完整解码并复核 Event 后才可发送。destination、URL、Topic、payload 和 delivery key 都不能由 LLM、Event payload 或业务调用方自由提供；
- **three keys**：`RuntimeEvent.idempotency_key` 负责 state/event mutation 幂等；`delivery_key=obx-v1-<sha256(destination + NUL + event_id)>` 在所有发布重试中保持不变，供 Sink/Consumer 去重；一次所有权由 `(delivery_key, claim_generation, claim_token, publisher_id)` 标识。`receipt_id=rcp-v1-<sha256(sink_id + NUL + ack_id)>` 由有效 Sink ACK 确定性派生，保证 ACK 重试不会生成另一身份。上述 key 不得混用或由模型生成；
- **canonical digests**：沿用 01B-2 的 UTF-8、`ensure_ascii=False`、key 排序、无空白 JSON canonicalization。`policy_digest` 的 preimage 固定为 `{schema:'outbox-policy/v1', policy_version, destination, expected_sink_id, claim_ttl_ms, batch_limit, retry_delays_ms}`；`intent_digest` 固定绑定 `{schema:'outbox-intent/v1', scope_id, source_event_id, event_digest, destination, delivery_key, created_at, policy_version, policy_digest}`；`ack_digest` 固定绑定 `{schema:'outbox-publish-ack/v1', receipt_id, delivery_key, destination, source_event_id, event_digest, claim_generation, claim_token, publisher_id, sink_id, ack_id, acked_at}`。三者均为 canonical bytes 的 lowercase SHA-256，不允许调用方传入未复算摘要；
- **v2 cutover**：保留已发布 v1/v2 migration 名称、checksum 与 DDL。fresh DB 原子得到 v3；真实 v2→v3 必须先校验现有 Thread/Event 数据，再使用本次数据库初始化显式绑定的同一 Policy snapshot，为历史 Event 原子建立 `LEGACY_SUPPRESSED` Outbox，固定原因 `pre_outbox_cutover`。该状态终态、不可 claim、不得伪造 PublishAck，也不得因升级突然重发历史事件；历史 exact retry 仍为零写入；
- **state machine**：`LEGACY_SUPPRESSED` 为迁移终态；新 intent 从 `PENDING(generation=attempt=0)` 开始。每次成功 claim（包括 NACK 后再 claim 和过期 claim 直接重领）都在一个短事务中令 generation 与 attempt 各 `+1`，生成新 token，设置 `updated_at=claim_time`、`claim_expires_at=claim_time+claim_ttl` 并进入 `CLAIMED`；claim commit 即定义一次 publication attempt 开始。当前且未过期 owner 的 NACK 才可 CAS 回 `PENDING`，保留 generation/attempt，清空 claim 字段，并按冻结策略设置 error/available/updated；过期 owner 不能 ACK/NACK。合法 ACK 在独立事务中插入 Receipt、清空 claim 字段并 CAS 为 `PUBLISHED`。不存在 `SENT`；本片不实现 DLQ、人工 redrive 或永久投递活性保证；
- **claim/fencing**：Clock、claim token factory 与版本化 Outbox policy 由 Composition Root 注入，不能由 Agent、Event 或 Transport 指定。claim 必须在短 UoW 内 CAS 并先 commit；`now >= claim_expires_at` 即旧 owner 失权，即使尚未重领也不能 ACK/NACK。迟到或错误 generation/token/publisher 的 ACK/NACK 确定性拒绝；这只是单条 Outbox 的本地发布所有权，不实现 01C 的 Invocation queue、heartbeat、Watchdog、Finalizer 或 Reaper；
- **policy/API**：公开不可变 `OutboxPolicy(policy_version, destination, expected_sink_id, claim_ttl_ms, batch_limit, retry_delays_ms)`，`policy_digest` 只能按 frozen preimage 内部计算。Composition Root 必须通过 `SQLiteRuntimeDatabase(config, outbox_policy=policy)` 为一个数据库显式绑定唯一 v1 Policy；缺失时抛 `RuntimeOutboxConfigurationError` 并拒绝 initialize，Thread/Event/Outbox Store 从该数据库读取同一绑定，调用方不能逐次覆盖。Policy 字段要求固定 destination/expected sink、正整数 TTL/batch limit 与非空非负整数元组 retry delays；第 `n` 次 attempt 失败时，`available_at=failure_time+retry_delays_ms[min(n-1,last_index)]`，01B-3 不加 jitter。缺失或与持久 snapshot 漂移时 fail-closed，不提供隐藏默认值；Policy 版本升级/多版本 registry 另开迁移，不在 v1 静默替换。最小 lifecycle API 为按 delivery key 读取、按单一 scope claim eligible batch、当前 claim NACK、当前 claim acknowledge，以及 `OutboxPublisher.publish_once(scope_id)`；Publisher/lifecycle API 不能注入任意 Clock、token、destination、sink 或 UoW。该限制不改变现有 `ThreadEventStore.apply(uow, mutation)` 的显式事务边界；
- **transaction split**：事务 A 提交 Thread+Event+Outbox；事务 B 提交 claim；关闭 B 后才可调用 Transport；事务 C 校验 ACK、追加 immutable receipt 并 CAS 为 `PUBLISHED`。任何网络、Broker、Consumer 或 Adapter 调用都不得发生在 SQLite 写事务内。Receipt 是发布证据，不再递归生成新 Event+Outbox，避免无限审计链；
- **ACK meaning**：`OutboxPublishAck` 必须绑定 destination、delivery key、event ID/digest、当前 claim generation/token、预期 sink identity 与 ack ID/digest。它只表示 Runtime 配置的 Sink 已持久且幂等接纳该 envelope；不等于用户已经看到 Message、不等于工具副作用成功、不等于 `DeliveryAck` 或 `AcceptanceRecord`。不存在任意调用者可直接 `mark_published` 的公共入口；
- **receipt projection**：ACK commit 后，Outbox 必须满足 `receipt_id == Receipt.receipt_id`、`published_at == updated_at == Receipt.acked_at`、`claim_generation == Receipt.claim_generation`，且双方的 delivery key、destination、source event ID/digest 完全相同；Receipt 的 publisher/token 必须等于被消费的当前 claim，sink 必须等于 policy expected sink。正反向 integrity scan 和 corruption 红测必须逐项复核，而不能只依赖 `(receipt_id, delivery_key)` FK；
- **failure/ACK retry**：Transport 抛错、无 ACK 或 ACK 无效且 owner 仍有效时，Publisher 尝试以稳定 error code NACK；NACK 成功则按策略回 PENDING，NACK 失败或 owner 已过期则保持 CLAIMED 等待 ACK 重试或到期重领。Sink 已返回有效 ACK 但本地 ACK 事务 busy/失败时不得 NACK，保持 CLAIMED 并优先重试同一 ACK；到期后仍可能用同 delivery key 重投。完全相同且已提交的 Receipt/ACK 重试返回 `ALREADY_ACKNOWLEDGED` 零写入；同 key 的不同 ACK、不同 claim 或不同 digest 一律 typed conflict；
- **delivery semantics**：本片只承诺 durable intent 与 at-least-once publication attempts。Sink 已接纳但 ACK 丢失、本地 ACK 事务失败或 Publisher 崩溃时，系统必须用相同 canonical Event bytes 与 delivery key 重投。只有 Consumer 将 delivery key Inbox 去重与自身业务效果放在同一事务中时，才能获得 effectively-once acceptance；仍不得称网络或端到端 exactly-once；
- **ordering**：同一 `(scope_id, aggregate_type, aggregate_id)` 只允许 claim 最早且尚未 `PUBLISHED/LEGACY_SUPPRESSED` 的 sequence；前一条 pending/claimed 会 fail-closed 阻塞后一条。不同 aggregate 不提供全局顺序保证，可并发发布；跨 Scope claim 必须隔离；
- **authority**：Thread/Event Repository 自动 enqueue；Publisher 只能改变 Outbox lifecycle 和写 Receipt，不能修改 Thread、Event、Acceptance 或其他业务真相。Transport/Sink 只接收冻结的 Event envelope、delivery key 与 attempt metadata，不获得数据库连接或 UoW；若 Consumer 要触发业务变化，必须提交新的、独立鉴权且幂等的 Command；
- **retry/integrity**：state/event exact retry 必须同时验证 Outbox intent、当前 lifecycle 与 receipt；无论 intent 是 claimed、published 或 legacy-suppressed，都不能重置为 pending。缺失、重复、错 Scope/Event/digest/destination、receipt 错绑或非法状态必须 typed fail-closed，Transport 调用数为 0；Outbox/ACK 冲突不得泄漏裸 `sqlite3` 错误；
- **SQL authority boundary**：公共 UoW 对两张 Outbox 表的任意 DML 均由 authorizer 拒绝；Store 的私有 managed-operation 只可执行上述 CAS。直接 SQLite 连接也必须被 schema trigger 拒绝 identity/Receipt 改写、DELETE/碰撞/REPLACE 与非法生命周期组合；但持有数据库文件写权限的进程仍在当前 `local_trusted_execution/v1` 信任域内，本片不冒充数据库级 RBAC，也不声称能阻止其伪造一个形状合法的 lifecycle 迁移。生产身份与 DB 文件权限隔离留 PROD-03/07；
- **required red/green gates**：fresh v3、真实 v2→v3（含新增 Event 父键索引）、历史 suppress、迁移故障回滚与 schema drift；state/event/outbox 三个写窗和 commit 前/后进程退出；exact retry 不重排；公共 UoW 全 DML 拒绝，以及 raw identity/Receipt/非法状态 UPDATE、DELETE、REPLACE/hidden-rowid 绕过；claim 在 Transport 前提交且不持有 writer lock；并发 claim、NACK 后 generation 增长、expiry/reclaim、stale ACK/NACK、exact ACK retry、ACK-vs-reclaim 竞态；Transport 异常/无效 ACK、Sink 接收后 ACK 丢失、claim/ACK SQLite lock、重启恢复、同 aggregate 顺序与跨 Scope 隔离；intent/policy/ack digest 与正反向 FK 腐败；durable Consumer fixture 同 key 两次只产生一次效果；Runtime 子集、全量、compileall 与 diff check；
- **nonclaims**：本切片不实现真实 Broker/网络/Egress、Consumer Inbox 产品能力、多订阅者/consumer group、Broker retention/认证/TLS、DLQ/redrive、最终用户 Message DeliveryAck、外部工具/不可逆副作用、producer principal RBAC、Invocation lease/fencing/Reaper、Budget、Acceptance writer、Detector/Incident/Replay、其他 aggregate Repository、完整 Journal 查询、PostgreSQL 或完整 PROD-01B；
- **INC / Harness Evolution**：本片风险目录先冻结为 `event_without_outbox`、`outbox_without_event`、`unexpected_legacy_replay`、`publish_inside_business_transaction`、`stale_publish_ack`、`duplicate_publish_attempt`、`aggregate_publish_reorder` 与 `outbox_corruption`。冻结时 Detector/Incident/Replay 为 0，3A 轻量轨为 `FROZEN/INCONCLUSIVE`；完成后，3A 以最终 22/73/159/372（4 skip）门禁和 10 组真实产品缺陷闭环为证据，独立 Review 给出 `APPROVE`（advisory），项目轻量轨据此记录为 `COMPLETED/KEEP`。完整 01B-3 因 3B 尚未实现仍为 `IN_PROGRESS/INCONCLUSIVE`；Detector/Incident/Replay 仍为 0，`INC-01` 保持待开始。真实模型、Evolver、Validation/Held-out、query budget、样本量和统计效果均为 `N/A`。

以下仅保留演进摘要；01B-1/2/3 的命令、计数、哈希、故障矩阵、真实缺陷、修复与回归位置、Review 和决策以 [`VerificationReports/PROD-01B.md`](../VerificationReports/PROD-01B.md) 为权威记录。历史首轮结构型 EXPECTED_RED 已完成（随后由显式 Policy 红卡扩展）：新增 `demo/tests/test_runtime_outbox.py`，只覆盖 fresh v3/两张 Outbox 表/Event 复合父键索引，以及现有 `ThreadEventStore.apply()` 的 Thread+Event+Outbox 三写 commit/rollback 结构。精确运行 `python3 -m unittest tests.test_runtime_outbox -v` 共发现并执行 3 项，结果为 1 failure + 2 errors：schema 常量仍为 v2、commit 后与同一 UoW 内均不存在 `runtime_outbox`；这是预期能力缺失，不是产品回归或 Incident。旧 01B-1/01B-2 的 68 项对照继续全绿，测试语法、`git diff --check` 均通过；该历史版本 `test_runtime_outbox.py` SHA-256 为 `6d8684486ced5a96c84275d6e0183f292bba7bae885ce5899bb325846e095826`，两份 01B-2 生产实现 hash 未变化。独立 Review 最终为 `APPROVE`，只批准这张历史结构型红卡，不代表完整 01B-3。

首轮 Review 还发现并修正了测试自身的盲点：最初版本从数据库行读取 Policy 再自证 digest、commit 只数 Outbox、rollback 未证明事务内曾写入 Outbox；修订后明确要求 commit 后 `(Thread, Event, Outbox)=(1,1,1)`、rollback 前同一 UoW 内为 `(1,1,1)`、退出后为 `(0,0,0)`。第二轮 Review 又发现仅删除 Policy 自证仍会让无参数构造暗示隐藏默认值，因此继续冻结上述 `OutboxPolicy`/database binding，并新增 Policy digest 独立复算与缺 Policy typed fail-closed 红测。该历史红卡 `test_runtime_outbox.py` SHA-256=`8452ba5f2add07c3cd30e75b5c3ce26ceb941984d58f15e2ab5d20f5e3ab948a`；当时精确执行 5 项、5 failures，首个线性缺口为公开 `OutboxPolicy` 尚不存在。旧 68 项继续全绿，py_compile/diff-check 与两份生产 hash 不变；这些都是历史 EXPECTED_RED，不是最终状态。

`01B-3A` 最终收口：显式 Policy、Schema v3、真实 v2→v3 cutover、历史 Event suppress 与 Thread+Event+Outbox intent 原子三写已实现。独立挑战在首绿之后实际发现并关闭 10 组产品缺陷；完整哈希、故障/并发矩阵、缺陷与 ReviewArtifact 见 [`VerificationReports/PROD-01B.md`](../VerificationReports/PROD-01B.md)。其后 `01B-3B-1` 已按 7 项 EXPECTED_RED → 首绿 → 独立攻击 → 修复 → 跨进程与 `os._exit` 恢复门禁收口；下一动作固定为 `01B-3B-2` Transport publish/ACK/Receipt。

##### PROD-01B-3B-1：Outbox claim / NACK / expiry-reclaim

状态：**`INV-PROD-01B-3B-1-CLAIM-LIFECYCLE-v1` 已完成；证据为 `FRESH_VERIFICATION`，决定为 `KEEP (3B-1 only)`。** 本片只建立本地 SQLite Outbox 的短事务所有权状态机，不接后台循环、旧 Executor、Web、Transport、Broker 或外部副作用；因此只能称为“本地 claim/NACK lifecycle”，完整 3B 与可靠发布仍为 `IN_PROGRESS/INCONCLUSIVE`。实际运行、哈希、四组真实产品缺陷与独立 Review 只记录在 [`VerificationReports/PROD-01B.md`](../VerificationReports/PROD-01B.md) 4.8，不在 Plan 复制平行证据。

- **公开类型与 API**：`OutboxState` 精确枚举 `LEGACY_SUPPRESSED/PENDING/CLAIMED/PUBLISHED`；公开 `OutboxNackErrorCode`、下列不可变 DTO 与 `SQLiteOutboxLifecycleStore`。`OutboxClaimOwnership` 固定为 `(scope_id, delivery_key, claim_generation, claim_token, publisher_id)`；`OutboxClaim` 固定为 `(ownership, source_event_id, destination, event_digest, attempt_count, claimed_at, claim_expires_at, policy_version, policy_digest)`；`OutboxNackResult` 固定为 `(scope_id, delivery_key, claim_generation, attempt_count, error_code, failed_at, available_at)`。`OutboxSnapshot` 是除 `claim_token` 外 runtime_outbox 全部公开列的只读投影：`scope_id/delivery_key/source_event_id/destination/event_digest/intent_digest/policy_version/policy_digest/state/claim_generation/attempt_count/available_at/publisher_id/claim_expires_at/last_error_code/suppress_reason/created_at/updated_at/published_at/receipt_id`；不存在的 key 与 scope 不匹配均返回 `None`，不得泄漏其他 Scope 是否存在。Store 构造固定为 `SQLiteOutboxLifecycleStore(database, *, publisher_id, clock, claim_token_factory)`；最小方法仅为 `get(scope_id, delivery_key) -> OutboxSnapshot | None`、`claim_eligible_batch(scope_id) -> tuple[OutboxClaim, ...]`、`nack(ownership, error_code) -> OutboxNackResult`。单次方法不得传入 UoW、Clock、token、publisher、destination、sink、limit 或 policy；没有 `release/requeue/renew/reclaim/mark_published` 公共捷径。只有成功 claim 返回当前 ownership token；所有 DTO 都是可能立即过期的快照，不是第二真相源或持久授权。
- **依赖契约**：`OutboxClock.now() -> datetime` 与 `OutboxClaimTokenFactory.new_token() -> str` 只由 Composition Root 注入。Clock 必须返回 aware datetime；claim/NACK 在取得内部事务后各只采样一次，整个选择、边界判断、CAS 与写入复用该 instant，并将新 lifecycle 时间规范化为 UTC `YYYY-MM-DDTHH:MM:SS.ffffff+00:00`。Event 派生的 `created_at` 以及 LEGACY/初始 PENDING 的 `updated_at/available_at` 必须保留 `RuntimeEvent.recorded_at` 原字符串，只要求是合法 aware ISO，不要求 UTC；claim/NACK 生成的 `updated_at/available_at/claim_expires_at` 才必须是上述 canonical UTC。3B-1 部署边界是单宿主 SQLite 与共享可信 wall clock；若 claim/NACK 的采样时间早于目标行上一次由 lifecycle Clock 写入的 `updated_at`，抛 `RuntimeOutboxClockError` 且整批零写入，generation=0 的 Event 派生时间不被误判为 Clock rollback。publisher ID 是 Composition Root 为本进程实例生成并在 Store 生命周期内固定的非空 UTF-8 标识，不是可跨重启复用的授权 principal；长度上限 256。claim token 精确为 `obc-v1-<64 lowercase hex>`，生产 factory 必须用至少 256-bit CSPRNG；同一 batch 不得重复，直接重领时不得复用该行旧 token。naive/非法 Clock、非法 token、TTL 或 retry 时间加法溢出必须在任何写入前抛 typed error，不能泄漏 `OverflowError`、`UnicodeError` 或裸 SQLite 错误。
- **Policy 可执行域**：已发布的 `outbox-policy/v1` wire/持久读取有效域保持不变；3B-1 仅为 `SQLiteOutboxLifecycleStore` 激活冻结执行档位：`1 <= claim_ttl_ms <= 86_400_000`、`1 <= batch_limit <= 1_000`、`1 <= len(retry_delays_ms) <= 64` 且每个 delay `0..604_800_000`。这不修改 v3 DDL/checksum，也不让 3A 已合法创建的更大 int64 Policy DB 失效：其 initialize、3A read/UoW 与 Thread+Event+Outbox 三写仍须兼容，但构造或调用 lifecycle Store 必须以 `RuntimeOutboxValidationError` 零写入拒绝，直至未来有版本化 Policy migration。Clock 接近 datetime 上界时即使数值在上述范围内仍须做 checked arithmetic。更大容量或时长需要新 Policy 版本和容量证据，不能靠 SQLite int64 上限暗示 3B-1 支持。
- **NACK code**：只接受 enum `TRANSPORT_ERROR='outbox:transport_error'`、`ACK_MISSING='outbox:ack_missing'`、`ACK_INVALID='outbox:ack_invalid'`；不接受任意字符串、异常正文、Prompt 或模型文本。NACK commit 后响应丢失再重试时，由于 token 已清空，返回 typed ownership-lost，不伪装幂等成功。
- **错误边界**：新增 `RuntimeOutboxLifecycleError` 及 typed `RuntimeOutboxValidationError`、`RuntimeOutboxOwnershipLostError`、`RuntimeOutboxClockError`、`RuntimeOutboxTokenFactoryError`、`RuntimeOutboxAttemptExhaustedError`。generation/attempt 已达 SQLite int64 最大值时不得自增，必须以 attempt-exhausted 零写入拒绝。持久行/digest/投影漂移继续使用现有 `RuntimeStoredDataCorruptionError`，busy/commit/rollback 继续使用现有 persistence errors。普通并发 claim loser在获得锁后重新读取；若没有其他 eligible 行则返回空 tuple、零写入，不抛 ownership conflict。事务内已选行 CAS=0 不是普通 loser，必须 typed fail-closed 并整批 rollback。
- **claim 语义**：一个 batch 在单一内部 `RuntimeUnitOfWork` 中 all-or-none。初始或 NACK 后 `PENDING` 仅在 `now >= available_at` 时 eligible；`CLAIMED` 在 `now >= claim_expires_at` 时可直接重领。每次成功 claim 都令 `claim_generation` 与 `attempt_count` 各 `+1`，生成新 token，写入构造时绑定的 publisher，令 `updated_at=claim_time`、`claim_expires_at=claim_time+policy.claim_ttl`，清空 available/error 并提交后才返回。返回时事务必须关闭且不持有 writer lock；claim commit 只表示一次本地 publication attempt ownership 开始，不表示已调用 Transport。
- **fencing / NACK**：当前 owner 的唯一身份是 `(scope_id, delivery_key, claim_generation, claim_token, publisher_id)`；NACK 必须先在同一事务内完成 Event、identity、Policy 和当前 CLAIMED lifecycle 的完整 decoder 校验，再 CAS 完整 tuple，且仅在 `now < claim_expires_at` 时有效。`now == claim_expires_at` 即旧 owner 失权，即使尚未重领也必须拒绝。成功 NACK 保留 generation/attempt，清空 claim 字段，设置稳定 error code、`updated_at=failure_time`，并令 `available_at=failure_time + retry_delays_ms[min(attempt_count-1,last_index)]`。错误 scope/key/generation/token/publisher、重复 NACK、过期 owner、重领后的旧 owner或任何持久腐败均 typed 拒绝且零写入；NACK 不得把腐败行覆盖成貌似合法的 PENDING。
- **eligibility / ordering**：所有时间必须先解析为 aware datetime 再按 instant 比较，禁止用 TEXT 字典序。每个 `(scope_id, aggregate_type, aggregate_id)` 只允许最早的非终态 sequence 参与领取；更早的 PENDING 即使尚未 available，或更早的未过期 CLAIMED，都会阻塞后序。经 3B-1 decoder 验证的 `LEGACY_SUPPRESSED` 是终态且不阻塞；`PUBLISHED` 不可领取，但在 3B-2 的 Receipt decoder 上线前也不能被 3B-1 当作已验证终态跳过——若它是某个候选 aggregate 的 predecessor，本次 batch 必须 typed fail-closed、零写入。跨 aggregate 候选按 eligible instant（PENDING=`available_at`，expired CLAIMED=`claim_expires_at`）、created-at instant、delivery key 排序，再截取 database-bound `policy.batch_limit`；这只定义可复现领取顺序，不承诺跨 aggregate 发布顺序或长期公平性。跨 Scope 必须完全隔离。
- **integrity**：`get`、claim、NACK 与 `verify_integrity()` 必须复用单一共享 decoder，重建并复核对应 RuntimeEvent、Outbox identity/digest/policy，以及 3B-1 所拥有的 `LEGACY_SUPPRESSED`、初始/NACK 后 PENDING、CLAIMED lifecycle；任何写操作都必须在同一读写事务中先完成相关 decoder 校验。generation/attempt、token/publisher、错误码、aware/Event 派生时间、canonical UTC lifecycle 时间、TTL/retry 投影或早期同 aggregate Outbox任一腐败都必须在写前 typed fail-closed。请求 Scope 内任一非终态 Outbox 或 predecessor 腐败会阻断本次整个 Scope batch；其他 Scope 的腐败不得被读取或过度阻塞。v3 CHECK-compatible 但语义非法的 raw lifecycle 更新属于 `local_trusted_execution/v1` 信任域内持久腐败，由 decoder fail-closed；3B-1 不为此原地修改 v3。PUBLISHED/Receipt 的深度校验仍属 3B-2；3B-1 不创建 Receipt 或 PUBLISHED 数据。
- **故障与并发**：新增仅位于可回滚事务内的 `OUTBOX_AFTER_CLAIM_UPDATE`、`OUTBOX_AFTER_NACK_UPDATE` fault point；不得新增可抛的 after-commit hook。CAS 后故障、commit 前故障或进程退出必须重开为操作前状态；commit 后退出必须重开为完整 CLAIMED/NACK-PENDING。同一旧 ownership 最多只能被一次 NACK CAS 直接消费；允许 `retry_delay=0` 时合法线性化为“NACK 成功，随后新一代 claim 也成功”。同行线程和跨进程的 claim、过期重领及 NACK/reclaim 竞态必须能串行解释、generation 单调且无丢失更新；SQLite busy 必须在既有 deadline 内 typed 失败，释放后可重试。
- **兼容与 schema stop**：`RUNTIME_DB_SCHEMA_VERSION=3`、已发布 v1/v2/v3 migration 名称、DDL 与 checksum 必须逐字节不变。fresh v3、真实 v2→v3 后的 generation=0 PENDING 均可使用同一 lifecycle 实现；`LEGACY_SUPPRESSED` 永不 eligible。当前没有 `(scope,state,claim_expires_at,...)` 索引，3B-1 明确不声明容量、p95 或大表扫描性能；若验收需要新列、索引、trigger、attempt ledger 或第二持久真相源，立即停止并另行冻结 Schema v4，不得原地修改 v3。
- **首轮 EXPECTED_RED**：固定 7 项：公开 read/API；初次 PENDING→CLAIMED；当前 owner NACK 与 retry 后 generation+1；expiry/reclaim 与 stale fence；同 aggregate 顺序/跨 aggregate/Scope；并发单赢家；claim/NACK CAS 后故障回滚。红卡使用 `getattr` 保证全部用例被发现；实现前失败只记 `EXPECTED_RED`，已经 KEEP 的 3A 回归若转红必须记真实 regression。
- **首绿后独立攻击**：至少覆盖时间前/等于/后 1 微秒、offset timestamp、时钟回拨、TTL/retry 溢出、整批第 N 个 token/腐败回滚、blocked candidate 不耗尽其他 aggregate、跨 Scope 腐败隔离、多线程/跨进程重复、busy、`os._exit`、generation int64 边界、形状合法但语义腐败、state/event exact retry 不重置 lifecycle，以及公共 UoW 仍不可改 Outbox。只有首绿后被冻结 Oracle 击穿才登记 `PRODUCT_DEFECT`。
- **nonclaims / stop**：本片不实现 Transport、`publish_once`、ACK、Receipt、PUBLISHED 转换、Consumer Inbox、at-least-once/effectively-once/exactly-once、后台调度、heartbeat/renewal、DLQ、Watchdog/Reaper、Invocation AttemptLease/Fence、取消、资源清理、Detector/Incident、容量或永久活性。实现需要任一这些能力，或 Agent/Transport 能控制 Clock/token/publisher/claim authority 时，必须停止并重新划界。
- **Harness Evolution / acceptance**：冻结时确定性轻量轨为 `FROZEN/INCONCLUSIVE`；7 项首红签名、原卡首绿、首绿后攻击、跨进程与 `os._exit`、Runtime/full/compileall/diff-check、exact hash 和独立 Review 均已闭合，最终记录为 `COMPLETED/KEEP (3B-1 only)`。真实模型、Evolver、Held-out 与统计效果均为 `N/A`；Review 仍不签发 Runtime Acceptance，完整 01B-3 继续 `IN_PROGRESS/INCONCLUSIVE`。

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

当前仍处于 **PROD-01B：状态 Store、Journal 与 Outbox**；`PROD-01B-1`、`PROD-01B-2`、`PROD-01B-3A` 与 `PROD-01B-3B-1` 已完成，完整 `PROD-01B-3` 仍进行中，下一动作是 `01B-3B-2` Transport publish/ACK/Receipt。

## 待验证事项

- 状态表、Journal、Outbox 和预算账本能否在 SQLite 单事务内保持原子、一致且可恢复。
- 现有 Coding/Scenario 执行接入 Thread 后，能否保持回归通过且不把 build/test 规则泄漏到普通交互。
- 真实多模型、长会话和多 Agent 并发是否能达到冻结的可靠性、成本与延迟目标；外部调用需届时重新授权。

## 待办事项

- PROD-01B-3A durable intent 原子三写与 01B-3B-1 本地 claim/NACK lifecycle 已完成；下一步冻结 01B-3B-2 红卡，再推进 Transport publish/ACK/Receipt。之后逐片完成 BudgetLedger、权威关系/Acceptance 和查询恢复协议，已完成切片不重复开发。
- 随后实施 PROD-01C 的 durable Invocation、Finalizer/Reaper、fencing、取消与恢复。
- 按 PROD/INC 双轨补齐对应事故事件、负向用例、正常对照和覆盖指标。
- 对每个适用的行为修改同步填写 Harness Evolution 实验模板；PROD-01B 使用确定性轻量轨，只填写 Baseline、单一变更、故障矩阵、正常对照、固定门禁、回归和决策，Evolver/principal、样本量、Validation/Held-out cohort、query budget、统计效果等字段统一标为 `N/A`，不得因此阻塞基础设施开发，也不得把该证据外推为模型智能或 Held-out 效果结论。
