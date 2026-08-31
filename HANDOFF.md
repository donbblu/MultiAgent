# 交互式多模态 Multi-Agent Harness 项目交接

## 使用方式

新任务先阅读本文，再检查 `git status`、最近提交和本文件列出的关键代码。代码、测试和 Git 是事实来源；本文用于恢复当前方向，不替代代码检查。

新窗口直接复制以下指令：

```text
请在 /Users/donbblu/codex/multiAgent 继续项目。
先读取 HANDOFF.md 顶部“新窗口接续摘要”、Plan/Plan30.md、
Plan/Plan29.md 顶部的产品口径纠正，以及 VerificationReports/STEP-LOG.md 的最新条目；
再检查 git status、HEAD 和相关代码。
以代码、测试和 Git 为事实来源，不需要旧聊天。

当前事实：MVP-AGENT-RUNTIME-01A～01D与MVP-CLOSE-01A～01D完成的是Runtime工程里程碑，
不是用户产品。本地候选`cbb35e3`通过干净检出Quickstart、33/184/579回归、compile/diff和独立Review；
这些证据继续作为底座回归，不要重复。当前仍没有任意任务Web入口、接入新Agent Runtime的真实模型协作、
用户介入和完整历史体验。

当前唯一产品主线是Plan30。PRODUCT-01C中的RoleAssignment、SEND_MESSAGE v1、显式Agent状态信封、SQLite持久重放与Backend Session私有绑定均已实现并通过回归，
SEND_MESSAGE已完成真实DeepSeek端到端复验；一跳Reviewer接收、ContextBundle、评审对象绑定、正文去重和幂等冲突检测均已完成。用户于2026-08-31把产品执行边界从API-only修订为CLI-first，并确认Codex CLI、ChatGPT订阅、分Role读写权限、每Agent/Thread隔离可恢复Session和脱敏公开输出。`CodexCliAgentExecutor` Fake合同、`SupervisedCodexCliTransport`和认证桥接均已转绿。两次早期真实read-only smoke的机械链路通过但Agent判断`CODEX_HOME`存在，canonical filter单变量修订无效；项目因此增加Runtime-owned固定布尔哨兵并把环境策略收紧为`inherit=none + 固定安全PATH`。用户随后只授权一次新Session真实复验：Runtime直接观察`codex_home_present=false`，模型一致，read-only、Workspace未修改、Session/shell/turn终态全部通过；耗时17912 ms，输入30671、缓存26112、输出153、reasoning输出32，无resume、retry或第二Agent。该具体认证环境泄漏缺口已关闭，但不等于PRODUCT-01B全部错误语义或生产安全认证完成。用户进一步冻结“协议无状态、业务状态显式化”：工具/Backend每次调用独立自描述，Runtime用Task/Snapshot/Permission/Artifact引用持有进度和恢复真相，CLI Session仅为可替换优化。

PRODUCT-01A必须覆盖：拓扑；通信协议；全局上下文与冗余控制；黑板/路由/发布订阅；角色、任务分解、
冲突消解；SOP/Debate/Master-Worker；死循环、终止条件、评估反馈、Voting/状态机/轮次与Token预算；
幻觉与幻觉死锁；上下文污染、重复讨论、非确定性复现，以及当前DAG不是LangGraph这一事实。

不得把本地工程候选冒称用户产品或生产系统；当前产品纵切只允许单用户、本地Web、一个主Thread、
固定核心角色和一个首选成熟Agent CLI Backend。DeepSeek/Qwen/Kimi API只保留为后续对照。不得扩张到分布式队列、Lease/Heartbeat、exactly-once、
生产Reaper、多租户、动态Agent市场、原生桌面端或生产认证。
不得触碰 demo/track.md、problems.md、prombles.md 的删除状态或 Plan/Plan28.md。
用户本轮授权的本地release-candidate分支/commit已完成；仍不授权push/tag/deploy。

产品纵切复用现有Runtime、Mailbox、Artifact和Runtime-owned Validator，不重写底座。
真实Agent CLI执行、网络或外部Provider调用仍需要执行当次授权；push/tag/deploy也未授权。
```

## 项目 Step Log（后续任务强制执行）

唯一项目级追加式过程账本是 [`VerificationReports/STEP-LOG.md`](VerificationReports/STEP-LOG.md)，权威规则见 [`Plan/Plan26.md`](Plan/Plan26.md) 的 `Append-only Project Step Log Protocol`。它记录每个逻辑步骤的 What / Why / Expected Effect / Actual Effect / Command / Result / Artifact Hash / Review / Status / Git Checkpoint；不记录私密推理，也不替代 VerificationReport、Harness Evolution、INC/RuntimeEvent、`KEEP` 或 Runtime Acceptance。

后续任务开始时必须读取末尾条目并检查 base HEAD/worktree；修改前追加 `PRE_REGISTER`，动作结束后在进入下一状态前追加 `ACTUAL`，收到独立审查后追加 `REVIEW`，里程碑追加 `CHECKPOINT`。失败候选不得删除或改写，只能通过新候选或 `CORRECTION + supersedes_entry_id` 续记；缺失原始证据必须写 `MISSING/UNKNOWN`，未提交只能写 `WORKTREE_ONLY/PENDING`。本轮正式序列从 `TRACE-20260826-001` 开始；本地候选内容commit为`cbb35e3`，最终证据由其后的文档commit记录。

`MVP-CLOSE-01` 与 `MVP-AGENT-RUNTIME-01` 按 Plan29 使用轻量例外：每个 01A～01D 批次只做批次级 PRE_REGISTER 和结束时 ACTUAL/CHECKPOINT；搜索、微小修正与普通定向复跑不再逐步建哈希/双审链。只有安全边界、真实外部副作用、Agent Runtime 01D和最终发布候选需要独立 Review；恢复 PROD/INC/安全认证时才回到上述严格协议。

## 新窗口接续摘要（2026-08-27，产品优先纠偏）

2026-08-27 用户进一步纠正：项目是在做用户产品，CLI 集成演示和工程发布候选只是技术底座，不能当成产品完成。现有 Agent Runtime、Mailbox、Handoff、Artifact/Validator 和回归证据全部保留，但主线改为 [`Plan30`](Plan/Plan30.md) 的产品可用纵切；此前 Plan29 的 `portfolio-complete` 只作为历史工程候选名称。

以下 `HandoffProposal` 是当前最小接续信息。它记录已经批准的优先级修订，但不创建 RouteEdge、Invocation、权限、任务完成、`KEEP` 或 Runtime Acceptance。

- **objective**：尽快交付一个用户可使用的本地 Multi-Agent Web 纵切，而不是继续扩建生产底座或美化 CLI 演示。
- **target_role**：通信合同、Codex CLI Executor、显式状态/Session恢复、精确ChangeSet用户批准门、离线产品服务和一次真实Planner+Reviewer双Invocation均已转绿；所有终态可跨Database/Service重建读取，精确相同Task重放零Agent调用，不同请求复用task ID会在Agent前拒绝。当前目标是接最小本地API；不自动开始第二CLI或扩大到自主进化。
- **public_rationale**：scripted/offline CLI只证明固定场景控制流；成熟Agent CLI是新的后端执行器，负责单Agent工具循环。用户仍通过Web使用产品，多Agent路由、状态、通信、收敛和验收继续由本项目Runtime控制。
- **completed_work**：既有Runtime里程碑、RoleAssignment、SEND_MESSAGE、Context、显式状态、SQLite执行重放、Backend Session恢复、真实恢复smoke和SQLite v11 ChangeApproval均继续有效。`LocalProductTaskService.run`已装配Planner→Runtime分配/Mailbox→Reviewer→Validator；SQLite v12以单事务append-only保存公开ProductTaskResult及存在时的Artifact和Verification。刷新相同请求先返回receipt，不重新执行Agent；request digest不一致返回`task_request_conflict`。Planner执行/协议、Assignment、recipient失败保存真实终态但不伪造Verification；Validator passed/failed/unknown保存完整证据。真实双Agent预检又补齐Planner动态Role ID与完整JSON Schema；获授权的两个read-only Codex新Session各调用一次，Runtime分配、消息、Artifact、Verification、重放和Workspace不变8项全通过，总耗时36375 ms、输入62423/缓存52224/输出350/reasoning49。当前服务6/6、smoke1/1、邻近36/36、Runtime185/185、普通全仓654/654（9 skip）和独立行为卡25/25通过。
- **evidence_refs**：底座候选与Plan29证据不变；RoleAssignment见TRACE-219起，SEND_MESSAGE及真实踩坑/修复见TRACE-224～232，一跳Context纵切见TRACE-233～234，显式状态与持久重放见TRACE-267～271，Backend Session绑定见TRACE-272起。当前`main@639e657`已包含至TRACE-287的Runtime、ChangeApproval和产品终态历史；TRACE-288真实双Agent smoke代码、测试与文档仍为WORKTREE_ONLY，未commit/push。
- **decisions_and_constraints**：用户于2026-08-31把API-only修订为CLI-first。成熟Agent CLI负责单Agent思考/工具loop；Runtime仍独占Planner分解、RoleAssignment、Message/Mailbox、Context、Handoff、终止、审计和Acceptance。首版只接一个CLI，并优先让核心Agent使用同CLI/模型、隔离Session以减少能力差异；CLI不得私下路由其他Agent。现有DeepSeek API和Provider自检保留为RawModelBackend对照，Qwen/Kimi不再阻塞第一版。CLI通过统一`AgentExecutor/FullAgentBackend`接入；凭据由CLI或OS凭据存储管理，不进入项目状态和公开输出。工具/Backend协议采用独立、自描述调用，业务状态由Runtime持久化并用Task/Snapshot/Permission/Artifact引用显式传递；供应商Session不能作为任务真相。产品禁止自主进化：Agent只能提交绑定精确digest的ChangeProposal；任何Prompt/Role/Profile/模型策略/权限/Skill/Runtime路由/验收规则或系统自身代码演进，必须逐次由用户检阅并明确批准，内容变化即重新批准，Agent投票、Validator或历史KEEP不能代替。Role与Agent动态解耦、Runtime中介受控网状拓扑、Planner语义拆分＋Runtime确定性分配、小步冻结＋垂直切片均保持不变。AgentInstance/Session、Mailbox、FIFO/并行和Runtime-owned Validator不重写。
- **assumptions_and_uncertainty**：01B固定单recipient、领取即推进游标，无ack/重试/崩溃重投；Message `turn_ref` 当前只有typed Scope引用，没有durable Turn存在性Store。原工作树仍保留四个未入候选的用户改动；候选未push/tag/deploy。
- **open_questions**：首个CLI、认证/付费、权限、Session命名与私有绑定、公开输出、禁止自主进化、显式状态、Session恢复、精确ChangeSet批准、产品服务全终态历史/重放和一次真实产品双Invocation均已确认。最小本地API/Web批准按钮、其余CLI取消语义、Developer链、角色冲突、Debate和完整收敛/评估仍未实现。
- **next_action**：按TDD为`LocalProductTaskService`接最小本机HTTP API：创建任务、读取结果/Artifact/Verification、重复请求与`task_request_conflict`稳定映射；先离线Fake Executor，不再次调用真实Codex。通过后再接Web状态投影和只由用户触发的批准/恢复按钮。
- **expected_output**：首个成熟CLI通过统一Executor接入Runtime；第一版产品能输入任意任务、运行至少两个隔离Session的真实Agent、显示持久消息/产物/验证并允许用户介入。
- **acceptance_criteria**：权威门槛见Plan30；scripted CLI回归不替代真实Agent CLI任意任务smoke，CLI退出0也不替代Runtime-owned语义验收。
- **required_capabilities**：离线实现只需仓库读写和本地测试。真实Agent CLI执行、网络、额度或费用需要用户当次授权；push/tag/deploy未授权。
- **resource_scope**：既有`recipient_runtime.py`、通信测试、DeepSeek脱敏smoke、Codex read-only smoke、真实人工恢复smoke和ChangeApproval门均保留。下一步只做本地产品服务链；不接第二CLI、不扩展分布式Mailbox/ACK，不应用任何未经用户批准的自身变更。继续保护`demo/track.md`、`problems.md`、`prombles.md`和`Plan/Plan28.md`。
- **budget_or_deadline**：从当前检查点完成第一版暂估还需5～9个专注小时，另留2～4小时风险缓冲。完整复刻Cat Café 00～15课与作业仍后置为`LEARNING-POST-01`。本次两个真实Invocation授权已经消费完毕，没有新的模型费用、额外CLI调用或发布授权。
- **post_product_reminder**：PRODUCT-01E准备完成时必须先提醒用户是否启动`LEARNING-POST-01`；未确认前不得自动开始或冒称课程已完成。该提醒按产品里程碑触发，不设置猜测日期的定时任务。
- **risks**：真实Codex已验证JSONL终态、Usage、read-only公开链路、默认拒绝工具环境、人工Session恢复和一个自包含双Agent任务，但这不证明任意复杂任务质量。真实有效resume、取消和其他错误分类尚未验证；一次性恢复尝试和ChangeSet application claim在进程崩溃后都会fail-closed为人工未决，不宣称exactly-once。`approve()`接API时必须只暴露给真实用户交互。终态历史已持久，但并发首次提交同一Task尚未验收；Mailbox仍无ack/retry/crash redelivery。

## 项目目标与定位

项目本体是一个可交互、可长期运行、单机优先并可演进到分布式的 **多模态 Multi-Agent Harness**。它以通用、可持久化的 **Multi-Agent Runtime** 为核心执行内核：Runtime 持有 Thread、Invocation、AgentSession、状态、生命周期、权限、预算、Event、恢复和 Acceptance 的权威控制；Harness 在其上组合角色与模型策略、任务拆分与路由、Context/Memory、工具、协作、评测、事故学习和 Skill。用户可以持续发送文本、图片、音频和视频，多个独立 Agent 可以并行判断、按依赖交接、使用受控工具并接受人工介入。Agent、模型、Prompt 和场景能力均可替换；Coding 与 VisionForge 是专业插件或纵向切片，不代表整个产品。当前代码尚无 `CodingPlugin`，Coding 仍是 Composition Root 和包内纵向切片。

项目不再定位为单纯的多 Agent 实验台，也不以接入模型数量或 Agent 数量作为成果。固定术语为“**Harness 是项目本体，Runtime 是执行内核，Plugin 是专业能力，Model 是可替换负载**”。目标系统包含三层：

1. **Durable Runtime Kernel**：Thread、Message、Invocation、AgentSession、Artifact/Event 真相源、生命周期、并发、权限、预算、恢复、审计和场景化 Acceptance。
2. **Harness 协作与控制层**：角色/模型策略、任务拆分与路由、Context/Memory、工具接入、DiscussionPolicy、Mailbox、结构化 Handoff、跨模型 Review、评测演进、事故学习和用户介入。
3. **插件与验证运营层**：Coding、VisionForge 及后续专业能力按插件接入；持续交互、协作、多模态和插件/工具任务分层评测，Coding 是首个代表，并通过故障注入、压力测试和事故复盘证明 Harness 及其 Runtime 内核的可靠性。

推荐的第一阶段生产边界是：自托管、单组织/单信任域、多个 Thread 和并发 Invocation、单机多进程或单集群部署。状态必须持久化，每个 Invocation 必须绑定输入快照、明确权限和预算；只有需要资源副作用的场景才绑定隔离 Workspace。敌对多租户、多区域高可用和大规模远程 Worker 属于后续演进目标，不应在没有容量或隔离证据时提前承诺。

VisionForge 的“参考图 → Vue 页面 → 浏览器功能验收 → VLM 视觉审查 → 自动修复”完整保留为 `visionforge:web_visual` 场景，不再代表整个产品。当前它是直接注册到 Core Registry 的独立 Scenario Plugin，并复用现有 Coding 能力；Plugin SPI 尚不支持插件嵌套。目标归属待 Coding 插件化时再按显式依赖协议迁移。

### Cat Café 的吸收方式

- 吸收 `Thread / Invocation / Session` 分离、持久 Invocation Queue、父子调用链、Session 隔离、统一事件、人工暂停点、独立 Review 和事故知识晋升。
- 不把聊天记录、自由 `@mention` 或长期模型 Session 作为任务状态和事实的真相源。
- Discovery 阶段允许不同模型先独立判断、再对等质疑；Delivery 阶段继续使用强类型 DAG、Artifact、Validator 和 Fix 闭环。
- Agent 只能提出 `HandoffProposal` 或图变更建议；Harness Policy 定义允许范围，由 Runtime Kernel 校验目标、权限、链深、预算、循环和资源冲突后，才能创建 `RouteEdge`、Invocation 或 Task。
- “无 Boss Agent”只表示内容判断可以对等，不表示没有中央控制面。Harness 是状态、权限、副作用和完成语义的逻辑所有者，Runtime Kernel 是这些规则唯一的强制执行与持久化边界。

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

- 完成 `Plan/Plan26.md` 的 Harness 产品定位与 Runtime Charter：交互式多模态 Multi-Agent Harness 是项目本体，通用 Multi-Agent Runtime 是执行内核；Coding 被确定为待插件化的专业纵向切片，VisionForge 保持独立 Scenario Plugin。本批只冻结文档边界，尚未实现持久 Thread、Agent 会话或 CodingPlugin。
- 完成 `PROD-01A` 通用领域协议与迁移骨架：新增独立 `runtime_domain`，冻结 Scope/Thread/Turn/Message、Agent Role/Profile/Instance/Session、Invocation/Attempt、Outcome/Acceptance 和 RuntimeEvent；Coding 只通过单向兼容适配器映射，尚未接入 Store、队列、旧 Executor 或 Web。
- 完成 `PROD-01B-1` SQLite 持久化地基：组件级 migration ledger、WAL/foreign-key 连接契约、显式 `RuntimeUnitOfWork`、事务状态机、故障回滚与事务逃逸门禁。
- 完成 `PROD-01B-2` 首个状态/审计纵切：concrete Thread current-state 与 append-only RuntimeEvent 原子提交、CAS、历史幂等、完整性读取、v1→v2 migration、进程退出/并发/腐败/旁路回归；完整 PROD-01B 尚未完成。
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
- 该历史里程碑当时有 213 个测试通过，另有 4 个真实浏览器类默认跳过；这不是当前测试总数，当前基线见后文 `pre-PROD-01B` 复跑记录。
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
- Thread/Task/Scenario 决定何时需要工作，Role 是 Worker 路由第一键，能力、输入协议、运行策略和可用性完成同 Role 内的 Backend/Worker 选择。`AgentInstance` 是 Thread 中长期可寻址的协作者身份，不等于 Model；Model/Backend 才是可替换执行负载。
- Harness 在逻辑上独占任务状态、权限、安全策略、Artifact 接纳和最终收敛规则；Runtime Kernel 负责强制执行、持久化和审计，Agent/Model/Plugin 均不能绕过。
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
- 当前子进程入口存在安全语义分裂：`ControlledCommandRunner` 已有显式最小环境、精确 argv、超时和进程组终止；`BrowserProcessRunner` 仍复制完整 `os.environ`；Legacy `ProjectWorkspace.run` 仍继承宿主环境且只有直接进程 timeout。三者都以宿主当前 UID/GID 运行，`cwd=Workspace` 也不构成文件系统沙箱；尚无每 Invocation 低权限身份、容器/cgroup 或等价隔离、默认断网、CPU/内存/PID/磁盘/输出限制、短期凭据和高风险人工审批。
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

### 当前版本安全边界与验收口径（`local_trusted_execution/v1`，已冻结）

状态：**边界与验收口径已冻结；统一实现已达到 `MOCK_STRUCTURAL_IMPLEMENTATION_REVIEWED`，已有两次双 Review 的真实 POSIX 零 target 窄证据、一次可信fixture `stdout_short` target开发smoke、一次Legacy production path和一次Guard-backed timeout cleanup。它们仍不是完整target adversarial、真实 Browser/E2E、最终 Review或安全验收。** 本节只定义当前个人本地版本；生产级隔离统一放在后文“后续展望”。

- **objective**：在不建设敌对代码沙箱的前提下，统一当前所有受控子进程的秘密最小化、命令门禁、Harness 路径门禁、deadline 和同一进程组生命周期语义，防止后续实现按不同 Runner 漂移。
- **target_role**：当前安全续接与提交审查者。默认禁用的POSIX target artifact已经构建、双审并只执行过一次`stdout_short`开发smoke；不得把这次窄证据当作重复执行许可或扩展到其他target/adversarial。本文不创建 RouteEdge、Invocation、CapabilityGrant、任务完成或 Runtime Acceptance。
- **public_rationale**：当前版本面向个人简历演示和可信本地任务。它必须如实降低已知风险，但不能用普通宿主子进程冒充低权限、文件系统或多租户沙箱。
- **completed_work**：已实现单一 `Popen` owner、五个冻结 Profile、global one-shot admission、Composition-owned exact-bool approval、Core/Legacy/VisionForge adapters、cleanup fence/quarantine/recovery、bounded/redacted output和autonomous dev lease；已复现的mock/structural blocker均有首红、修复、终绿和独立窄复审。POSIX fixture的稳定登记、ACK freshness与terminal-no-escape修复已在pure safety卡和独立复审中关闭；watchdog-only与arm→ACK→disarm各执行一次，均1/1成功、零target、terminal clean/join并经双Review和exact cleanup收口。之后默认禁用的target artifact完成pure/static审查，只执行一次可信fixture `stdout_short`开发smoke；Legacy `ProjectWorkspace` production path和Guard-backed timeout cleanup也各执行一次并留下窄证据。CLI报告、VisionForge preflight、168项聚焦回归和五批scope/hash checkpoint均已完成。当前仍为`INCONCLUSIVE / KEEP_NOT_ISSUED`；真实Browser/E2E、Renderer/browser binary契约、完整target adversarial和最终Review未完成，提交/推送由用户本轮另行授权并正在收口。
- **evidence_refs**：当前实现、测试结果、复审链与合规偏差见 [`VerificationReports/SEC-EXEC-01.md`](VerificationReports/SEC-EXEC-01.md) 4.3～4.6；实现链见 [`VerificationReports/STEP-LOG.md`](VerificationReports/STEP-LOG.md) `TRACE-20260826-043`～`059`，POSIX fixture修复与两档no-target真实证据见`TRACE-20260826-067`～`104`，单次`stdout_short` target见`TRACE-20260826-105`～`121`，Legacy production path见`TRACE-20260826-122`～`124`，五批CLI/timeout/VisionForge/回归/scope checkpoint见`TRACE-20260827-125`～`149`；核心入口见 `demo/coding_workflow/local_execution.py`、`local_execution_approval.py`、`command_validators.py`、`workspace.py` 与 `visionforge/browser.py`。
- **decisions_and_constraints**：
  - **适用信任域**：单用户、本人控制的本机；仓库、依赖、锁文件及 build/test/dev/browser 脚本均已人工确认可信；使用可丢弃 Workspace；Browser 只访问 loopback；执行进程不需要真实秘密、外部账号、非 loopback 网络或真实外部副作用。每次启动前由 Composition Root 记录不可由模型生成的 `trusted_local` 确认，并绑定 Workspace/输入摘要与 Profile digest；缺失、过期或摘要不匹配都不执行。
  - **最小可测试 admission seam**：包根只冻结一个 issuer 名称 `issue_trusted_local_confirmation(*, workspace_digest, input_digest, profile_digest, expires_at_monotonic)`；具体 token 类型与内部字段、内部模块及 Supervisor/Profile 类名均不冻结。`ControlledCommandRunner.run`、`ProjectWorkspace.run`、`BrowserProcessRunner.run/start_background` 只新增可缺省的 `trusted_local=` 关键字语义，使缺失值能到达 admission 并返回 `SANDBOX_REQUIRED`，而不是先被 Python `TypeError` 短路。当请求除缺少确认外已经完全合法时，该结构化拒绝的 `confirmation_request` 映射必须且只含 Runtime 实际计算的 `{workspace_digest,input_digest,profile_digest}`，三个值均为 64 位小写十六进制；拒绝对象仍可另带 `code/audit` 等结构化字段。Composition Root 只能用这份 challenge 调 issuer 后原样重试，测试不猜测或冻结 digest 的私有 preimage/序列化格式。非法 argv、越界路径、非 loopback 网络、秘密或真实副作用请求不得获得 confirmation challenge。issuer token 必须 opaque、不可由 bool/dict/JSON 或模型 payload 重建、绑定 Runtime 内部可信 provenance 且一次性消费；顺序或并发复用跨四入口合计最多一次 spawn。公开可导入不等于模型拥有签发权，Composition Root 只有在用户确认当前可信范围且三个 digest/期限来自这次 Runtime challenge 后才能调用 issuer。
  - **覆盖入口**：Core build/test/CLI Validator、VisionForge build/dev/browser 的前台与后台进程、Legacy `ProjectWorkspace.run`，以及不主动 `setsid`、double-fork 或 daemonize、始终留在同一受管进程组的子孙进程。仓库内不得保留绕过统一监督语义的其他 `subprocess.Popen/run` 执行入口。
  - **当前承诺**：独立宿主进程/进程组；从空映射构造的显式环境；受信任绝对 executable 和完整 argv 精确门禁；`shell=False`、`stdin=DEVNULL` 和非授权 FD 不继承；Harness 文件 API 的绝对路径、`..`、保留路径和 symlink 越界拒绝；版本化 deadline、TERM grace、输出上限；同组进程和受管端口/句柄的同步清理。
  - **环境 Profile**：公共环境名固定为 `PATH/LANG/LC_ALL/HOME/TMPDIR`，其中 `PATH` 来自 Runtime 冻结路径，`HOME/TMPDIR` 是本次运行私有目录；Python Profile 只额外允许 `PYTHONDONTWRITEBYTECODE/PYTHONUNBUFFERED`。模型/云密钥、proxy、`SSH_AUTH_SOCK`、`PYTHONPATH`、`NODE_OPTIONS`、`LD_*/DYLD_*` 和其他父环境变量一律不继承。工具确需新增非秘密变量时必须新建 Profile 版本并同步正常/负向测试，不能直接读取 `os.environ`。
  - **命令 Profile**：每个入口必须绑定版本化 Profile，包含受信任绝对 executable、完整 argv、Workspace cwd、单调 deadline、TERM grace 和输出上限；调用方只能收紧，不能放宽。任一字段缺失、Profile 漂移或 argv 不完全匹配，必须在 spawn 前 fail-closed。
  - **清理屏障**：success、failure、timeout、cancel、readiness failure、后台 stop 和异常全部进入同一 Finalizer：`TERM process group → 等待 Profile grace → KILL process group → wait/reap 直接子进程 → 核对 owned PID/PGID/port/handle`。Supervisor API 只有在核对完成后才能返回；无法证明清理完成时返回固定 `CLEANUP_FAILED`，不得返回业务成功，该 Workspace 在人工确认前不得启动新进程。失败结果必须提供 Runtime 生成的 `quarantine_id`、正整数 `quarantine_generation`、结构化 `cleanup_evidence` 与 `cleanup_evidence_digest`。解除隔离是只供 Composition Root/operator 使用的两阶段管理面：包根 `request_local_execution_recovery(*, quarantine_id)` 先重新核对全部 owned PID/PGID/port/handle；仍有资源或无法证明消失时保持隔离且不发 challenge，证明完成时返回 `recovery_request`，该映射必须且只含 `quarantine_id/quarantine_generation/workspace_digest/input_digest/profile_digest/cleanup_evidence_digest/recovery_evidence_digest`。调用方把其中三个通用 digest 原样交给既有 issuer 取得 opaque、限时、一次性 token，再调用包根 `recover_local_execution_quarantine(*, quarantine_id, recovery_confirmation)`；Runtime 必须在同一线性化临界区再次核对当前 owned resources、generation 与两份 evidence digest，过期、复用、并发资源再出现或 stale generation 均拒绝。Admission token 与 recovery token 必须做域分离，任何跨协议误用都 fail-closed。不得暴露无授权、无证据绑定的裸 clear 开关，测试也不得猜测两份 evidence 的 canonical preimage。
  - **明确不承诺**：独立低权限 UID/GID、容器/VM、OS 文件系统 containment、默认断网、CPU/内存/PID/磁盘硬配额、真实 Secret Broker、持久 fence/Reaper、Supervisor 崩溃恢复、恶意依赖防护，以及脱离进程组的 `setsid`/double-fork/daemon 或敌对 symlink/TOCTOU/hardlink 攻击。`cwd=Workspace` 和 Harness 路径测试不得被描述成 OS 沙箱。
  - **越界停止规则**：遇到陌生或可能恶意的仓库/依赖/脚本、多用户、真实秘密进入执行进程、非 loopback 网络、真实外部副作用或上述“不承诺”能力成为必需条件时，固定返回 `SANDBOX_REQUIRED` 并拒绝启动；不得回退为普通宿主执行。
- **frozen_profile_manifest**：以下是当前版本的权威默认值；每次实际运行还要把解析后的绝对 executable、完整 argv、cwd、环境 name/value-source、限制与输入摘要组成 canonical JSON 并记录 `profile_digest`。调用方可以缩短 deadline 或降低输出上限，其他改变都必须升级 `local_trusted_execution` 版本。

  | Profile | executable / argv 约束 | wall deadline | TERM grace | cleanup barrier 上限 | 持久化输出上限 |
  |---|---|---:|---:|---:|---:|
  | `core_validator` | Runtime 注册的绝对 executable + 完整冻结 argv；默认 Coding 命令为 `python3 -m unittest discover -s tests -v` | 30s | 1s | 5s | stdout/stderr 各 10,000 chars |
  | `legacy_workspace_verify` | 用户确认并绑定 Task/Input digest 的完整 argv；不得由模型修改 | 60s | 1s | 5s | stdout/stderr 各 10,000 chars |
  | `visionforge_build` | 绝对 `pnpm` + `run build` | 60s | 1s | 5s | stdout/stderr 各 10,000 chars |
  | `visionforge_dev` | 绝对 `pnpm` + `run dev --port 4173`；仅 loopback，readiness 15s，受管 lifetime 60s | 60s | 1s | 5s | server log 10,000 chars |
  | `visionforge_browser` | 绝对 `node` + 固定 runner；动态 URL/路径必须通过 loopback 与 Workspace/Runtime 路径校验 | 45s | 1s | 5s | stdout/stderr 各 10,000 chars |

  Runtime 冻结 PATH 为 `/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin`，但只允许先在受信任 Composition Root 解析并写入 Profile 的绝对 executable；Workspace 内同名文件不能参与解析。`HOME/TMPDIR` 每次运行唯一创建为 `0700`，子进程使用 `umask 077`，并在清理屏障内回收。`close_fds=True`，除 stdin/stdout/stderr 和 Profile 显式登记句柄外不得传递 FD。持久化文本超过上限时固定保留前后各一半，插入截断字符数标记，并同时保存原长度与原文 SHA-256，随后通过统一脱敏边界；本地可信版本不承诺对无限原始输出提供 OS 级内存配额。
- **assumptions_and_uncertainty**：当前支持口径固定为 macOS/POSIX 本地可信执行；模型/Provider Client 位于受信任控制面且不属于子进程 Profile。对模型远端取消和费用停止不作当前版本承诺。
- **open_questions**：ReferenceImageRenderer 采用预渲染 hash-pinned 资产还是新增明确 Profile；browser executable 如何进入 Profile-owned manifest 而不恢复任意 environment 注入。任何新增环境变量、命令入口、平台、网络、秘密或副作用都是显式版本变更，必须先修订 Profile、风险说明和验收，不得在实现中静默决定。
- **next_action**：完整SEC-EXEC闭包及远端checkpoint已经提交并推送；本安全认证路线现已暂缓。后续真实Browser/Renderer、full regression、更多POSIX target或最终安全Review只有在用户恢复SEC认证时才分别PRE_REGISTER；当前保持`INCONCLUSIVE / KEEP_NOT_ISSUED`并转向Plan29项目闭环。
- **expected_output**：版本化 Command/Environment Profile、单一监督语义、迁移后的全部前台/后台入口、冻结的正常路径 manifest、结构化拒绝/清理结果、负向和正常路径测试证据；不包含生产 Sandbox、真实密钥、真实外部副作用或未经授权的宿主配置修改。
- **acceptance_criteria**：
  - **A / 信任域 admission**：缺失 `trusted_local`、确认由模型产生、Workspace/输入摘要变化，或请求真实秘密、非 loopback 网络/副作用、陌生依赖时，必须在 spawn 前返回 `SANDBOX_REQUIRED`；spawn/PID/副作用计数均为 0。仅“其他检查全部合法、只缺确认”的拒绝可返回 Runtime 计算的三个 digest challenge；合法确认绑定该 challenge 后有正常执行对照，Workspace/input/profile 任一单独漂移、顺序复用或并发复用都仍为零 spawn。
  - **B / 环境、FD 与秘密**：对每个前台/后台入口在父环境注入唯一 sentinel，以及 fake provider key、proxy、SSH 和语言注入变量；在被 exec 的首个受控探针中枚举初始继承环境与可用 FD。未列入 Profile 的父环境 name/value 在探针、stdout/stderr、server log、Artifact、Event、SQLite 和下一轮模型输入 fixture 中命中数必须为 0；已列入的初始变量与 `profile_digest` 完全一致；除登记句柄外可继承 FD 为 0。`pnpm/node` 等受信任工具在 exec 后自行生成的内部变量必须通过正常路径 manifest 单独记录，不得误算为父环境继承，也不得包含父 sentinel 或秘密。每次 `HOME/TMPDIR` 路径唯一、权限符合 Profile，清理屏障返回后均不存在。
  - **C / 命令与 Profile**：表中每个精确登记 argv 可执行；相同 executable 下任一参数变化、空 registry、字段缺失、digest 漂移、超出上限、shell 元字符和 Workspace 内同名伪 executable 均在 spawn 前拒绝，spawn counter 与 Workspace 外 canary 不变。
  - **D / Workspace API**：Harness 文件 API 对绝对路径、`..`、保留路径和 symlink escape 全部拒绝，Workspace 外 canary 内容与哈希不变；Browser 的 cwd、log、spec、result 和 screenshot 路径也必须解析到获准 Workspace/Runtime 目录。对子进程只验证 cwd 正确，测试名、报告和 UI 不得宣称 OS containment。
  - **E / 生命周期与失败隔离**：可信 fixture 记录 PID、PGID、port、handle 和 marker，启动同组 child/grandchild 并忽略 TERM；分别触发 success、普通 nonzero failure、timeout、cancel、异常、background stop 和 readiness failure。Supervisor 必须在 Profile 的 5s cleanup barrier 内返回；返回时记录的 PID/PGID 不存在、端口关闭、marker 不再变化、直接子进程已 wait/reap。注入核对失败时必须返回带 quarantine ID/generation、`cleanup_evidence` 及 Runtime digest 的 `CLEANUP_FAILED`，同一 Workspace 的下一次启动在 spawn 前被拒绝；两阶段 recovery 在仍有资源、错误 ID、过期/复用 token 或 stale generation 时都不得解除，只有新鲜的独立 `recovery_evidence` 与相匹配的一次性确认被管理面核验后才可恢复，随后正常对照才能再次 spawn。
  - **F / 输出边界**：分别产生低于和超过上限的 stdout、stderr 与 server log；低于上限内容保持一致，超限内容只保留规定裁剪结果、原长度和摘要，secret sentinel 经统一脱敏后在所有下游 sink 明文命中为 0；不得把“持久化已限长”外推为 OS 内存配额。
  - **G / 正常对照**：上述 frozen manifest 就是正常路径基线；Core 默认验证、显式 Task 命令以及 VisionForge build/dev/browser 全部通过，结构化 Result/Artifact、timeout 和 error code 契约不变，不再使用无对象或依赖历史测试总数的“回归不退化”。
  - **H / 无旁路**：静态扫描与调用图审查证明受控范围不存在绕过统一监督语义的 `subprocess.Popen/run`；保留的非执行型例外必须在 manifest 逐项说明，不能静默豁免。新增进程入口但未注册 Profile 时，此门禁必须失败。
- **required_capabilities**：仓库读、受控 Patch、Python/浏览器本地测试和无秘密故障注入；本文不授予真实模型、生产秘密、外部网络、副作用或宿主提权权限。
- **resource_scope**：`demo/coding_workflow/command_validators.py`、`workspace.py`、`policy.py`、`agents.py`、`dag_runner.py`、`visionforge/browser.py`、`visionforge/web_runtime.py`、`demo/coding_agent_cli.py` 及其直接 Composition Root 和对应测试；范围外出现新的进程入口必须先变更本契约。
- **budget_or_deadline**：无自动开始时间和外部调用预算；用户已授权方案 A 的仓库内文档、红卡与后续受控实现顺序，但每个小批仍单独收口。未完成 A～H 前，不得再次把真实模型生成代码或候选代码交给当前 Legacy/Browser 执行入口。
- **risks**：环境 allowlist 可能暴露工具兼容性问题；同宿主 UID、无 OS containment、无网络/资源硬隔离和进程组逃逸仍是已接受的当前残余风险，因此本版本只能运行可信任务并必须展示 `local_trusted_execution/v1`，不能对外表述为生产安全沙箱。

## 学习与生产实践规则

- 每个批次必须由一个真实工作负载、历史事故或可复现的故障假设驱动，不能只因为某项技术流行而接入。
- 每项生产能力必须同时说明：领域契约、持久状态、失败语义、恢复/补偿方式、审计证据和验收 SLI/SLO。
- 从 PROD-01 起，每批至少包含一个与范围匹配的主动故障演练或确定性故障注入，例如 `kill -9`、重复投递、取消/完成竞态、供应商 429/半截响应、Session 串线、资源冲突、秘密泄漏或磁盘/预算耗尽。纯协议的 PROD-01A 用非法状态、跨 Scope、伪造 Acceptance、过期 lease/fence 和迟到结果负向构造验收；只有持久化或进程边界落地后才能执行对应的 `kill -9`，不得为未实现资源伪造演练证据。
- Fake Model 和单元测试证明 Runtime 按设计工作；真实交互、多模态输入和分场景 Workload 才能评价智能效果与可落地性，真实代码仓库只验证 Coding 能力，两类证据不得混为一谈。
- 每次真实事故必须形成：事件证据、影响与根因、修复、回归测试、预防规则/Skill 或自动门禁，以及 SLO 影响记录。
- 每个 `PROD-*` 批次必须同时规划、实现和验收对应的 `INC-*` 增量；后续新增或完善 Plan、Backlog、Learning Path 和 HANDOFF 内容时，必须包含事故检测、证据、止损、回放、回归和覆盖指标，不能把事故闭环推迟到功能全部完成之后补做。
- 允许实验得出“某个 Agent、Memory 策略、并发或反思机制没有收益”的结论；无法证明边际价值的复杂度应删除、降级为可选策略或继续暂缓。
- 开始生产批次前先冻结范围、信任边界、外发数据、模型/Prompt/协议版本、预算和停止条件；真实外部调用仍需要当次明确授权。

### Harness Evolution Protocol / 评测驱动演进规则（后续任务强制执行）

状态：**开发纪律与文档协议已冻结；尚无正式 Harness Evolution Experiment、自动 Evolver 或 Held-out 泛化结论。** 权威方法写入 `Plan/Plan26.md` 的 “Harness Evolution Protocol”，本节只保存历史生产证据边界。技术成熟度仍停在部分 `PROD-01B`，Agent Runtime 工程里程碑已完成；当前产品批次是Plan30的`PRODUCT-01A`。Harness Evolution、SEC最终认证和3B-2均不阻塞这条产品纵切。

固定术语边界：

- **Harness Evolution Protocol / 评测驱动演进协议**：本项目自定义的内部开发纪律，借鉴 Evo-Bench 的固定 Policy、隔离 Validation/Evaluation 和冻结候选思想；它不是外部产品，也不代表已经运行正式 Benchmark。
- **Evo-Bench**：RUCAIBox 发布的外部正式 Benchmark（<https://github.com/RUCAIBox/Evo-Bench>）。当前项目没有运行其 160-task Validation、448-task Evaluation、固定角色、20 iterations / 1,000 steps / 48h 等正式协议，不得宣称复现或取得其成绩。
- **`evo-hq/evo`**：独立的外部 autoresearch 编排工具（<https://github.com/evo-hq/evo>），提供 benchmark discovery、worktree 实验树、并行 subagent、Gate 和 dashboard。当前仓库未安装、未集成、未授权使用；未来若采用，只能作为外部候选实验执行器，不能拥有本项目的 Runtime 状态、权限、Acceptance、Incident、Memory 或 Skill 真相源。

- **objective**：把 Harness 开发从“想到功能就增加”改为 `Baseline → 失败证据 → 可证伪假设 → 单一 Mutation → Validation → Held-out → KEEP/ROLLBACK/INCONCLUSIVE`，使每项复杂度都能说明解决了什么真实问题、改善多少、付出什么代价。
- **target_role**：所有后续 Planner、Implementer、Reviewer 和 Eval Runtime；本文不创建 Evolver Invocation，不授予模型修改生产、读取保留集或签发 Acceptance 的权力。
- **public_rationale**：模型行为具有随机性，Harness 的 Prompt、路由、Context、Memory、协作、重试、停止、工具和验收机制又会相互影响；没有固定控制项、强 Baseline 和隔离保留集，就无法区分真实 Harness 收益、模型差异、题目过拟合和偶然成功。
- **completed_work**：已有 3 个版本化固定 Coding 任务、对 Policy Agent 隐藏的 Runtime 私有 Validator、任务校准、三种协作策略、统一预算与脚本/Fake Model 报告；这些只证明评测管线和控制流存在。当前没有对人工 Evolver 密封的 held-out，固定 Coding 三策略的真实模型效果对照仍未完成，3 个任务也不足以支持泛化结论。
- **evidence_refs**：`demo/coding_eval/v1/suite.json`、`demo/coding_workflow/coding_evaluation.py`、`coding_evaluation_runtime.py`、`coding_ablation.py`、`coding_model_workers.py`、`Plan/Plan16.md`～`Plan/Plan20.md` 及本文件“当前限制”。
- **decisions_and_constraints**：
  - 所有改变 Agent 行为、路由、协作拓扑、Prompt、Context、Memory、重试、停止、工具选择或 Acceptance 行为的修改，都必须绑定版本化实验记录；纯协议/文档批次可以写“不适用”，但必须说明没有可运行行为和由哪个后续批次验证。
  - 实验开始前冻结 workload/manifest hash、Policy Model/版本或预注册 assignment、Prompt/协议/策略版本、环境、权限、预算、工具、最终 EvalOracle/EvalAcceptancePolicy/HiddenValidator、随机种子、重复次数、主次指标、停止条件、排除规则、最小效果阈值、成本/延迟上限、不确定性方法、最小样本量、promotion rule 和 heldout query budget。运行后不得为改善结果更换分母、阈值、样本或删除失败 Trial；配置漂移必须产生新 experiment/version。
  - 每次实验预注册 `mutation_axis`，一次只检验一个主要机制。纯 Harness 因果实验固定 per-role Backend/Model manifest；模型或路由实验预注册 baseline/candidate assignment，不能表述为“模型不变的 Harness 收益”。同时改变模型、Prompt、拓扑、Context、预算或内部反馈 Validator 时必须拆分或做消融。
  - 被测 Harness 的内部 Prompt、路由、Context、协作、重试、停止、internal acceptance/gating、反馈策略或内部 Validator 可以作为白名单 Mutation，但不能兼任最终 Oracle。最终 EvalOracle/EvalAcceptancePolicy/HiddenValidator、安全/权限硬边界、预算、计分与完整分母由独立 Eval Runtime 冻结，不可演化。
  - 数据固定分为 development/calibration、validation 和 sealed held-out。默认每个内部 Harness Evolution Experiment 只允许对一个最终冻结候选查询一次 held-out；任何逐题或聚合结果暴露后，该 cohort 对后续调参即退役。Policy Agent、Evolver 与 Eval 使用分离 principal，涉及 Agent 连续性时还要使用不同 AgentSession，并审计 suite/version、访问者、查询次数和退役原因；泄漏、反复窥视或按 held-out 调参会使本轮结论 `INVALID`。
  - Agent 行为、智能效果和可泛化收益声明必须有隔离 Held-out。事务、状态机、权限等确定性正确性变更若不主张统计泛化，可以把 Held-out 标为“不适用”，但必须使用独立冻结的故障矩阵、正常对照和回归证明声明范围，且不能外推为模型或产品效果提升。
  - 独立 Eval Runtime 独占报告生成/冻结与验收结论权；当前没有加密签名机制。Evolver、Policy Agent、Worker 和被测 Harness 只能提交 ChangeProposal、Artifact 与 Evidence；`unknown != accepted`，超时、解析失败、缺失 usage 和异常退出按预注册规则进入完整分母，不能静默丢弃。
  - 安全硬门禁采用字典序，不与效果指标加权抵消：`false accepted=0`、跨 Scope/Thread/Session 污染=0、未授权或重复副作用=0、cancel/fence 后迟到结果接纳=0、预算硬限制突破=0、评测泄漏/篡改=0。通过后再按预注册 promotion rule 比较 safe acceptance、恢复率、Token/费用、延迟、人工介入和 Generalization Gap；未达到最小样本量时展示逐 Trial 分布，不报告 p95。
  - 报告保留全部已启动 Trial、失败与缺失数据、重试、配置 hash 和原始 Evidence 引用，并区分绝对值、相对 Baseline 差值及不确定性。只展示最佳 Run、用 pass@k 掩盖不稳定、把脚本/Fake Model 当真实收益或把 Plugin 指标外推 Core 都属于无效结论。
  - 只改善单一 fixture 的规则留在对应 Scenario/Plugin。新复杂度若没有 held-out 边际收益，或收益不足以覆盖 Token、延迟、误拦截和维护成本，结论必须是回滚、删除、降为可选策略或继续调查，不能用 Agent 数、模型数、代码量或测试数代替效果证据。
  - 未严格复现官方 Evo-Bench 的任务、角色、轮次、预算、隔离与计分协议时，只能称为“内部 Harness Evolution Experiment/Pilot”或“评测驱动演进实验”，不能使用 `Evo-style`、`Evo Pilot` 等容易混淆的简称，也不能宣称完成官方 Evo-Bench 或取得其榜单成绩。
- **演进层级**：
  - `L1 人工评测驱动演进`：当前默认方法；人分析失败、提出假设和修改候选，独立验证边界执行评测。当前尚无通用独立 Eval Runtime，现阶段证据只能标为 Verification，不能冒充 Runtime `AcceptanceRecord`。
  - `L2 Agent 辅助评测驱动演进`：外部 Agent 的离线候选可先用版本化 Bundle 和人工隔离；作为 Harness 一等能力时，其持久实验索引与受控执行边界仍依赖 PROD-01B～04 与 INC-03。Agent 只能提交 ChangeProposal/候选 Patch，不能修改最终 Scorer、权限或生产配置；候选依次经过静态/单元门禁、Offline Eval、独立 Review、Shadow、人工批准和可回滚 Canary。
  - `L3 生产自主 Harness 演进`：自动修改并晋升生产 Harness；当前非目标。INC-03 提供发布验证和 Shadow/Canary/Rollback，INC-04 提供 Learning/Guardrail 审批与退役，INC-05 提供运营和长期复发评价；全部成熟后仍需重新立项。
- **assumptions_and_uncertainty**：当前离线资产可以支持 L1 的管线 smoke 与小规模实验，但样本量、真实模型重复运行和跨任务 Held-out 尚未冻结；没有这些证据前，不能声称 Multi-Agent、Memory、Reviewer 或任何 Harness 版本更优。
- **open_questions**：第一次真实 Harness Evolution Pilot 的任务数量、模型、调用预算、重复次数和 Held-out cohort 必须在用户明确授权真实调用时单独预注册；不得从历史示例数字反推。
- **next_action**：保持既有 KEEP/INCONCLUSIVE 历史不变；当前不继续 SEC、3B-2 或 Harness Evolution 实验。产品主线以后文顶部的Plan30 CLI-first接续摘要为准；本段旧路线不再决定PRODUCT-01B执行方式。
- **expected_output**：每个适用批次包含一个版本化 Harness Evolution 实验小节或报告，引用可定位 Run/Trial/Evidence，明确示例、离线确定性结果、真实模型实测和生产观察；分别给出 `lifecycle_status=PROPOSED/RUNNING/FROZEN/COMPLETED/INVALID` 与 `decision=KEEP/ROLLBACK/INCONCLUSIVE`。
- **acceptance_criteria**：后续适用 Plan 至少包含失败/工作负载、Baseline、强对照、可证伪假设、单一 Mutation、固定 manifest、Validation/Held-out 隔离或确定性“不适用”依据、硬门禁、效果与代价、Incident/Regression 落点和决策/回滚；没有真实行为变化时显式写不适用及原因。
- **required_capabilities**：仓库读、受控候选 Patch、独立评测、版本化报告和与实验范围匹配的故障注入；真实模型、网络、媒体、外部仓库、秘密或副作用仍需当次授权。
- **resource_scope**：本规则约束所有 Harness/Runtime/Plugin 行为修改；实验记录只能引用已经实现或按 PROD/INC 路线建设的 RuntimeEvent、Incident、Artifact、Verification 和 Acceptance 权威对象，不新建平行事实库。某类真相源尚未落地时只能标注计划引用或 `N/A`，不得伪造对应记录或声称已由 Runtime 验收。
- **budget_or_deadline**：本文不授权模型调用或新增预算；当前作品集闭环优先于恢复 `PROD-01B` 顺序。
- **risks**：小样本、验证集泄漏、只挑最佳 Run、同时改变多个变量、脚本结果冒充真实收益和 Evolver 越权都会制造虚假改进；命中任一项时实验必须标为 `INVALID`，而不是修饰报告。

## 下一步

### 方向决议

2026-08-23 的实质决议是把产品从 Coding 专用链路泛化到长期、多场景 Multi-Agent 系统；当时文档曾简称为“多模态 Runtime”。2026-08-25 只做术语澄清，不改变领域模型与 PROD 顺序：统一表述为“构建一个可交互、可长期运行、支持多个独立 Agent 协作的多模态 Multi-Agent Harness，并以通用 Multi-Agent Runtime 作为执行内核。”Coding 是可加载的专业能力；当前 VisionForge 是独立的 `visionforge:web_visual` Scenario Plugin 并复用 Coding 能力。任何一个 Bug、仓库或网页测试都不能代表 Harness Core。

`Plan/Plan26.md` 已完成 `PROD-00` 产品 Charter，冻结 Scope、Thread、Turn/Outcome、Message、AgentInstance/AgentSession、Invocation、SessionBinding、RouteEdge、Artifact/Context、Capability 和场景化 Acceptance 的边界。批次 10A～13A 的代码和测试继续作为 Coding、多模态 Intake 与插件机制的已实现资产保留，不因产品纠偏删除，也不得再被描述成默认产品流程。

2026-08-27 起后续仍一次只推进一个小批次；`MVP-CLOSE-01A～01D`与`MVP-AGENT-RUNTIME-01A～01D`的工程证据、本地候选`cbb35e3`和最终独立Review全部保留。当前活动批次是Plan30的`PRODUCT-01A`，目标是先冻结用户产品的Agent通信与协作合同。`PROD-01A`、`PROD-01B-1`、`PROD-01B-2`、`PROD-01B-3A` 与 `PROD-01B-3B-1` 的完成事实保留；完整 `PROD-01B`、SEC最终认证和增强版 `01B-3B-2` 统一转为后续 Roadmap。

### PROD / INC 双轨联动规则（后续任务强制执行）

事故学习闭环是一等子系统，主计划见 `Plan/Plan25.md`，覆盖范围与漏检计算见 `Plan/闭环覆盖范围.md`。它不是 `PROD-07` 才补做的复盘模块，而是从 `PROD-00` 开始伴随每项生产能力演进的横切控制面。

当前事实：`INC-00` 的专项计划及产品泛化已随 `PROD-00` 完成文档冻结；`PROD-01A` 完成 RuntimeEvent 值协议和同步不变量地基，`PROD-01B-2` 完成 concrete Thread+RuntimeEvent 的首个持久原子纵切，`PROD-01B-3A` 完成 durable Outbox intent 原子三写，`PROD-01B-3B-1` 完成本地 claim/NACK/expiry-reclaim。它仍不是完整跨领域 Journal，也没有 Transport publish/ACK/Receipt、Detector、Incident Ledger、Replay 或自动学习，`INC-01`～`INC-05` 状态不因此提前完成。

后续任何任务只要新增、修改、拆分、实现或收口一个 `PROD-*` 批次，就必须在同一份 Plan、Backlog 更新和交接摘要中增加 `INC 联动` 小节，至少写明：

1. 对应 `INC-*` 阶段、当前状态、前置依赖和本批只完成的增量；
2. 新增或受影响的 `RuntimeEvent`、同步不变量和异步 Detector；
3. IncidentSignal、Evidence Bundle、脱敏、审计和证据定位方式；
4. 自动止损、人工批准、恢复和回滚边界；
5. ReplaySpec、Fault Injection、事故负向用例与正常路径对照；
6. Regression、Policy、Validator、Adapter、Runbook、Skill 或 Memory 的正确修复落点；
7. detected、prevented、missed、escaped、false-positive、recurrence、MTTD、MTTC 和 MTTR 中本批适用的指标；
8. `INC-*` 状态是否变化、剩余缺口，以及需要同步更新的 `Plan/Plan25.md`、`Plan/闭环覆盖范围.md`、`OPTIMIZATION_BACKLOG.md`、`LEARNING_PATH.md` 和 `HANDOFF.md`。
9. 对应 `VerificationReports/PROD-*.md` 必须同步记录受测版本/文件哈希、环境、精确命令、通过/失败/错误/跳过、故障注入/并发、真实缺陷、修复与回归位置、未覆盖风险、独立 Review 和 `KEEP/ROLLBACK/INCONCLUSIVE`。缺失或哈希不匹配时不得收口为 `已完成/KEEP`。

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

### 后续展望：任务专用子 Agent 与生产级 Invocation 回收规则

本节描述 `PROD-01C/02/03/04` 的目标边界，不属于 `local_trusted_execution/v1` 的实现或验收声明。

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

### PROD-00：Harness 产品定位与 Runtime Charter

状态：**已完成（仅文档和协议冻结，Runtime 行为未修改）**。

验收物是 `Plan/Plan26.md` 及本次同步的 HANDOFF、Backlog、Learning Path 和事故计划。第二个真实代码仓库已移到目标 Coding Plugin Canary/dogfood，不再是 Harness Charter 的完成条件。PROD-00 是唯一的 Charter 例外：以代码事实核对、跨文档一致性、`git diff --check` 和现有回归验收；主动故障演练从 PROD-01 开始。

### PROD-01：Durable Thread、Message、Invocation 与 Event Journal

状态：**01A、01B-1、01B-2、01B-3A 与 01B-3B-1 已完成，完整 01B 技术上仍在进行中、当前路线已暂缓**。Plan29 的 `MVP-AGENT-RUNTIME-01` 单机工程切片已经完成；现在执行Plan30的产品优先纵切，它不等于恢复或完成PROD批次。未来恢复生产 Roadmap 时，再按 01B-3B-2、01C～01E 逐批推进。

目标：建立第一版真正可恢复的交互控制面，并复用现有 Artifact、SQLite Snapshot、TaskGraph 与 ScenarioRuntime，不另起平行真相源。

未来生产 Roadmap 的执行顺序：

1. **PROD-01A 领域协议与迁移骨架（已完成）**：实现最小 `Scope/Thread/Turn/Message`、通用 `AgentProfile/Role`、`AgentInstance/AgentSession`、`Invocation/Attempt`、`Outcome`、`AcceptancePolicy/Record` 和 `RuntimeEvent`；冻结 Message/Artifact 边界与 Coding 兼容映射。Invocation 只包含 `input_refs + input_digest + policy_snapshot_ref + budget_reservation`，完整 Grant 和 ContextManifest 分别留给 PROD-03/05。
2. **PROD-01B 状态 Store、Journal 与 Outbox（进行中）**：SQLite 状态表是当前业务真相源，Journal 是不可变审计记录，Snapshot 是兼容恢复检查点；状态更新、Event、Outbox 与最小 BudgetLedger 预留/结算同事务提交。`PROD-01B-1` 已完成组件级版本化 Schema、Migration 与 RuntimeUnitOfWork；`PROD-01B-2` 已完成 concrete Thread 状态与 append-only RuntimeEvent 原子纵切；`PROD-01B-3A` 已完成 durable Outbox intent 原子三写；`01B-3B-1` 已完成本地 claim/NACK lifecycle；`01B-3B-2` Transport publish/ACK/Receipt 尚未开始，仍不得提前把整个 01B 标为已完成。
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
- RuntimeEvent 只保存小型、脱敏、深冻结 JSON 元数据和引用；在 PROD-01A 当时，append-only、唯一序号、授权写入和 Outbox 均未实现。此后 PROD-01B-2 已完成 concrete Thread 的 append-only/唯一序号持久纵切，PROD-01B-3A 已增加 durable Outbox intent，PROD-01B-3B-1 已增加本地 claim/NACK/expiry-reclaim；生产者授权与 Transport publish/ACK/Receipt 仍未完成。Coding 的 Role/Worker/Task/Artifact/Verification 通过单向适配器映射，旧 passed/verified/completed 都不会直接生成 accepted。

### PROD-01A / INC 联动与验收

- 对应阶段：`INC-01` 的协议前置；状态仍为待开始，只有 RuntimeEvent envelope 与同步不变量已具备。
- 风险与不变量：覆盖跨 Scope、错误 subject、伪造 Runtime Acceptance、非法执行/清理状态、过期 lease、stale/future fence、取消后迟到结果、幂等冲突和 Artifact 内容漂移；合法普通交互与 Coding 兼容映射同时作为正常对照。
- Evidence / 审计：证据是严格协议对象、内容哈希和 64 项定向测试；没有持久 Journal/Ledger，因此不能声称事件已经 append-only 或事故可以重启恢复。Event 值协议禁止正文、私密推理、凭据、未校验 `*_ref`、Prompt、Completion、原始媒体和 bytes；同一 Event 的更正必须使用新 event ID。
- 止损 / 恢复：本批仅在对象构造和 mutation admission 时 fail-closed，不产生外部副作用；事务补偿、重启恢复、Watchdog/Reaper 和人工事故权限分别由 PROD-01B/01C/01E 实现。
- Replay / Fault Injection：本批以确定性负向协议构造覆盖；SQLite 中断、重复投递、`kill -9`、锁竞争和孤儿恢复不适用，因为尚未实现 Store/进程边界，固定由 PROD-01B/01C 补齐。
- SLI/SLO：新增协议测试 64 项全部通过；默认全量共执行 277 项，其中 273 项通过、4 项真实浏览器测试按设计跳过、0 failure、0 error；已注册 Detector 数仍为 0，不能报告 detected/missed/MTTD/MTTR。
- `pre-PROD-01B` 开发基线复跑（2026-08-24）：这是 `VerificationReport`，不是 `AcceptanceRecord`。证据绑定 `HEAD=1f4dc13afb348d36b6e89ac09f1d85eccc960488`、Python 3.9.6；运行时工作区为 dirty，存在未提交文档/Backlog 修改，因此不能仅靠该 commit 精确复现全部工作区内容。从 `demo/` 执行 `python3 -m unittest discover -s tests -q`，结果为 277 项执行、273 项通过、4 项真实浏览器 E2E 按设计跳过、0 failure、0 error，耗时 20.590 秒；`PYTHONPYCACHEPREFIX=/private/tmp/multiagent-pycache python3 -m compileall -q coding_workflow tests` 与仓库根目录 `git diff --check` 均以退出码 0 通过且无输出。该记录只证明 PROD-01B 开发前的既有回归基线为绿，不证明 Store、Journal、Outbox、BudgetLedger、SQLite 原子性、并发或崩溃恢复已经实现或验收。
- `pre-PROD-01B-1` 新鲜基线（2026-08-25）：`VerificationReport` 绑定 `HEAD=12f315e103bb3fd4d8879feb9331bb605ea51a64` 和开始实现前的 dirty 文档工作区；`demo/coding_workflow` 与 `demo/tests` 尚无本切片代码差异。Python 3.9.6、SQLite 3.51.0；Runtime 定向 64 项全部通过。全量 `python3 -m unittest discover -s tests -q` 执行 277 项，273 项通过、4 项真实浏览器 E2E 按设计跳过、0 failure、0 error，耗时 20.768 秒；compileall 与工作树 `git diff HEAD --check` 通过。该记录只冻结 pre-slice 行为基线，不是 PROD-01B-1 或 Runtime Acceptance。
- 完成门禁：Python compileall、`git diff --check` 和全部默认回归通过；无需手动检验，没有模型、网络、媒体、外部仓库或数据库写入。

### PROD-01B-1 / INC 联动与验证摘要

01B-1/2/3 的详细版本、环境、命令、测试计数、故障/并发结果、真实缺陷、修复与回归位置、未覆盖风险、独立 Review 和决策已经集中到 [`VerificationReports/PROD-01B.md`](VerificationReports/PROD-01B.md)。本节只供快速交接；若数字或哈希冲突，以匹配受测文件哈希的独立报告为准。

- 状态：`PROD-01B-1` 已完成；完整 `PROD-01B` 继续为进行中。实现只包含 `runtime_kernel` 组件级 schema metadata/migration ledger、SQLite 连接契约与显式 `RuntimeUnitOfWork`，没有领域 State Store、Repository、Journal、Outbox、BudgetLedger、持久查询、Runtime-only Acceptance、Detector 或 Incident Store。
- 证据绑定：`HEAD=12f315e103bb3fd4d8879feb9331bb605ea51a64` 且工作区 dirty；实现 `demo/coding_workflow/runtime_persistence/sqlite.py` SHA-256 为 `52aaad07318ed17415bde9686ada2a6fd9b5effe29938beb271f199e7679ba59`，测试 `demo/tests/test_runtime_sqlite_uow.py` 为 `f1ef68b22517bca828f0a5063e297dfad545de345ef1a4db68682afc8416e13e`，包导出分别为 `55f3c756282514b0b97ada356462964aa350cec651feaa867da36688ce4c04bd` 与 `25af66784c95e1231ed7dfff574d3555c1a69a48d38e20765e5f0fec3efea880`。新增实现/测试尚未提交，后续 changeset 必须纳入 untracked 文件。
- 自动验证：Python 3.9.6、SQLite 3.51.0；`python3 -m unittest tests.test_runtime_sqlite_uow -q` 为 32/32（0.672 秒），Runtime pattern 为 96/96（0.658 秒），默认全量执行 309 项、305 通过、4 个真实浏览器 E2E 按设计跳过、0 failure/error（21.558 秒）；compileall 与 `git diff HEAD --check` 通过。两个独立 Review 分别为 `APPROVE` 与 `APPROVE WITH NOTES`；无需用户手动测试。
- 开发中真实发现并修复的 pre-release 缺陷包括：原始 connection/SQL commit 绕过、ALTER/DDL authorizer 绕过、iterator cursor 泄漏、`INSERT OR ROLLBACK` 让外层事务失效、rollback failure 被隐藏、WAL/Schema 检查时序和 REAL migration version 被强转。所有修复均有回归；这是本次“实现 → 挑战 → 红测/复现 → 修复 → 全量回归”的事故学习前置证据，不是生产 Incident 或 Detector 命中。
- INC：合成测试覆盖 migration/commit 前回滚、commit 前/后子进程退出的 none/all、重开、锁忙、Schema 漂移和事务逃逸；本切片没有写 RuntimeEvent、Journal、Replay 或 Incident Ledger。`INC-01` 仍待开始，Detector 数为 0，不报告 detected/missed/MTTD/MTTR。
- Harness Evolution：确定性轻量轨 `lifecycle_status=COMPLETED`、`decision=KEEP`；保留范围仅为 01B-1 事务底座。真实模型、Evolver、Validation/Held-out、query budget、样本量和统计效果均为 `N/A`，本记录是 Verification，不是 `AcceptanceRecord`。
- 后续 notes：01B-1 留下的通用 v2 migration runner 与实际 DDL shape 校验已由 01B-2 补齐；连接/路径建立失败的异常封装仍是非阻塞后续项。`PROD-01B-3A` 与 `01B-3B-1` 已完成；SEC最终认证与`01B-3B-2`已后置，当前按Plan29收口作品集闭环。

### PROD-01B-2 / INC 联动与验证摘要

- 状态：`PROD-01B-2` 已完成；完整 `PROD-01B` 继续为进行中。实现只覆盖 concrete `Thread` current-state、append-only `RuntimeEvent`、二者原子 mutation 与最小按 ID/aggregate 读取；不是任意 aggregate State Store，也没有 Outbox、Budget、Acceptance writer、其他领域 Repository、Detector、Incident Ledger、Replay、Web 或旧执行器接线。
- 实现：Schema v2 新增 `runtime_threads`/`runtime_events` 与连续 migration registry；Thread head 以 deferred composite FK 绑定 last Event，更新使用 version+sequence CAS。Event 采用 collision INSERT/UPDATE/DELETE trigger、`WITHOUT ROWID`、global event/idempotency uniqueness 和 scoped aggregate sequence；读回与 `verify_integrity()` 会复核 canonical digest、投影、当前 head、last Event 及反向 orphan 关系。
- 证据绑定：`HEAD=b864b20093f20077424fc81a564ecffecbf7ecb0` 且工作区 dirty；实现 SHA-256 为 `sqlite.py=4e052962d0047b90d0872136044ca4c5d80dadaad3c7e854910ab1bd145b497d`、`state_event.py=ba1a6974b067666b6eb12b7f41431861c8ea672645e301dbbd3d1f5628c26a2c`、`runtime_persistence/__init__.py=41b0fc9d1e5de90206370452d0891588acbb36d9908f67bd60a797e2e8867f41`、`coding_workflow/__init__.py=5a3ff4ff3358b5046aecb1a8cf90e92dd6b62ded314fe0d0c7851fe0eeb8180d`，测试为 `test_runtime_sqlite_uow.py=e1e07c5c47c33112f0f9a35ac73e188a8b6ad491f7390f37daa5327eca8416fd`、`test_runtime_thread_event_store.py=c1c2e700283b48e77c80ac15ab25da7a9d08bd4ae55eaa4c2f989f1bfc7b7f2c`。本切片尚未提交，不能只靠 HEAD 复现。
- 自动验证：Python 3.9.6、SQLite 3.51.0；EXPECTED_RED 先以缺少 01B-2 API 的导入错误失败。最终专项 68/68、Runtime 132/132、默认全量执行 345 项、341 通过、4 个既有真实浏览器 E2E 按设计跳过、0 failure/error；compileall（缓存定向 `/private/tmp`）与 `git diff --check` 通过。两个独立只读 Review 均为 `APPROVE`；无需用户手动测试，Review 不构成 Runtime Acceptance。
- 开发中由红测和独立挑战实际发现并修复：`INSERT OR REPLACE` 绕过 append-only trigger、隐式 rowid collision 改写历史、Store 与异库 UoW 误接、跨线程 abort 覆盖 typed error、损坏的历史 Event/当前 Thread head/最新 head Event 被幂等快路径误报成功，以及完整性扫描漏掉 orphan Thread Event。每个反例都有自动回归。
- INC：故障注入与负向用例只证明本切片对 orphan state/event、历史改写、duplicate/conflict、sequence/version drift、跨 Scope/跨数据库误绑和持久数据腐败能预防或 fail-closed；当前 Detector、IncidentSignal/Ledger 和 Replay 数仍为 0，`INC-01` 继续待开始，不报告 detected/missed/MTTD/MTTR。
- Harness Evolution：确定性轻量轨 `lifecycle_status=COMPLETED`、`decision=KEEP`；保留范围仅为 Thread+RuntimeEvent 原子纵切。真实模型、Evolver、Validation/Held-out、query budget、样本量和统计效果均为 `N/A`。
- 下一动作已执行并收口：`PROD-01B-3A` 的 durable Outbox intent 与 `01B-3B-1` 的本地 claim/NACK lifecycle 已完成；Transport publish/ACK/Receipt 仍留给 01B-3B-2。

### PROD-01B-3A / 3B-1 实现收口，3B-2 接续 Handoff

- 状态：`INV-PROD-01B-3-EVENT-OUTBOX-ATOMICITY-v1` 已冻结；`01B-3A=COMPLETED/KEEP`、`01B-3B-1=COMPLETED/KEEP`，完整 `01B-3=IN_PROGRESS/INCONCLUSIVE`。权威完整契约位于 `Plan/Plan26.md` 的 01B-3 小节。
- 验证报告：3A/3B-1 的最终哈希、分层门禁、真实产品缺陷、修复/回归、未覆盖风险和独立 Review 统一见 [`VerificationReports/PROD-01B.md`](VerificationReports/PROD-01B.md)；Review 不构成 Runtime Acceptance。
- 3A 已实现的原子边界：Schema v3 后每个新 Thread mutation 自动提交 Thread+RuntimeEvent+固定 `core:runtime_events` durable Outbox intent；不存在 Event-without-Outbox 公共入口。历史 v2 Event 原子迁移为终态 `LEGACY_SUPPRESSED`，不会因升级突然发布。
- 3B-1 已实现的所有权边界：短事务 claim 先 commit，claim generation/token/publisher 形成当前本地 ownership；支持 expiry-reclaim 和当前 owner NACK，同 aggregate 顺序与跨 Scope 隔离由共享 decoder/validator 强制。
- 3B-2 待实现的投递边界：Transport 只在 claim 事务关闭后调用，`OutboxPublishAck` 在新事务中写 immutable receipt 并 CAS 为 `PUBLISHED`。同 aggregate 按 sequence 发布，不提供跨 aggregate 全局顺序。
- 当前可靠性声明：3A+3B-1 只承诺 durable intent 与本地短事务 ownership，不承诺 Transport 已被调用或 at-least-once publication attempts。ACK 丢失重投、Consumer Inbox 去重、effectively-once acceptance 与“不称 exactly-once”的门禁均需 3B-2 及后续切片实现和验证。PublishAck 也不等于用户 Message DeliveryAck、工具副作用成功或 Acceptance。
- 当前生命周期：3A 创建 `generation=attempt=0` 的 `PENDING` 或 `LEGACY_SUPPRESSED`；3B-1 已实现 `PENDING↔CLAIMED`、expiry-reclaim、NACK retry 和 stale ownership 拒绝。ACK、Receipt 与 `PUBLISHED` 转换尚未实现；本地 Outbox ownership 也不等于 Invocation heartbeat、Watchdog、Finalizer、Reaper 或通用 fencing。
- INC / Harness Evolution：风险目录已包含 `event_without_outbox`、`outbox_without_event`、`unexpected_legacy_replay`、`publish_inside_business_transaction`、`stale_publish_ack`、重复尝试、顺序漂移和腐败。Detector/Incident/Replay 仍为 0，`INC-01` 待开始；3A 与 3B-1 确定性轻量轨均为 `COMPLETED/KEEP`，父切片仍为 `IN_PROGRESS/INCONCLUSIVE`，真实模型与 Held-out 为 `N/A`。
- 证据演进：首轮结构卡 hash=`6d8684486ced5a96c84275d6e0183f292bba7bae885ce5899bb325846e095826`，执行 3 项并准确暴露 v2/表缺失；Review 后的显式 Policy 红卡 hash=`8452ba5f2add07c3cd30e75b5c3ce26ceb941984d58f15e2ab5d20f5e3ab948a`，当时执行 5 项、5 failures。它们是历史 EXPECTED_RED，不是产品事故。实现首绿后独立挑战真实击穿并关闭 10 组产品缺陷；最终 adversarial 22/22、directed 73/73、Runtime 159/159、全量 372 项中 368 通过且 4 skip，compileall/diff-check 通过。
- 3B-1 验证摘要：权威报告保留 7 项 EXPECTED_RED 历史、首绿、4 组真实产品缺陷、跨进程/`os._exit` 恢复、最终门禁和独立 Review；决定仅为 `KEEP (3B-1 only)`，不构成 Runtime Acceptance。
- 历史窗口收口：`SEC-EXEC-01` 的统一 Profile/Admission/Supervisor、两档零target证据、target artifact、一次`stdout_short`开发smoke、一次Legacy production path、一次timeout cleanup和五批聚焦检查点均已完成并提交；决定仍是`INCONCLUSIVE / KEEP_NOT_ISSUED`。当时仅授权的62-path commit与普通push也已结束。当前不继续该认证或增强版`01B-3B-2`，而是按Plan29完成作品集闭环；未来若恢复真实Browser/Renderer、full regression、更多POSIX target、最终Review或3B-2，仍须分别预注册，不得冒领可靠发布或原地改写Schema v3/checksum。

必做故障演练：消息或副作用已提交但完成事件未写入时 `kill -9`、重复投递、错误 Thread/Session 绑定、取消与完成竞态、SQLite 锁竞争/磁盘异常和孤儿 Invocation 恢复。合法普通对话与当前 Coding 纵向切片都必须有对照，避免把 build/test 误设为所有 Thread 的完成条件。

### 后续展望：生产级演进顺序

1. **PROD-02 Backend v2、Session 与 Streaming**：Raw Model/Full Agent Backend、流式事件、硬取消、SessionBinding、usage/finish reason、错误分类、fallback 和 Canary。
2. **PROD-03 Capability、Tool Gateway 与执行隔离**：每 Invocation Grant、Secret Broker、隔离环境、默认断网、资源配额、高风险 Approval 和副作用审计。
3. **PROD-04 交互式协作控制面**：Mailbox、结构化 Handoff、并行/顺序协作、独立 Review、用户介入、循环终止，以及 Thread/Agent 泳道和讨论因果链。
4. **PROD-05 Context、共享记忆与多模态工作区**：Context Compiler/Manifest、版本/TTL/ACL、Session 压缩、共享记忆治理、检索评测和通用媒体附件链路。
5. **PROD-06 插件产品化与效果/容量验证**：Coding/VisionForge 插件入口、四类业务模式分层评测、背压、公平性、配额、压力与 soak。
6. **PROD-07 迁移与事故运营**：Schema/Prompt/Plugin/Model 迁移、golden trace replay、canary、回滚、备份恢复、Game Day 和运行手册。

#### 生产级执行隔离（不属于当前版本验收）

当系统开始接收陌生或可能恶意的仓库/依赖/脚本、处理真实秘密、访问非 loopback 网络或真实外部系统、操作重要 Workspace、支持多用户/多租户或对外暴露 Web 时，`local_trusted_execution/v1` 自动失效，必须先完成新的版本化生产 Sandbox 契约。

生产目标由 `PROD-01C/02/03` 分批实现：持久 TerminationIntent、Lease/Fencing、Watchdog、幂等 Finalizer/Reaper 和恢复；Backend/CLI/工具的流式或物理硬取消；每 Invocation 独立低权限 UID/GID 或 rootless 容器/等价隔离、只读输入与独立可写输出、OS 文件系统 containment、默认断网与受控 Egress、CPU/内存/PID/磁盘/输出配额、Secret Broker、短期 CapabilityGrant、高风险 Approval 和副作用审计。Linux OCI/cgroup v2、macOS/Windows Backend 以及路径隔离原语仍是后续 Plan 的实现决策，当前不预先宣称选型完成。

生产验收至少要求：执行身份不能读取 Runtime Store、Secret Store、其他 Workspace 或宿主保护 canary；无网络 Grant 时外部连接为 0；double-fork、`setsid`、忽略 SIGTERM 和 Supervisor 崩溃恢复演练后进程、端口、挂载、Lease 与临时资源残留为 0；cancel/fence 线性化点后的 Artifact、Event 和副作用接纳为 0；必需隔离原语不可用时 fail-closed。具体平台、时限、故障矩阵和证据格式必须在对应 PROD Plan 开始前冻结，不得引用当前本地测试作为生产验收证据。

### 后续提醒：Skill 候选箱

状态：**暂缓，不纳入当前 `MVP-CLOSE-01`**。未来讨论“后续展望”、Skill/Learning/Memory 治理、PROD-05 或 INC-04 时，必须提醒用户重新评估 PROD-05-SKILL-CANDIDATE-INBOX；未经用户确认不得让它插队当前项目闭环主线。

已确认的产品边界：

- Harness 从已持久的 RuntimeEvent、Acceptance/Verification、IncidentOccurrence 和人工纠正投影结构化观察，用稳定字段而非原始需求文本生成版本化指纹；现有 ScenarioRuntime._request_fingerprint 只用于恢复身份校验，不能用于相似需求聚类。
- 同一 Scope 内同类模式至少出现于 3 个独立真实任务或事故，且至少有 1 个带可验证成功证据的案例，才能生成 Skill 候选；同任务重试、Replay、Shadow 和故障注入不得重复计数。
- 系统只能自动产生 LearningItem(kind=skill, status=PROPOSED) 或其只读候选投影；模型和 Worker 不能自动批准、写入 Active Skill 注册表或改变 Runtime 行为。
- 候选必须先判断正确落点：硬禁止、权限、secret、预算、验收和终止循环归 Runtime/Policy/Validator；可确定重现缺陷归 Regression；Provider 差异归 Adapter；人工处置归 Runbook；只有重复出现且依赖判断的认知流程才归 Skill。
- 主入口是 API + Web 工作台的 Learning → Skill Candidates；CLI 只是可选运维/批处理适配器，不是用户使用该能力的前置条件。Web 至少展示指纹组成、独立任务计数、正反例与证据、人工纠正、建议落点、输入/输出/停止条件、评测计划、owner/版本/过期和完整决策历史。
- 人工“批准”只允许进入草案与 Offline Eval，之后仍须经过独立 Review、正反例、Shadow 和可退役的版本治理，不得从候选箱直接跳到 Active。

前置顺序不变：PROD-01B 先提供跨任务 Journal 与持久查询，PROD-01E/INC-01 提供 Observe-only 事故指纹和证据，PROD-05/INC-04 再实现 LearningStore、候选箱、人工审批、Offline/Shadow 评测、替代与退役。若为演示提前做只读统计，必须标记 Observe-only v0，不得宣称 INC-04 完成。

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
- `demo/coding_workflow/runtime_domain/events.py`：小型、不可变、Scope 绑定的 RuntimeEvent envelope；01B-2 已将 Thread Event 接入持久 Journal 纵切，01B-3A 已自动生成 durable Outbox intent；其他 aggregate、生产者授权与发布生命周期仍待后续。
- `demo/coding_workflow/runtime_persistence/sqlite.py`：组件级 SQLite migration、显式 UoW、受管 SQL 边界，以及 v3 Thread/Event/Outbox schema、显式 Policy 与真实 v2→v3 migration。
- `demo/coding_workflow/runtime_persistence/state_event.py`：Thread+append-only RuntimeEvent+Outbox durable intent 原子 mutation、exact retry、幂等冲突和完整性读取。
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
- `demo/tests/test_runtime_sqlite_uow.py`：组件 migration/UoW、released-v1→v2、故障回滚和受管 SQL 边界测试。
- `demo/tests/test_runtime_thread_event_store.py`：Thread/Event 原子性、幂等冲突、append-only 绕过、腐败拒绝、并发和进程退出测试。
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
- `Plan/Plan25.md`：事故学习闭环一等子系统的领域模型、状态机、分批实施、Fault Catalog、SLI/SLO、关闭门禁，以及与 Harness Evolution Protocol 的单一真相源边界。
- `Plan/Plan26.md`：Harness 产品定位、Runtime 通用领域模型、Core/Plugin 边界、场景化 Acceptance、Harness Evolution Protocol 和 PROD-00～07 路线。
- `Plan/闭环覆盖范围.md`：事故闭环的已知覆盖范围、漏检概率模型、统计口径、主要盲区和阶段性目标。
- `OPTIMIZATION_BACKLOG.md`：优化批次、优先级、状态和验收标准。

## 验证命令

在 `/Users/donbblu/codex/multiAgent/demo` 执行：

```bash
/usr/bin/env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 LC_ALL=C.UTF-8 HOME=/private/tmp TMPDIR=/private/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONWARNINGS=error PYTHONPYCACHEPREFIX=/private/tmp/multiagent-sec-cards /usr/bin/python3 -m unittest tests.test_local_trusted_execution_behavior_expected_red tests.test_local_trusted_execution_expected_red -q
/usr/bin/env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 LC_ALL=C.UTF-8 HOME=/private/tmp TMPDIR=/private/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPYCACHEPREFIX=/private/tmp/multiagent-sec-puremock /usr/bin/python3 -m unittest tests.test_local_execution_supervisor tests.test_local_execution_approval tests.test_visionforge_eval_composition tests.test_local_execution_posix_safety -q
PYTHONPYCACHEPREFIX=/private/tmp/multiagent-pycache /usr/bin/python3 -m compileall -q coding_workflow tests coding_agent_cli.py web_server.py visionforge_eval_run.py
rg -n 'subprocess\.(Popen|run)' . --glob '*.py' --glob '!tests/**'
```

上述 unittest 仍只是允许的 mock/structural 门禁。`TRACE-20260826-097/104`保存两次零target窄证据；随后target artifact已构建并完成pure/static审查，且只在`TRACE-20260826-119`～`121`执行过一次`stdout_short`开发smoke。该历史执行不是当前重跑许可；继续禁止full discovery、完整`tests.test_command_validators`、returned command tuple、其他target `Popen`、`success_orphan`、端口或崩溃workload。

真实 Browser E2E 也暂不得按历史 `VISIONFORGE_BROWSER_EXECUTABLE` environment 方式启动；现有 4 个 E2E fixture 与已收紧的 `workspace_root`/approval/environment 契约不匹配。必须先冻结 Profile-owned browser binary 与 Renderer 方案、迁移 fixture，再另行预注册真实 Browser 正常对照。

在仓库根目录执行：

```bash
git diff --check
git status --short
```

## Git 基线

- 仓库：`/Users/donbblu/codex/multiAgent`
- 分支：`main`（`codex/multimodal-coding-mvp` 与远端对应分支在本次同步前均指向同一提交）
- 远端：`git@github.com:donbblu/MultiAgent.git`
- 历史归档基线提交：`ab1ecd8 chore: archive daily progress 2026-08-22`
- 当前 3B-1 代码 clean content checkpoint：`HEAD=f66e71e02c206dd361f18f58f669824ae7de6cab`（`feat: add outbox claim lifecycle`），生产实现、公开导出、测试与收口文档均已提交并推送。当前工作树存在后续文档与红卡修改，精确状态以 `git status` 为准；01B-2/3A/3B-1 的历史 EXPECTED_RED、最终门禁、缺陷、文件哈希和独立 Review 继续以 [`VerificationReports/PROD-01B.md`](VerificationReports/PROD-01B.md) 为权威记录，不因 post-commit checkpoint 被倒填或改写。
- `.env`、`.runtime/`、`.runs/`、运行输出和 `.DS_Store` 不得提交。

## 安全提醒

- 不读取、打印或提交 `.env` 和 API Key。
- 不让模型生成的路径绕过 ProjectWorkspace 与 PatchIntegrator。
- 不用模型记忆覆盖权限、安全策略、验收条件或状态机。
- 不展示或持久化 Agent 的原始思维过程，只记录摘要、事件、结果和证据。
