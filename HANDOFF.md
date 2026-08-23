# 交互式多模态 Multi-Agent Runtime 项目交接

## 使用方式

新任务先阅读本文，再检查 `git status`、最近提交和本文件列出的关键代码。代码、测试和 Git 是事实来源；本文用于恢复当前方向，不替代代码检查。

推荐的新任务开场指令：

```text
请读取 /Users/donbblu/codex/multiAgent/HANDOFF.md，
再检查 git status、最近一次提交和其中列出的关键文件。
以代码和测试为事实来源，不要重新读取旧聊天。
从 HANDOFF.md 的“下一步”继续推进。
规划、实现或收口任何 PROD-* 批次时，严格执行“PROD / INC 双轨联动规则”，
同步写明并更新对应的 INC-* 事故学习增量，不得只推进功能主线。
```

## 项目目标与定位

项目核心是一个可交互、可长期运行、单机优先并可演进到分布式的 **多模态 Multi-Agent Runtime**。用户在持久 Thread 中持续发送文本、图片、音频和视频；多个独立 Agent 可以并行判断、按依赖交接、使用受控工具并接受人工介入。Harness 是项目本体；Agent、模型、Prompt、工具和场景插件都是可替换负载。目标架构把 Coding 封装为插件式专业能力，不再用代码 Bug 修复代表整个产品。当前代码尚无 `CodingPlugin`，Coding 仍是 Composition Root 和包内纵向切片。

项目不再定位为单纯的多 Agent 实验台，也不以接入模型数量或 Agent 数量作为成果。目标系统包含三层：

1. **Harness Runtime**：Thread、Message、Invocation、Session、Artifact、上下文装配、模型与 Worker 路由、工具执行、权限隔离、预算、恢复、审计和场景化收敛。
2. **交互式 Agent Workspace**：参考 Cat Café 的 Agent 泳道、Mailbox、结构化 Handoff、跨模型 Review 和用户介入，为长期协作提供真实入口。
3. **插件与验证运营系统**：Coding、VisionForge 及后续专业能力按插件接入；持续交互、协作、多模态和插件/工具任务分层评测，Coding 是首个代表，并通过故障注入、压力测试和事故复盘证明 Runtime 可靠性。

推荐的第一阶段生产边界是：自托管、单组织/单信任域、多个 Thread 和并发 Invocation、单机多进程或单集群部署。状态必须持久化，每个 Invocation 必须绑定输入快照、明确权限和预算；只有需要资源副作用的场景才绑定隔离 Workspace。敌对多租户、多区域高可用和大规模远程 Worker 属于后续演进目标，不应在没有容量或隔离证据时提前承诺。

VisionForge 的“参考图 → Vue 页面 → 浏览器功能验收 → VLM 视觉审查 → 自动修复”完整保留为 `visionforge:web_visual` 场景，不再代表整个产品。当前它是直接注册到 Core Registry 的独立 Scenario Plugin，并复用现有 Coding 能力；Plugin SPI 尚不支持插件嵌套。目标归属待 Coding 插件化时再按显式依赖协议迁移。

### Cat Café 的吸收方式

- 吸收 `Thread / Invocation / Session` 分离、持久 Invocation Queue、父子调用链、Session 隔离、统一事件、人工暂停点、独立 Review 和事故知识晋升。
- 不把聊天记录、自由 `@mention` 或长期模型 Session 作为任务状态和事实的真相源。
- Discovery 阶段允许不同模型先独立判断、再对等质疑；Delivery 阶段继续使用强类型 DAG、Artifact、Validator 和 Fix 闭环。
- Agent 只能提出 `HandoffProposal` 或图变更建议；Harness 校验目标、权限、链深、预算、循环和资源冲突后，才能创建 `RouteEdge`、Invocation 或 Task。
- “无 Boss Agent”只表示内容判断可以对等，不表示没有中央控制面。Harness 始终独占状态权、副作用权和最终完成判定。

## 当前已实现的 Coding 纵向切片（目标迁移为 Plugin）

下面是现有实现事实，不是未来产品默认入口。通用目标主链是 `Thread → Message/Artifact → AgentSession/Invocation → Message/Artifact/Handoff → 用户介入或协作收敛 → 场景 AcceptancePolicy`；普通交互不必创建 Patch、测试或 Fixer。

```text
用户需求
  → TaskContext 标准化目标、验收条件和权限
  → StructuredTaskPlanner 生成 TaskSpec
  → TaskGraph 校验依赖、Artifact 和资源冲突
  → TaskGraphExecutor 从 ready queue 并发调度 Worker
  → Worker 生成 ImplementationPlan Artifact
  → PatchIntegrator 检查路径、权限和跨 Artifact 冲突
  → ProjectWorkspace 受控应用并在批次失败时补偿回滚
  → Tester 运行白名单验证命令
  → 通过：整合已验证长期记忆并 completed
  → 失败：记录证据并创建局部 FixTask
  → Fixer 生成修复 Artifact，安全合并并运行受影响测试
  → 最终完整质量门禁通过后整合长期记忆并 completed
```

CLI 和 Web 只使用 DAG Runtime，不再提供旧式顺序执行或引擎回退选项。

## 已完成

- 完成 `Plan/Plan26.md` 的产品中心纠偏与 Runtime Charter：交互式多模态 Multi-Agent Runtime 是产品本体，Coding 被确定为待插件化的专业纵向切片，VisionForge 保持独立 Scenario Plugin；本次只冻结文档边界，尚未实现持久 Thread、Agent 会话或 CodingPlugin。
- 完成 `PROD-01A` 通用领域协议与迁移骨架：新增独立 `runtime_domain`，冻结 Scope/Thread/Turn/Message、Agent Role/Profile/Instance/Session、Invocation/Attempt、Outcome/Acceptance 和 RuntimeEvent；Coding 只通过单向兼容适配器映射，尚未接入 Store、队列、旧 Executor 或 Web。
- 建立确定性的 TaskState 与 LifecycleState 双层状态机。
- 建立 `TaskSpec`、`TaskGraph`、循环依赖和 Artifact 关系校验。
- 根据依赖、输入 Artifact 和资源冲突选择可并发子任务。
- `TaskGraphExecutor` 通过 `WorkerRegistry` 并发执行任务。
- 子任务失败只重试自身，依赖失败任务的节点进入 blocked。
- Worker 不直接写共享项目，只提交 Artifact。
- `PatchIntegrator` 是共享项目的唯一写入入口，支持权限、路径和文件冲突检查。
- Workspace 对单文件使用临时文件和原子替换，并在批量失败时执行补偿回滚；这不等同于跨文件事务性原子提交。
- 建立感知、Working、长期和实体四类统一 MemoryRecord。
- 支持 Harness 主动触发和 Agent 被动检索，并按 Task、Role 和上下文预算过滤。
- 使用 SQLite 保存记忆与 TaskWorkingMemory Checkpoint。
- 只有真实测试通过且拥有 Artifact 证据的节点结果才会晋升长期记忆。
- CLI/Web 已接入真实 DAG 执行路径，并移除旧式顺序执行分支。
- Web 展示任务图、状态、Artifact 和验证事件，不展示模型原始推理。
- 动态创建局部 FixTask，修复 Artifact 通过安全合并、受影响验证和最终完整质量门禁。
- 持久化并恢复完整 TaskGraphRuntime、尝试次数、生命周期、Artifact 和 Workspace 哈希。
- 建立文件、符号、测试和 Artifact 实体索引，并支持中文文本与实体精确检索。
- 长期记忆支持幂等去重、失效、过期和 `supersedes` 版本替代，持久化前扫描并脱敏常见秘密。
- 建立 VisionForge 1.0 UI Spec 与 Visual Review 协议，包含受控交互、P1/P2/P3 问题和 Runtime 视觉通过判定。
- ModelClient 支持 text、vision、tool_calling、structured_output 能力声明，以及多模态结构化请求和 Token/耗时响应元数据。
- 图片按 SHA-256 内容寻址存储；Artifact 与 SQLite 只保存 PNG/JPEG 引用、尺寸、MIME 和哈希。
- 建立固定 Vue 3 + Vite 模板、保护路径、锁文件和稳定 DOM Hook，并通过真实生产构建。
- 建立受控命令运行器和 Vue 开发服务器生命周期，支持白名单、readiness、超时、取消与进程组清理。
- Playwright Browser Tester 使用固定 viewport 和受控交互，阻止外部网络请求并记录 DOM 断言、控制台、页面错误和实际截图 Artifact。
- Requirement Analyst、Developer 和 Visual Reviewer 已通过供应商无关 ModelClient 接入，并在调用前检查所需模型能力。
- `WebVisualScenario` 已将 UI Analyst、Web Developer、Patch Integrator、Browser Tester、Visual Reviewer 和 Quality Gate 接入通用 `ScenarioRuntime + TaskGraphExecutor`；参考图作为外部 Artifact，失败后按 `ConvergenceDecision` 执行最多两轮 Fix DAG。
- 新增 `ArtifactDraft` 和 Worker staging store；Agent、Browser Tester 与 Quality Gate 不再直接写共享 `ArtifactStore`，产物只由 Executor 接纳。
- `SQLiteScenarioRunStore` 保存场景状态、当前轮次、每轮 Runtime Snapshot、活跃 Artifact 和终态结果；支持 Graph 完成后恢复、Gate 与 Fix 轮次之间恢复、completed 幂等恢复及 Workspace 漂移拒绝。
- Runtime 质量门禁组合构建、DOM/交互、控制台、页面/网络错误、视觉分数和 P1/P2，模型不能自行宣告 completed。
- Fixer 根据结构化反馈生成局部 Patch，最多修复两轮；旧 Patch、失败证据和最终验证状态通过 ArtifactStore 追踪。
- 旧 Runner 的 SQLite 返工 Checkpoint 仍保留兼容测试；产品路径改由 `SQLiteScenarioRunStore + SQLiteRuntimeStore` 统一恢复。
- 后端仍保留受控 PNG/JPEG 内容寻址上传和 VisionForge 任务 API；任务只接收 asset ID 与需求，固定使用 Runtime 创建的 Vue 项目目录。
- 历史 VisionForge 首页曾展示参考图、实际截图、评分、修复轮次和 Artifact 调用链；当前默认首页已替换为 Coding 工作台，不再提供该展示，但对应后端 API 与回归仍保留。
- 建立 3 个版本化固定页面任务、Runtime 拥有的 DOM/交互断言和受控 HTML→PNG 参考图渲染器。
- 建立三方案统一评测协议与 JSON 报告，记录构建、功能、视觉、首次通过、自动修复、轮数、Token、耗时和人工介入。
- 建立 DeepSeek 文本模型与 DashScope Qwen 视觉模型的独立配置和按角色路由；客户端适配供应商级结构化输出模式与请求选项。
- 已用 `deepseek-v4-pro` 和 `qwen3.7-plus` 各完成一次经授权的最小真实能力烟测，图片输入、JSON 解析及 Token/耗时元数据均验证通过。
- 当前共有 213 个测试通过；其中 4 个真实浏览器类默认跳过，需要显式开启。
- 建立 Core `Claim`、三态 `VerificationOutcome` 和不可变 `VerificationRecord`；模型观察、推断和建议不会自动成为已验证事实，验证记录随 SQLite Runtime Snapshot 恢复。
- Worker Artifact metadata 会拒绝伪造的验证字段，正文中的同名业务数据不会改变外层状态；TaskGraph 节点全部成功不再自动验证产物、晋升长期记忆或宣布 completed，`GraphExecutionResult.acceptance_outcome` 默认是 unknown。
- VisionForge 质量门禁不再循环自证：build/browser/review 验证 quality gate，quality gate 再验证其他周期产物；场景流程和视觉规则未改变。
- 建立通用 `RequirementEvidence`、`CodingRequirement`、`RepositoryScope`、`AcceptanceCriterion`、`EvidenceGrant` 和冻结的 `ValidatorProfile`；UI Spec 继续只属于 VisionForge。
- 结构化需求启用后，Executor 会在调用 Worker 前核对 Evidence Artifact、仓库范围、Profile/Criterion 摘要和 Task/Role 授权；缺失授权不会回退为宽松模式。
- 建立 Runtime `ValidatorRegistry + ValidatorProfileRunner`，缺失能力或执行异常产生 unknown 和报告 Artifact；组合 profile gate 独占最终 Artifact 验证更新。
- VerificationRecord 绑定 Artifact 内容哈希和可选 Workspace 摘要；`required_verified_inputs` 会拒绝过期证明，并随 SQLite Runtime Snapshot 恢复。
- `WorkerRegistry` 已支持同一 Role 多个 `WorkerDescriptor`，按 Role、能力、协议、策略、职责隔离、可用性和稳定 tie-break 选择；结构化选择审计随 SQLite Checkpoint 恢复。
- 无合格 Worker 会确定性进入 blocked，不跨 Role 或降低要求；Runtime producer provenance 用于阻止 Reviewer/Tester/Validator 对同一 principal 的产物自审或自证。
- VisionForge 已通过 `VisionForgePlugin` 显式注册为 `visionforge:web_visual`；Web 从 PluginRegistry 解析场景，场景状态保存插件 ID/版本，未启用插件时安全拒绝。
- UI Spec、参考图、实际截图、Browser Run、Visual Review、视觉质量门禁和场景 Run 使用 `visionforge:*` Artifact kind；Core 不解释这些业务协议。
- Core 已实现受控 `core:build`、`core:test`、`core:cli` Validator：完整 argv 白名单、无 shell、清理环境、进程组超时清理和脱敏裁剪日志；超时或缺失工具保持 unknown。
- 建立首个版本化离线 Coding 任务 `python-tax-rounding`；Runtime 只在独立验证副本注入隐藏检查，starter 稳定失败、参考修复稳定通过，Profile gate 与验证 Workspace 摘要绑定。
- 固定 Core Coding 任务集已扩展为舍入 Bug、API payload 输入契约和跨文件 CLI 三类；build/test/CLI 分别提供确定性门禁，不依赖网页或模型评分。
- `FixedCodingEvaluationRunner` 每次复位独立 Workspace，并生成版本化离线 JSON 校准报告；3 个 starter 全部失败、3 个参考修复全部通过，坏题不会被误判为校准成功。
- 建立三方案 `AblationStrategyProfile`、统一预算和 Artifact 可见性策略；单 Agent、Planner + Developer 与完整 Tester/Fixer 使用同一固定任务和 Validator。
- 脚本化 dry-run 已覆盖 9 个 trial 的首次通过、修复、预算、越权和指标路径；报告明确区分脚本/模型用量，当前真实模型调用为 0，结果不得用于宣称多 Agent 更优。

## 关键设计决策

- 生产化阶段采用 **production-shaped modular monolith**：先在一个可部署系统内保持清晰模块边界和持久失败语义，再由真实锁竞争、吞吐、可用性或隔离需求推动 PostgreSQL、外部队列和远程 Worker；不以微服务数量代表生产成熟度。
- 目标控制面显式区分 `Scope`、`Thread`、`Turn/Outcome`、`Message`、`Task/ScenarioRun`、`AgentInstance/AgentSession`、`Invocation/Attempt`、`SessionBinding`、`RouteEdge`、`AcceptanceRecord` 和 `RuntimeEvent`。AgentSession 是 Runtime 自有连续性，SessionBinding 只是到供应商 Session 的可替换映射，彼此不能混用。
- Discovery 使用受控的对等判断与跨模型校准；Delivery 使用确定性 DAG。自由文本 mention 不是可信控制协议，动态转交必须转成结构化 Handoff 并由 Runtime 接纳。
- Agent Backend 分为两类：直接模型 API 的 `RawModelBackend`，以及 Codex、Claude Code、Gemini CLI 等自带工具循环的 `FullAgentBackend`。两者统一到可流式、可取消、可恢复的 Invocation Event 协议，但通过能力协商和供应商扩展保留差异。
- Agent Profile 由 Role、Backend/Model Policy、Tool Capability、Context Policy、Output Contract 和预算组成；Role 不永久绑定 GPT、Claude、Gemini、DeepSeek、Qwen 或其他具体模型。
- 模型路由必须记录 provider、model/version、Prompt/协议版本、能力、策略和选择理由。Fallback 不能静默降低隐私、工具、结构化输出或验证要求。
- Thread/Task/Scenario 决定何时需要工作，Role 是 Worker 路由第一键，能力、输入协议、运行策略和可用性完成同 Role 内选择；Agent 和模型是可替换 Worker。
- Harness 独占任务状态、权限、安全策略、Artifact 接纳和最终收敛判断。
- Agent 只能读取裁剪后的 `RoleMemoryView`，不能访问密钥或扩大权限。
- Agent 不能直接修改共享目录或直接改变 TaskGraphRuntime 状态。
- 节点之间通过 Artifact 引用交接，不通过共享可变对象隐式通信。
- 在新交互域中，Message Store/Journal 独占消息正文、顺序、sender、parent 和投递状态；Artifact 只保存附件、大正文、证据和消息产出的结构化对象，不能成为第二套消息状态。
- 每个持久实体必须直接携带 `scope_id`，或通过不可变外键唯一归属一个 Scope；父子关系、Artifact/Acceptance Evidence 和因果引用必须同 Scope。Context、Memory 和检索先按 Scope fail-closed，再做 Thread、Project、Role 和相关性过滤。
- 未经验证的推测不能晋升长期记忆。
- 当前实现仍使用线程池和 SQLite。Runtime 2.0 先补齐持久 Invocation、Event Journal、幂等、lease/heartbeat、fencing、硬取消和故障恢复；是否引入外部工作流平台、消息队列或 PostgreSQL 由多进程正确性和容量证据决定。向量数据库或图数据库仍必须由检索评测证明必要性。
- PROD-01 采用状态表为当前业务真相源、Journal 为不可变审计记录、Snapshot 为兼容恢复检查点；关键状态、Event 与 Outbox 必须同一 SQLite 事务提交，不实施平行状态或完整 Event Sourcing。
- 输入模态与验证场景解耦：图片、音频或视频可以描述后端、CLI、库或前端任务；Visual Reviewer 只在显式 `web_visual` 场景启用。
- 通用 Coding 任务的 completed 只依赖构建、固定/隐藏测试、行为断言、权限和回归；不使用抽象视觉评分。
- Core 通过显式 `PluginRegistry` 接纳可信场景插件；Core 不依赖具体插件，场景使用 `plugin_id:scenario` 命名空间并按 Core API 版本校验；场景快照同时保存插件 ID/版本并在恢复时拒绝漂移。
- 模型只有提交观察、推断、建议和候选产物的权力；只有 Runtime 能签发 `AcceptanceRecord`。Evaluator principal 只能提供证据。Outcome 固定为 `unknown/needs_input/accepted/rejected`；continue 表示保持 unknown 并调度下一 Invocation，blocked/cancelled 属生命周期状态。Worker 正常返回不等于验收通过。目标 Coding Plugin 才把 build/test/Review 等 `VerificationRecord` 作为主要接受证据。
- `Invocation.completed`、`Outcome.accepted` 与 `Thread.archived` 是三种不同状态：一次模型调用结束不能证明本轮结果被接受，本轮结果被接受也不能自动关闭长期 Thread。
- 内部 Specialist 委派默认留在同一 `Scope/Thread`，由 `HandoffProposal → RouteEdge → ChildInvocation` 表达，不能为了一个内部子任务偷偷创建新的长期 Thread。`Thread`、`AgentInstance` 和 `AgentSession` 是可持久恢复的协作身份与连续性，不等于常驻模型进程；每个实际执行的 `Attempt` 都必须进入技术终态，并由 Runtime 回收或隔离其进程、SessionBinding、Capability、Lease、端口和临时环境。
- 当前 Core 已有通用 Evidence、Claim、Verification 和 Validator 基础，以及明显绑定仓库的 `CodingRequirement/RepositoryScope`。PROD-01A 没有新增重叠的 `InteractionRequest`：新 `Message + Turn` 表达通用交互请求，现有 `TaskSpec` 继续承载可选 Task；`UI Spec`、视觉评分和视觉问题分类仍属于 VisionForge。
- Role 始终是 Worker 路由的第一键，承载职责、权限、记忆视图和职责隔离；同一 Role 后续允许注册多个 Worker，再由能力、输入/输出协议、运行策略和可用性进行确定性筛选，不能因为缺少 Worker 而降低要求或改派其他 Role。

## 生产可靠性与智能效果边界

Harness 的确定性不变量与模型智能指标必须分开报告，不能用模型答错掩盖 Runtime 事故，也不能把模型答错全部归因于 Harness。

目标 Runtime 不变量：

- `false accepted = 0`；每种交互或插件场景必须显式声明 `AcceptancePolicy`，accepted 必须具有该策略要求的新鲜、匹配证据。只有策略要求独立评估时，Producer 与 Evaluator 才必须 principal 分离。
- 跨 Scope、Thread、AgentSession、Artifact 或 ExecutionEnvironment 的污染为 0；供应商 SessionBinding 也不得串线。
- 未授权文件、命令、网络、凭据和外部消息副作用为 0。
- 已 committed 的 Message 和已确认 RuntimeEvent 永久丢失为 0；未收到 DeliveryAck 却记录为 delivered 为 0；重试产生的重复可见或不可逆副作用为 0。临时投递失败、重试次数和送达延迟作为运行指标，不伪装成“永不失败”。
- Token、费用、调用次数和资源硬预算突破为 0。
- 所有副作用都能追溯到 user、scope、thread、turn、可选 task/scenario、invocation、attempt、principal、tool 和证据。

模型与协作效果使用分场景统计指标：消息送达与首响应延迟、accepted interaction/task rate、handoff 有效率、未解决问题显式率、Artifact 来源完整率、human intervention rate、结果方差，以及每次 accepted 交互的 Token、费用和端到端延迟。Fixer recovery rate 只属于 Coding 场景。多 Agent 必须与强单 Agent 基线比较，额外 Agent 需要证明边际收益。

## 当前限制

- 尚未把 Scope、Thread、Turn、Message、AgentInstance、AgentSession、Invocation、Attempt、Outcome/Acceptance、SessionBinding、RouteEdge 和 RuntimeEvent 建模为统一、持久的一等实体。
- 旧 Role/Capability 仍以 Planner、Implementer、Tester、Reviewer、Fixer 和代码读写/验证为主；PROD-01A 已新增不依赖 Coding 的通用 AgentProfile/Role 协议，并通过单向兼容适配器保留旧 Role 映射。真正的 Worker 注册迁移和执行接入仍在 PROD-01D。
- 当前 `TaskDispatcher` 和 `TaskGraphExecutor` 使用进程内线程池，没有生产意义上的 durable enqueue、claim/lease/heartbeat、fencing token、幂等键、inbox/outbox、dead-letter 和孤儿任务回收。
- 当前 DAG Snapshot 解决安全边界上的重放问题，但不是追加式 Runtime Event Journal；事件缺少统一的 trace_id、invocation_id、attempt_id、causation_id 和 correlation_id。
- ModelClient 当前是同步、非流式请求，主要只有 OpenAI Chat Completions 兼容实现；尚无统一的流式 AgentEvent、provider request/session 引用、工具调用事件、finish reason、硬取消和 Full Agent CLI Adapter。
- `timeout_seconds` 主要是策略元数据，不能强制终止运行中的模型线程。
- 暂停和取消只在 Worker 边界生效，不能立即中断 HTTP 请求或验证子进程。
- 受控命令已有 argv 白名单、清理环境、超时和进程组终止，但仍作为宿主机普通进程运行；尚无每 Invocation 文件系统/容器、默认断网、CPU/内存/进程/磁盘限制、短期凭据和高风险人工审批。
- Workspace 批量变更依靠逐文件原子替换和失败补偿；其他读者仍可能观察到批次中间态，不具备真正跨文件事务语义。
- 不同顶层 Run 之间尚无统一 Workspace lease、基线版本和 compare-and-swap；图内资源冲突也主要依赖精确 scope 字符串，尚无可靠的路径包含、glob 交集和符号级分析。
- Reviewer 和 Safety 尚未成为 DAG 最终收敛门禁。
- 记忆检索已有确定性单元测试，但尚未建立独立测评集、质量指标、真实任务对照实验和调优闭环。
- 当前使用实体精确命中和文本排序；是否需要向量或混合检索应由测评结果决定。
- 当前 Browser Tester 只支持固定 Vue 模板、单一本地 HTTP origin 和 Chromium；浏览器二进制由 Playwright 安装或由 Runtime 显式指定。
- 当前已完成一次保留失败记录和一次校准后真实基线，但 9 个试验的交付通过率仍为 0；结构化输出可靠性、构建错误证据保真和视觉修复稳定性尚未达到可用于产品结论的水平。
- 场景恢复不会重复已完成 DAG 节点；如果 Workspace 在快照后被外部修改，会拒绝自动恢复并要求人工处理。
- 当前 Web 已从 VisionForge 专用页调整为 Coding Harness 兼容工作台，但仍以单次 Coding Run 为中心；它尚不是目标中的持久 Thread、Agent 泳道、讨论因果链和用户介入工作台。VisionForge 的 Scenario Runner 继续作为插件 API 和回归资产保留。
- Web 上传资产目录会跨进程保存，但 Web 任务索引和运行句柄目前只保存在内存中，服务重启后不能可靠查询、取消或恢复旧任务。
- 固定 Vue 模板当前共享单一浏览器端口，因此 Web Runtime 串行执行页面任务；尚未支持取消正在运行的任务。
- 固定评测框架的第二次真实运行只有 SaaS 任务形成完整三方案结果；其余任务受模型空内容、非法/截断 JSON 和不存在的图片引用影响。该报告可以作为可靠性诊断基线，但尚不能证明业务效果提升。
- 第一版固定任务集只有 3 个页面，适合 MVP 烟测，不足以产生统计上稳定的普遍结论。
- 当前代码库已有 build/test/CLI Validator，目标归属 Coding Plugin；通用 CLI/Web 产品入口尚未装配通用 Interaction Profile，API/browser Validator 由后续工具能力或场景插件提供，VisionForge 继续使用已有场景门禁。
- EvidenceGrant 当前由 Composition Root 注入且不作为可跨进程复用的授权凭据持久化；恢复时必须重新提供，否则结构化需求会安全拒绝。
- text/image/audio/video 已有统一 Evidence 描述协议；图片感知、音频转录和当前偏 Coding 的视频 Bug 时间线协议与 Fake Client 链路已接入。通用视频观察协议、真实媒体持久化及真实图片、语音和视频供应商适配尚未接入。
- 确定性 Coding 评测已有 3 个小型任务，能够覆盖函数、API 输入和跨文件 CLI，但样本仍不足以代表普遍 Coding 能力；三方案已接入通用 ModelClient Worker 并用 Fake Model 验证，尚未形成真实模型效果对照。
- VisionForge 仍位于 `coding_workflow/visionforge`，但已作为显式插件装配；本批未做包目录大迁移或删除 Legacy Runner。
- 图片、音频和视频已分别通过受控 Worker 转成结构化感知 Artifact；现有视频类型仍名为 `core:video_bug_evidence`，这是待迁移的 Coding 泄漏，不是新的 Core 术语。三条链路都保留原 Evidence 引用，下游文本 Agent 不重复读取原媒体。
- 已建立统一 `MultimodalIntakeRunner + core:evidence_bundle`：同一需求的媒体感知可并行执行，每个原始 Artifact 最多处理一次；Bundle 保存每条来源和 ready/blocked/failed。当前 Composition Root 仍把就绪 Bundle 交给 Coding Planner，PROD-05 才接入通用 Thread/Context 产品链路。

## 学习与生产实践规则

- 每个批次必须由一个真实工作负载、历史事故或可复现的故障假设驱动，不能只因为某项技术流行而接入。
- 每项生产能力必须同时说明：领域契约、持久状态、失败语义、恢复/补偿方式、审计证据和验收 SLI/SLO。
- 从 PROD-01 起，每批至少包含一个与范围匹配的主动故障演练或确定性故障注入，例如 `kill -9`、重复投递、取消/完成竞态、供应商 429/半截响应、Session 串线、资源冲突、秘密泄漏或磁盘/预算耗尽。纯协议的 PROD-01A 用非法状态、跨 Scope、伪造 Acceptance、过期 lease/fence 和迟到结果负向构造验收；只有持久化或进程边界落地后才能执行对应的 `kill -9`，不得为未实现资源伪造演练证据。
- Fake Model 和单元测试证明 Runtime 按设计工作；真实交互、多模态输入和分场景 Workload 才能评价智能效果与可落地性，真实代码仓库只验证 Coding 能力，两类证据不得混为一谈。
- 每次真实事故必须形成：事件证据、影响与根因、修复、回归测试、预防规则/Skill 或自动门禁，以及 SLO 影响记录。
- 每个 `PROD-*` 批次必须同时规划、实现和验收对应的 `INC-*` 增量；后续新增或完善 Plan、Backlog、Learning Path 和 HANDOFF 内容时，必须包含事故检测、证据、止损、回放、回归和覆盖指标，不能把事故闭环推迟到功能全部完成之后补做。
- 允许实验得出“某个 Agent、Memory 策略、并发或反思机制没有收益”的结论；无法证明边际价值的复杂度应删除、降级为可选策略或继续暂缓。
- 开始生产批次前先冻结范围、信任边界、外发数据、模型/Prompt/协议版本、预算和停止条件；真实外部调用仍需要当次明确授权。

## 下一步

### 方向决议

2026-08-23，用户确认产品中心必须回到“可交互、可长期运行、支持多个独立 Agent 协作的多模态 Runtime”。目标上 Coding 是可加载的专业能力；当前 VisionForge 是独立的 `visionforge:web_visual` Scenario Plugin 并复用 Coding 能力。任何一个 Bug、仓库或网页测试都不能代表 Core。

`Plan/Plan26.md` 已完成 `PROD-00` 产品 Charter，冻结 Scope、Thread、Turn/Outcome、Message、AgentInstance/AgentSession、Invocation、SessionBinding、RouteEdge、Artifact/Context、Capability 和场景化 Acceptance 的边界。批次 10A～13A 的代码和测试继续作为 Coding、多模态 Intake 与插件机制的已实现资产保留，不因产品纠偏删除，也不得再被描述成默认产品流程。

后续严格一次推进一个小批次。`PROD-01A` 已完成；当前下一批固定为 `PROD-01B`，不得跳过它直接增加更多供应商、自由 A2A、向量数据库、微服务或分布式 Worker。

### PROD / INC 双轨联动规则（后续任务强制执行）

事故学习闭环是一等子系统，主计划见 `Plan/Plan25.md`，覆盖范围与漏检计算见 `Plan/闭环覆盖范围.md`。它不是 `PROD-07` 才补做的复盘模块，而是从 `PROD-00` 开始伴随每项生产能力演进的横切控制面。

当前事实：`INC-00` 的专项计划及产品泛化已随 `PROD-00` 完成文档冻结；`PROD-01A` 只完成 RuntimeEvent 值协议和同步不变量的地基，Detector、持久 Journal/Ledger、Replay 和自动学习仍未实现，`INC-01`～`INC-05` 状态不因此提前完成。

后续任何任务只要新增、修改、拆分、实现或收口一个 `PROD-*` 批次，就必须在同一份 Plan、Backlog 更新和交接摘要中增加 `INC 联动` 小节，至少写明：

1. 对应 `INC-*` 阶段、当前状态、前置依赖和本批只完成的增量；
2. 新增或受影响的 `RuntimeEvent`、同步不变量和异步 Detector；
3. IncidentSignal、Evidence Bundle、脱敏、审计和证据定位方式；
4. 自动止损、人工批准、恢复和回滚边界；
5. ReplaySpec、Fault Injection、事故负向用例与正常路径对照；
6. Regression、Policy、Validator、Adapter、Runbook、Skill 或 Memory 的正确修复落点；
7. detected、prevented、missed、escaped、false-positive、recurrence、MTTD、MTTC 和 MTTR 中本批适用的指标；
8. `INC-*` 状态是否变化、剩余缺口，以及需要同步更新的 `Plan/Plan25.md`、`Plan/闭环覆盖范围.md`、`OPTIMIZATION_BACKLOG.md`、`LEARNING_PATH.md` 和 `HANDOFF.md`。

若某一项在当前批次确实不适用，必须显式写 `不适用`、原因和以后由哪个批次补齐，不能静默省略。不得仅用“增加日志”“调整 Prompt”或“让 Agent 反思”代替事故闭环。

双轨完成点如下：

| 生产主线 | 必须同步完成的事故学习增量 |
|---|---|
| `PROD-00` | 完成 `INC-00`：统一 Charter、Backlog、Learning Path、Core/Plugin 事故目录、领域契约、SLO 和验收口径。 |
| `PROD-01` | 完成 `INC-01`；Journal 可用后额外建立四组 Observe/Shadow 信号：false acceptance、消息完整性（状态不一致硬错误与送达 SLO 超限分开）、Thread/Session 错绑、取消/迟到结果。它们只把 `INC-02` 标为“部分 Shadow”，不宣称 INC-02 完成。 |
| `PROD-02` | 扩展 `INC-02` 的 Session、Model/Provider/Adapter Detector、协议错误证据、熔断/fallback 事故、模型版本漂移和 Canary 指标。 |
| `PROD-03` | 扩展 Tool/权限/隔离/副作用 Detector 和止损，累计完成首批 `INC-02` 的 Detector、Evidence 与预批准 Runbook 验收。 |
| `PROD-04` | 完成 `INC-03`：协作与并发事故的确定性 Replay、Fault Injection、ChangeSet、Shadow/Canary/Rollback 和正常路径对照。 |
| `PROD-05` | 完成 `INC-04`：LearningItem、人工审批、知识晋升、Guardrail Evaluation、Memory 投影、退役和复发重开；通用媒体链路同步扩展 `media_binding_mismatch` Detector。 |
| `PROD-06` | 启动 `INC-05`：接入容量、背压、配额、压力/soak、告警、error budget 和事故运营指标。 |
| `PROD-07` | 完成 `INC-05`：Incident Operations、Game Day、Runbook、人工接管、关闭门禁、备份恢复和长期复发评估。 |

这里的“同步完成”不表示每个 PROD 批次都要完成整个事故子系统，而是必须完成与该批新增风险对应的事故纵向能力。一个 `PROD-*` 批次不能只因正常路径测试通过而关闭；其声明范围内的 Incident 事件、故障注入、证据、回放或明确的后续缺口也必须达到该批冻结的验收标准。

后续 Plan 固定使用以下最小模板：

```text
### INC 联动
- 对应阶段：INC-xx
- 当前状态：未开始 / Observe-only / Shadow / Active / 已完成
- 新增风险与事故目录：
- RuntimeEvent / Detector / Invariant：
- Evidence / 脱敏 / 审计：
- 止损 / 恢复 / 人工权限：
- Replay / Fault Injection：
- 事故用例与正常对照：
- SLI/SLO 与覆盖率：
- 本批完成门禁：
- 剩余缺口及后续归属：
- 需要同步的文档：
```

### 任务专用子 Agent 与 Invocation 回收规则（后续实现强制）

用户可见、可长期协作的是 `Thread`；为某个任务临时创建的 Specialist 执行单元是 `ChildInvocation/Attempt`。内部委派必须优先复用当前 Thread，通过 `parent_invocation_id`、`RouteEdge` 和因果事件建立关系。只有用户明确要求新的长期协作空间，或产品策略确实需要独立保留、权限和参与者边界时，才能创建新 Thread；Thread 结束时使用 close/archive，不使用进程意义上的 kill。

需要终止的是 Invocation 的执行域。Runtime 必须把“业务/交互结果”和“资源是否已经清理”拆成两条状态：

```text
执行状态：CREATED → QUEUED → CLAIMED → RUNNING
          → SUCCEEDED / FAILED / CANCELLED / TIMED_OUT

清理状态：ALLOCATED → ACTIVE → DRAINING → TERMINATING
          → REAPED / TERMINATION_FAILED
```

`SUCCEEDED` 只表示候选结果和终态意图已经可靠持久化；只有 `cleanup_state=REAPED`，Invocation 才能 `CLOSED`。如果清理失败，结果证据可以保留，但执行域必须进入 `TERMINATION_FAILED/RECOVERY_REQUIRED` 并创建 Incident，不能静默宣称已经完全结束。`Outcome.accepted` 仍由 AcceptancePolicy 决定，Thread 仍由用户或生命周期策略归档，三者不能互相推导。

所有成功、失败、取消和超时路径必须进入同一个幂等 Finalizer，顺序至少包括：

1. 使用乐观版本或 CAS 把 Invocation 转为 finalizing，停止接受新的 ToolCall、Handoff 和副作用请求；
2. 先吊销本次 CapabilityGrant、短期凭据和写权限，并级联请求取消所有非终态 ChildInvocation；
3. 封存候选 Artifact、Usage、终态意图和 Evidence，关键状态、RuntimeEvent 与 Outbox 同事务提交；
4. 请求 Backend、工具和子进程协作退出，在冻结的 grace period 后终止 Runtime 拥有的进程组；仍不退出时强制 kill 整个进程组、容器或等价隔离单元；
5. 释放 SessionBinding、Workspace/ExecutionEnvironment Lease、端口、预算预留和临时目录；保留 Thread、Message、Artifact、Acceptance、RuntimeEvent 和 Incident 等审计事实；
6. 持久化 `reaped`，由 Watchdog/Reaper 重试未完成清理；旧执行单元的所有迟到事件、Artifact 和副作用请求必须被 fencing token 确定性拒绝。

推荐的终态不变量：

```text
Invocation.closed
⇒ execution_state is terminal
∧ cleanup_state = REAPED
∧ active_capability_grants = 0
∧ active_resource_leases = 0
∧ active_child_invocations = 0
∧ late_results_cannot_mutate_state
```

当前 `ThreadPoolExecutor/Future.cancel()` 不能安全终止已经运行的 Python 线程，当前同步 ModelClient 的超时也不能证明供应商已经停止推理。因此必须如实分层实现：

- `PROD-01A` 冻结 parent/child、执行状态、清理状态、终止原因、deadline、lease、fencing 和资源引用协议；
- `PROD-01C` 实现取消意图持久化、级联取消、claim/lease/heartbeat、fencing、Watchdog、孤儿识别和幂等 Reaper；在仍使用进程内线程的路径上，只能保证逻辑失权和拒绝迟到结果，不能伪称已经物理 kill；
- `PROD-02` 为 Raw Model/Full Agent Backend 实现流式取消、连接关闭、CLI 进程组终止和 SessionBinding revoke/expire；远程 Provider 无法保证停止时，必须记录潜在费用并依靠 fencing 阻止其结果生效；
- `PROD-03` 对高风险工具增加独立进程、容器/cgroup 或等价隔离、孙进程回收、短期凭据和 Workspace/端口资源回收；
- `PROD-04` 只有在上述父子生命周期和回收语义可用后，才能开放动态 Specialist 委派和多级 Handoff。

建议由 `InvocationSupervisor` 统一组合 CancellationController、Lease/Fencing、Backend/Process Supervisor、CapabilityBroker、SessionRegistry、Workspace/Environment Manager、Watchdog 和 ResourceReaper；不能让每个 Agent 自己决定是否已经退出。

事故联动至少覆盖 `orphan_invocation`、`stuck_cancelling`、`termination_failed`、`process_leak`、`session_binding_leak`、`resource_lease_leak`、`late_result_after_cancel`、`side_effect_after_parent_cancel` 和 `non_terminal_descendant_on_close`。`PROD-01E/INC-01` 先记录并 Shadow 取消、迟到、孤儿与清理失败信号；`PROD-04/INC-03` 再完成多级委派、Parent/Child 竞态和资源冲突的确定性 Replay 与 Fault Injection。

必做故障演练包括：Worker 忽略取消、Agent/工具创建孙进程、Provider 在取消后迟到返回、Artifact 已保存但终态事件未提交时进程退出、Reaper 重启、Parent 取消与 Child 完成竞态、旧 Worker 在 lease 失效后继续提交，以及 Workspace/端口/SessionBinding 回收失败。每个演练都必须同时检查技术终态、资源回收、迟到结果拒绝、证据和合法正常路径。

### PROD-00：产品中心纠偏与 Runtime Charter

状态：**已完成（仅文档和协议冻结，Runtime 行为未修改）**。

验收物是 `Plan/Plan26.md` 及本次同步的 HANDOFF、Backlog、Learning Path 和事故计划。第二个真实代码仓库已移到目标 Coding Plugin Canary/dogfood，不再是 Core Charter 的完成条件。PROD-00 是唯一的 Charter 例外：以代码事实核对、跨文档一致性、`git diff --check` 和现有回归验收；主动故障演练从 PROD-01 开始。

### PROD-01：Durable Thread、Message、Invocation 与 Event Journal

状态：**01A 已完成，当前等待 01B**；继续严格按 01B～01E 逐批推进。

目标：建立第一版真正可恢复的交互控制面，并复用现有 Artifact、SQLite Snapshot、TaskGraph 与 ScenarioRuntime，不另起平行真相源。

执行顺序：

1. **PROD-01A 领域协议与迁移骨架（已完成）**：实现最小 `Scope/Thread/Turn/Message`、通用 `AgentProfile/Role`、`AgentInstance/AgentSession`、`Invocation/Attempt`、`Outcome`、`AcceptancePolicy/Record` 和 `RuntimeEvent`；冻结 Message/Artifact 边界与 Coding 兼容映射。Invocation 只包含 `input_refs + input_digest + policy_snapshot_ref + budget_reservation`，完整 Grant 和 ContextManifest 分别留给 PROD-03/05。
2. **PROD-01B 状态 Store、Journal 与 Outbox（当前下一批）**：SQLite 状态表是当前业务真相源，Journal 是不可变审计记录，Snapshot 是兼容恢复检查点；状态更新、Event、Outbox 与最小 BudgetLedger 预留/结算同事务提交。
3. **PROD-01C Durable Invocation**：durable enqueue、幂等、claim/lease/heartbeat、fencing、watchdog、孤儿回收、重启恢复和取消意图持久化；模型请求硬取消留给 PROD-02。
4. **PROD-01D 兼容接入与 Web 查询**：把 TaskGraph、ScenarioRuntime 和当前 Coding 纵向切片接入 Thread 的可选工作，保留回归；Web 先支持持久查询，不实现完整 Agent 泳道。
5. **PROD-01E INC-01 与首批 Shadow**：完成 `INC-01`；建立 false acceptance、消息完整性、Thread/Session 错绑、取消/迟到结果四组 Observe/Shadow 信号，只将 `INC-02` 标为“部分 Shadow”。

PROD-01B 的 Store 门禁已固定：在同一 `RuntimeUnitOfWork` 中用权威索引解析并校验同 Thread/Turn/AgentSession 关系，只允许 Runtime 依据已持久且 `validate_against(policy)` 通过的 Record 生成 Outcome，对 `event_id` 和序号实施唯一/append-only 约束，并将状态、Event、Outbox 和预算预留/结算原子提交。Invocation 的 `closed` 还必须用 Store 中的 Attempt/Child/Lease/Grant/Resource 权威索引二次核对，不能只信单个 DTO 上的活动引用。

PROD-01A 只做领域协议、状态不变量、序列化和兼容映射测试。Backend、Capability、Context、Mailbox 只保存可选或不透明版本引用；本批不实现 Mailbox 投递、SessionBinding、模型调用、Gateway、Context Compiler、SQLite Store/Journal、调度、Web 或现有 Runtime 执行接入。

PROD-01A 实现事实：

- 通用 Core 协议不依赖 Coding；每个实体直接携带 Scope，父子、因果、Artifact、Acceptance 和执行引用跨 Scope 时 fail-closed，所有值采用严格 schema version、不可变结构和 JSON 往返。
- Invocation 输入用 `ScopedRef + content_hash` 固定，Attempt 持有真实 worker/principal、lease 和 Runtime 单调签发的 fence；执行与清理分轴，终态必须有 TerminalRecord，`TERMINATION_FAILED` 保留独立清理证据，`closed` 不等于 succeeded 或 accepted。
- late result admission 是纯函数，不写 Store：旧/未来 fence、取消后、终态后、过 deadline/lease、错误 Thread/Attempt/输入/策略一律拒绝；相同 mutation 是幂等 no-op，同 ID 不同 payload 是冲突。
- AcceptancePolicy/Record/Outcome 固定为 `unknown/needs_input/accepted/rejected`；accepted 必须满足匹配、新鲜证据和策略要求的独立 evaluator。数据类只能校验协议形状，真正“只有 Runtime 可签发”的写权限必须由 PROD-01B Store 在同一事务中强制。
- RuntimeEvent 只保存小型、脱敏、深冻结 JSON 元数据和引用；append-only、唯一序号、授权写入和 Outbox 还未实现。Coding 的 Role/Worker/Task/Artifact/Verification 通过单向适配器映射，旧 passed/verified/completed 都不会直接生成 accepted。

### PROD-01A / INC 联动与验收

- 对应阶段：`INC-01` 的协议前置；状态仍为待开始，只有 RuntimeEvent envelope 与同步不变量已具备。
- 风险与不变量：覆盖跨 Scope、错误 subject、伪造 Runtime Acceptance、非法执行/清理状态、过期 lease、stale/future fence、取消后迟到结果、幂等冲突和 Artifact 内容漂移；合法普通交互与 Coding 兼容映射同时作为正常对照。
- Evidence / 审计：证据是严格协议对象、内容哈希和 64 项定向测试；没有持久 Journal/Ledger，因此不能声称事件已经 append-only 或事故可以重启恢复。Event 值协议禁止正文、私密推理、凭据、未校验 `*_ref`、Prompt、Completion、原始媒体和 bytes；同一 Event 的更正必须使用新 event ID。
- 止损 / 恢复：本批仅在对象构造和 mutation admission 时 fail-closed，不产生外部副作用；事务补偿、重启恢复、Watchdog/Reaper 和人工事故权限分别由 PROD-01B/01C/01E 实现。
- Replay / Fault Injection：本批以确定性负向协议构造覆盖；SQLite 中断、重复投递、`kill -9`、锁竞争和孤儿恢复不适用，因为尚未实现 Store/进程边界，固定由 PROD-01B/01C 补齐。
- SLI/SLO：新增协议测试 64 项全部通过，默认全量 277 项通过、4 项真实浏览器测试按设计跳过；已注册 Detector 数仍为 0，不能报告 detected/missed/MTTD/MTTR。
- 完成门禁：Python compileall、`git diff --check` 和全部默认回归通过；无需手动检验，没有模型、网络、媒体、外部仓库或数据库写入。

必做故障演练：消息或副作用已提交但完成事件未写入时 `kill -9`、重复投递、错误 Thread/Session 绑定、取消与完成竞态、SQLite 锁竞争/磁盘异常和孤儿 Invocation 恢复。合法普通对话与当前 Coding 纵向切片都必须有对照，避免把 build/test 误设为所有 Thread 的完成条件。

### 后续生产顺序

1. **PROD-02 Backend v2、Session 与 Streaming**：Raw Model/Full Agent Backend、流式事件、硬取消、SessionBinding、usage/finish reason、错误分类、fallback 和 Canary。
2. **PROD-03 Capability、Tool Gateway 与执行隔离**：每 Invocation Grant、Secret Broker、隔离环境、默认断网、资源配额、高风险 Approval 和副作用审计。
3. **PROD-04 交互式协作控制面**：Mailbox、结构化 Handoff、并行/顺序协作、独立 Review、用户介入、循环终止，以及 Thread/Agent 泳道和讨论因果链。
4. **PROD-05 Context、共享记忆与多模态工作区**：Context Compiler/Manifest、版本/TTL/ACL、Session 压缩、共享记忆治理、检索评测和通用媒体附件链路。
5. **PROD-06 插件产品化与效果/容量验证**：Coding/VisionForge 插件入口、四类业务模式分层评测、背压、公平性、配额、压力与 soak。
6. **PROD-07 迁移与事故运营**：Schema/Prompt/Plugin/Model 迁移、golden trace replay、canary、回滚、备份恢复、Game Day 和运行手册。

任何真实模型、媒体、外部仓库或网络调用都需要执行当次的新授权，不能沿用历史授权摘要。

## 关键文件

- `demo/coding_workflow/harness/task_graph.py`：TaskSpec、DAG 校验和 ready 任务选择。
- `demo/coding_workflow/harness/scheduler.py`：任务图运行状态和 Artifact 就绪管理。
- `demo/coding_workflow/harness/executor.py`：并发调度、局部重试和节点结果接纳。
- `demo/coding_workflow/harness/dispatcher.py`：当前进程内 TaskHandle、线程池提交、暂停、取消与 shutdown；PROD-01 durable queue 的替换/兼容边界。
- `demo/coding_workflow/harness/lifecycle.py`：当前生命周期状态机与 checkpoint 取消语义；PROD-01 端到端硬取消和恢复的基础。
- `demo/coding_workflow/runtime_domain/common.py`：PROD-01A 严格版本、Scope 引用、JSON 冻结和内容摘要基础。
- `demo/coding_workflow/runtime_domain/interaction.py`：通用 Scope、Thread、Turn、Message 与 Agent Role/Profile/Instance/Session 协议。
- `demo/coding_workflow/runtime_domain/invocation.py`：Invocation/Attempt、执行/清理双状态轴、输入快照、终止、lease、fencing 和迟到结果 admission。
- `demo/coding_workflow/runtime_domain/acceptance.py`：场景化 AcceptancePolicy/Evidence/Record 与 Outcome 协议。
- `demo/coding_workflow/runtime_domain/events.py`：小型、不可变、Scope 绑定的 RuntimeEvent envelope；持久 Journal 留给 PROD-01B。
- `demo/coding_workflow/coding_runtime_compat.py`：Coding Role/Worker/Task/Artifact/Verification 到通用协议的单向兼容映射。
- `demo/coding_workflow/planning.py`：结构化 Planner 和非法图修复。
- `demo/coding_workflow/dag_runner.py`：真实 DAG 端到端执行入口。
- `demo/coding_workflow/graph_workers.py`：DAG Worker 契约实现。
- `demo/coding_workflow/artifacts.py`：Artifact 与 ArtifactStore。
- `demo/coding_workflow/truth.py`：Claim、三态验证结果和不可变 VerificationRecord。
- `demo/coding_workflow/requirements.py`：通用 Evidence、Coding Requirement、仓库范围、验收、授权和冻结 Validator Profile。
- `demo/coding_workflow/validator_runtime.py`：Runtime Validator 注册、按 Profile 执行、报告 Artifact 和组合门禁。
- `demo/coding_workflow/command_validators.py`：受控 build/test/CLI 子进程、完整 argv 白名单、日志证据和三态判定。
- `demo/coding_workflow/coding_evaluation.py`：固定任务清单校验、Agent/私有验证 Workspace 隔离和 Profile 运行入口。
- `demo/coding_workflow/coding_evaluation_runtime.py`：多任务复位、starter/参考修复校准、trial 指标和 1.0 JSON 报告。
- `demo/coding_workflow/coding_ablation.py`：三方案 Profile、Artifact 可见性、统一预算、脚本 Worker、trial 和消融报告。
- `demo/coding_workflow/coding_model_workers.py`：四类 ModelClient Worker、Plan/Patch/Diagnosis Schema、严格解析、源码披露审计与请求哈希。
- `demo/coding_workflow/coding_ablation_execution.py`：真实实验配置、21 次调用估算、源码 preflight、真实 Runner 装配与报告 Bundle。
- `demo/coding_workflow/image_perception.py`：通用图片 Evidence、视觉 Claim 协议、感知 Worker、Role-first 注册与客观准确率。
- `demo/coding_workflow/audio_transcription.py`：供应商无关转录协议、时间戳 Transcript/Claim、音频授权与完整性检查、Role-first 注册和客观准确率。
- `demo/coding_workflow/video_perception.py`：供应商无关视频时间理解、事件/差异/候选复现协议、视频授权与完整性检查和 Role-first 注册。
- `demo/coding_workflow/multimodal_intake.py`：统一 Intake Plan、逐模态预检、并行媒体 DAG、状态化 Evidence Bundle、失败关闭和文本 Planner 交接。
- `demo/coding_workflow/model/budget.py`：Core 共享模型调用/Token 预留预算和受控客户端包装。
- `demo/coding_workflow/model/base.py`：当前 ModelClient、请求/响应、Usage 和能力协议；PROD-02 Backend v2 的迁移起点。
- `demo/coding_workflow/model/config.py`：模型配置、能力、结构化输出模式和供应商请求约束。
- `demo/coding_eval/v1/`：版本化 Core Coding starter、隐藏验收、参考答案和哈希清单。
- `demo/core_coding_eval_run.py`：默认零外部调用的离线题目校准 CLI。
- `demo/core_coding_ablation_run.py`：默认禁止真实模型的三方案脚本化 dry-run CLI。
- `demo/core_coding_model_ablation_run.py`：默认零网络的真实消融 preflight 与摘要绑定执行入口。
- `demo/coding_workflow/harness/registry.py`：WorkerDescriptor、Role-first 多实现筛选、结构化拒绝和选择审计。
- `demo/coding_workflow/roles.py`：Role 的职责、能力和权限边界；后续仍作为 Worker 第一路由键。
- `demo/coding_workflow/integration.py`：Patch 安全检查和集中合并。
- `demo/coding_workflow/memory.py`：分层记忆、Working Memory 和 RoleMemoryView。
- `demo/coding_workflow/memory_sqlite.py`：记忆和 Checkpoint 持久化。
- `demo/coding_workflow/runtime_sqlite.py`：TaskGraphRuntime、生命周期和 Artifact 快照持久化。
- `demo/coding_workflow/visionforge/contracts.py`：UI Spec 与 Visual Review 1.0 协议。
- `demo/coding_workflow/visionforge/artifact_types.py`：VisionForge 私有 `visionforge:*` Artifact kind。
- `demo/coding_workflow/visionforge/plugin.py`：VisionForge Manifest、`web_visual` 注册和产品 Registry 工厂。
- `demo/coding_workflow/visionforge/assets.py`：图片内容寻址存储与 Artifact 引用。
- `demo/coding_workflow/visionforge/agents.py`：四个 VisionForge 角色、结构化 Schema、模型能力和输入裁剪。
- `demo/coding_workflow/visionforge/browser.py`：受控进程、Vue 服务器生命周期、Playwright 调用与浏览器 Artifact。
- `demo/coding_workflow/visionforge/quality.py`：Runtime 组合质量门禁与可审计失败原因。
- `demo/coding_workflow/harness/scenario.py`：通用多轮 DAG、收敛决策、终态和恢复控制。
- `demo/coding_workflow/harness/plugins.py`：Core 插件 Manifest、受控注册上下文、命名空间场景和 Registry。
- `demo/coding_workflow/harness/scenario_sqlite.py`：场景清单、轮次与活跃 Artifact 持久化。
- `demo/coding_workflow/visionforge/dag.py`：VisionForge GraphWorker、外部参考图、主 DAG 与 Fix DAG Factory。
- `demo/coding_workflow/visionforge/scenario.py`：`WebVisualScenario` 和 `VisionForgeScenarioRunner` 产品入口。
- `demo/coding_workflow/visionforge/recovery.py`：SQLite 返工 Checkpoint、Artifact 快照和 Workspace 漂移检查。
- `demo/coding_workflow/visionforge/runner.py`：旧纵向执行兼容实现；Web 与评测已不再使用。
- `demo/coding_workflow/visionforge/web_runtime.py`：图片上传目录、固定项目准备、任务执行与安全公开快照。
- `demo/coding_workflow/visionforge/evaluation.py`：固定任务加载、参考图渲染、三方案试验记录、指标汇总和 JSON 报告。
- `demo/coding_workflow/visionforge/evaluation_runtime.py`：三方案隔离执行器、模型预算、固定验收注入和 Artifact Bundle 持久化。
- `demo/visionforge_eval_run.py`：真实基线预算预检与显式授权执行入口；默认不调用外部模型。
- `demo/coding_workflow/model/factory.py`：DeepSeek/Qwen 配置预设、环境变量读取和文本/视觉客户端独立路由。
- `demo/coding_workflow/model/openai_compatible.py`：当前同步、非流式 OpenAI 兼容请求，多模态输入、供应商结构化输出模式和本地 Schema 约束；也是 PROD-02 需要突破的现有限制。
- `demo/visionforge_model_smoke.py`：两次最小真实能力烟测入口；只报告安全的验证元数据。
- `demo/visionforge_vue_template/`：受保护的固定 Vue 3 + Vite 页面模板。
- `demo/visionforge_vue_template/.visionforge/browser-runner.mjs`：固定 viewport、受控交互、截图和浏览器证据采集。
- `demo/visionforge_vue_template/visionforge.ui-spec.json`：固定页面的可执行 UI Spec 测试用例。
- `demo/tests/test_visionforge.py`：VisionForge 协议、模型能力、图片和模板测试。
- `demo/tests/test_visionforge_browser.py`：命令生命周期和真实浏览器闭环测试。
- `demo/tests/test_visionforge_runner.py`：Fake Model 契约、安全 Patch、Artifact 链和真实纵向链路测试。
- `demo/tests/test_visionforge_rework.py`：质量门禁、修复上限、Artifact 替代、恢复和真实浏览器修复闭环测试。
- `demo/tests/test_visionforge_evaluation.py`：固定任务、实验配置、三方案汇总、Run 适配和真实参考图渲染测试。
- `demo/visionforge_eval/v1/`：版本化任务 manifest、3 个参考页面和 Runtime 固定验收 UI Spec。
- `demo/visionforge_eval/render-reference.mjs`：无外部网络、固定环境的 HTML→PNG 渲染器。
- `demo/visionforge_eval/README.md`：三方案反馈边界、统一评分口径和指标说明。
- `demo/coding_agent_cli.py`：通用 Coding DAG 的 CLI 入口。
- `demo/web_server.py`：当前 Coding Harness 兼容入口，并保留 VisionForge 上传、任务和图片读取 API；尚未实现持久 Thread API。
- `demo/web/index.html`、`demo/web/app.js`、`demo/web/styles.css`：当前 Coding 工作台兼容页面；不是目标中的 Thread/Agent 泳道产品页。
- `demo/tests/test_web_server.py`：Web Runtime、上传目录、受控请求字段和前端入口测试。
- `demo/tests/test_workflow.py`：Harness、DAG、记忆和端到端测试。
- `demo/tests/test_runtime_interaction_protocol.py`、`test_runtime_invocation_protocol.py`、`test_runtime_acceptance_protocol.py`、`test_runtime_event_protocol.py`：PROD-01A 通用协议、状态不变量、跨 Scope、late result 和 Outcome 防伪测试。
- `demo/tests/test_runtime_coding_compat.py`：Coding 单向映射、完整 TaskSpec 快照、Artifact 内容绑定和旧通过状态不越权测试。
- `demo/tests/test_plugins.py`：Core 插件注册、兼容、原子性、缺失语义和反向依赖禁令测试。
- `demo/tests/test_truth.py`：事实分类、验证权、伪造字段、unknown、执行/验收分离和 SQLite 往返测试。
- `demo/tests/test_requirements.py`：需求协议、范围冻结、EvidenceGrant、Profile Runner、验证新鲜度和恢复测试。
- `demo/tests/test_worker_routing.py`：同 Role 多 Worker、硬过滤、blocked、选择恢复和自审/自证隔离测试。
- `demo/tests/test_command_validators.py`：命令策略、三态、脱敏裁剪、隐藏边界、任务哈希和 starter/solution 门禁测试。
- `demo/tests/test_coding_evaluation_runtime.py`：三任务校准、Workspace 复位、JSON 报告和坏题拒绝测试。
- `demo/tests/test_coding_ablation.py`：三策略、反馈隔离、同预算、越权、预算停止、模型误接入和报告测试。
- `demo/tests/test_coding_model_workers.py`：Fake Model 纵向修复、能力预检、秘密披露、越权 Patch、伪造通过和 Registry 隔离测试。
- `demo/tests/test_coding_ablation_execution.py`：调用估算、全局预算、重试/输出限制、源码摘要和授权前零凭据测试。
- `demo/tests/test_image_perception.py`：图片授权、能力/哈希边界、Claim 幻觉约束、同 Role 路由、感知 F1 和同隐藏验收测试。
- `demo/docs/task-graph-and-memory.md`：设计边界说明。
- `catCafe/cat-cafe-tutorials-study-notes.md`：Cat Café 的对等判断/结构化执行、生产事故、Session、A2A、Review 和知识晋升参考。
- `catCafe/cat-cafe-runtime-framework.md`：Thread、Invocation、Session、AgentEvent、Queue、Adapter 和安全层的 Runtime 参考；只吸收机制，不取代现有 DAG/Artifact 真相源。
- `LEARNING_PATH.md`：交互式多模态 Runtime 的生产演进与事故驱动学习路线。
- `Plan/Plan06.md`：任务拆分和记忆机制的策略归档。
- `Plan/Plan09.md`：多模态 Coding Multi-Agent MVP、客观验收和实施顺序。
- `Plan/Plan11.md`：Core 插件边界、信任模型和 VisionForge 后续迁移顺序。
- `Plan/Plan12.md`：Core 事实与验证权边界、兼容策略和未完成事项。
- `Plan/Plan13.md`：Core 通用需求、Evidence 授权、Validator 执行和新鲜度边界。
- `Plan/Plan14.md`：Role-first 多 Worker 路由、审计、blocked 和 principal 职责隔离。
- `Plan/Plan15.md`：VisionForge 插件装配、Web 解析、场景身份和 Artifact 命名空间边界。
- `Plan/Plan16.md`：受控 Core Validator、私有隐藏验收和首个固定离线 Coding 任务。
- `Plan/Plan17.md`：多类型固定任务、离线复位、自校准规则和版本化报告。
- `Plan/Plan18.md`：三方案 Artifact/预算协议、脚本化 dry-run 和结果解释边界。
- `Plan/Plan19.md`：Core 模型 Worker、结构化输出、调用前披露、请求审计和真实运行前置条件。
- `Plan/Plan20.md`：真实消融冻结配置、源码外发范围、预算硬边界和授权摘要。
- `Plan/Plan21.md`：Core 图片需求证据链、视觉感知/文本规划拆分和确定性评测边界。
- `Plan/Plan22.md`：Core 音频需求证据链、独立转录客户端、时间线/不确定性和同一代码验收边界。
- `Plan/Plan23.md`：Core 视频 Bug 证据链、观察/推测/建议分离、时间线引用和同一回归验收边界。
- `Plan/Plan24.md`：统一多模态 Intake、并行/单次处理、状态化 Bundle、失败关闭和下游隔离。
- `Plan/Plan25.md`：事故学习闭环一等子系统的领域模型、状态机、分批实施、Fault Catalog、SLI/SLO 和关闭门禁。
- `Plan/Plan26.md`：产品中心纠偏、通用领域模型、Core/Plugin 边界、场景化 Acceptance 和 PROD-00～07 路线。
- `Plan/闭环覆盖范围.md`：事故闭环的已知覆盖范围、漏检概率模型、统计口径、主要盲区和阶段性目标。
- `OPTIMIZATION_BACKLOG.md`：优化批次、优先级、状态和验收标准。

## 验证命令

在 `/Users/donbblu/codex/multiAgent/demo` 执行：

```bash
python3 -m unittest discover -s tests -q
PYTHONPYCACHEPREFIX=/private/tmp/multiagent-pycache python3 -m compileall -q coding_workflow tests
```

真实浏览器测试需要先安装 Chromium，或通过 `VISIONFORGE_BROWSER_EXECUTABLE` 指定 Runtime 管理的 Chrome/Chromium，再设置 `VISIONFORGE_E2E=1` 执行 `test_visionforge_browser.py`。

在仓库根目录执行：

```bash
git diff --check
git status --short
```

## Git 基线

- 仓库：`/Users/donbblu/codex/multiAgent`
- 分支：`codex/multimodal-coding-mvp`
- 远端：`git@github.com:donbblu/MultiAgent.git`
- 当前基线提交：`ab1ecd8 chore: archive daily progress 2026-08-22`
- `.env`、`.runtime/`、`.runs/`、运行输出和 `.DS_Store` 不得提交。

## 安全提醒

- 不读取、打印或提交 `.env` 和 API Key。
- 不让模型生成的路径绕过 ProjectWorkspace 与 PatchIntegrator。
- 不用模型记忆覆盖权限、安全策略、验收条件或状态机。
- 不展示或持久化 Agent 的原始思维过程，只记录摘要、事件、结果和证据。
