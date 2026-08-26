# 交互式多模态 Multi-Agent Harness：生产演进与事故驱动学习路线

## 1. 学习目标

本项目要学习和验证的不是“如何让多个 Agent 修一个 Bug”，而是如何构建一个以通用 Multi-Agent Runtime 为执行内核、可长期交互、可恢复、可审计的多模态 Multi-Agent Harness，使多个独立 Agent 能安全地并行、交接、使用工具和接受用户介入，并让 Coding 等专业能力以插件形式接入。

学习与开发同步进行。每一批只解决一个可复现问题，留下协议、自动化测试、运行证据、失败语义、事故用例和已知限制。不能用 Demo 成功、模型自评或测试数量代替生产结论。

## 2. 核心概念地图

| 对象 | 负责什么 | 不负责什么 |
|---|---|---|
| Scope | 权限、保留策略和数据隔离的最上层边界 | 强迫所有交互绑定 Project/Workspace |
| Thread | 用户可见的长期协作空间、参与者和消息顺序 | 单次模型调用或某个代码任务 |
| Turn / Outcome | 一轮交互边界及其阶段结果 | 自动关闭长期 Thread |
| Message | 人、Agent 或 Runtime 的可见表达与 Artifact 引用 | 原始思维过程和隐式控制命令 |
| AgentProfile | Role、Backend、能力、上下文、输出协议和预算 | 持久运行状态 |
| AgentInstance | Thread 中长期可寻址的 Agent 身份和 Mailbox | 永久占用一个模型进程 |
| AgentSession | Runtime 自有的 Thread-Agent 连续性、游标和摘要 | 供应商私有 Session 或业务事实 |
| SessionBinding | AgentSession 到供应商 Session 的可替换映射 | Runtime 状态真相源 |
| Invocation / Attempt | 一次受控执行及其重试 | 整个 Thread 生命周期 |
| Task / ScenarioRun | 需要明确交付和验收的可选工作 | 所有普通对话的强制容器 |
| RouteEdge / HandoffProposal | 经 Runtime 接纳的交接关系 | 自由文本 mention |
| Artifact | 媒体、文档、代码、工具结果和结构化证据 | 共享可变对象 |
| ContextBundle / ContextManifest | 本次调用看到了什么、没看到什么及原因 | 无边界复制全部历史 |
| CapabilityGrant / Approval | 本次调用可做什么及高风险人工授权 | Agent 自行扩大权限 |
| AcceptancePolicy / AcceptanceRecord | 某个 Turn/Task/Scenario/外部动作需要哪些证据，以及 Runtime 的实际判定 | 模型的一句“完成了” |
| RuntimeEvent / Incident | 审计、恢复、检测、回放和学习 | 模型私有推理记录 |
| Plugin | Coding、VisionForge 等专业协议、工具和验收 | 修改 Core 不变量 |

最重要的边界是：Agent 提议 Message、Artifact、Handoff 或工具动作；Runtime 校验身份、协议、权限、预算、版本和依赖；受控 Gateway 执行副作用；Runtime 记录事件和证据；最后由场景 AcceptancePolicy 决定接受、继续、阻断或等待用户。

**AgentClaim 不等于 RuntimeAcceptance，unknown 不等于 accepted。** 普通对话、协作分析、多模态理解、Coding 和外部副作用可以有不同证据，但都不能让模型把推测直接登记成事实。

## 3. 当前真实成熟度

当前代码已经有可复用基础：TaskGraph、ScenarioRuntime、WorkerRegistry、Role-first 多 Worker 路由、ArtifactStore、Claim/Verification、SQLite Snapshot、插件注册、受控命令、Patch/Workspace、分层 Memory、多模态 Evidence 和固定 Coding/VisionForge 回归。PROD-01A 还新增了独立的通用 Runtime 领域协议：Scope/Thread/Turn/Message、Agent Role/Profile/Instance/Session、Invocation/Attempt、Outcome/Acceptance 和 RuntimeEvent，并用单向适配器兼容现有 Coding 对象。

当前这些领域对象仍有大量只停留在协议层；`PROD-01B-1` 已补上组件级 SQLite Schema/Migration/RuntimeUnitOfWork，`PROD-01B-2` 已实现 concrete Thread current-state + append-only RuntimeEvent 的首个持久原子纵切，`PROD-01B-3A` 已实现 durable Outbox intent 原子三写，`PROD-01B-3B-1` 已实现本地 claim/NACK/expiry-reclaim。项目仍没有权威持久 Message、长期 AgentInstance、独立 AgentSession、完整跨领域 Journal/Outbox Transport publish/ACK/Receipt、durable Invocation queue、真实 Agent 讨论与收敛因果链、ContextManifest、硬取消和操作系统级隔离。一个纵切存在，不等于完整运行能力已经存在。

因此不再使用“L1 完成、向 L2 过渡”这种单轴结论。成熟度按领域分别报告：交互持久性、协作协议、上下文正确性、权限隔离、多模态来源、插件兼容、事故恢复和智能效果。

## 4. 学习与开发原则

每个 PROD 批次都要回答：

1. 状态由谁拥有，写入的唯一入口是什么？
2. 输入快照、权限和预算如何冻结？
3. Agent 之间通过什么结构化对象交流？
4. 中断、重试、迟到结果和部分副作用如何处理？
5. Runtime 根据哪些证据接受结果？
6. 哪些故障可以同步阻断，哪些只能异步发现？
7. 如何回放，同时避免再次产生外部副作用？
8. 这项机制属于 Core 还是某个 Plugin？

以下要求适用于实现批次；纯 Charter 的 PROD-00 是唯一例外，以代码事实核对、跨文档一致性、`git diff --check` 和现有回归验收。故障注入必须匹配已实现边界：纯协议批次使用非法构造和 admission 负向用例，持久化或进程批次才使用 `kill -9`、锁竞争和恢复演练，不能为不存在的资源伪造证据。

每个实现批次必须留下：

- 一个版本化领域协议或迁移说明；
- 正常路径与至少一个负向事故用例；
- 自动化测试和可观察运行证据；
- 幂等、恢复、取消和兼容边界；
- 适用的 SLI/SLO 与仍未覆盖的风险；
- 对应的 INC 事件、Detector、Evidence 和回放增量。

## 5. 分批学习路线

### PROD-00：产品中心、领域语言与验收边界

状态：已完成文档冻结，见 Plan/Plan26.md。

需要能解释：为什么 Thread 不等于 Task，AgentSession 不等于供应商 Session，Invocation 不等于 Worker；为什么 Coding 的目标归属是插件而当前尚未实现 CodingPlugin；为什么 AcceptancePolicy 必须按场景声明；为什么第二个代码仓库不是 Harness Charter 的前置条件。

### PROD-01：持久交互与可恢复执行

严格按修订后的顺序学习：01A 已实现 Scope/Thread/Turn/Message、通用 AgentProfile/Role、AgentSession、Invocation、Outcome/Acceptance 和 Event 协议；01B 正在实施，其中 01B-1 已完成 SQLite Migration/UoW，01B-2 已完成 Thread+RuntimeEvent 原子提交，01B-3A 已完成 durable intent 原子三写，01B-3B-1 已完成本地 claim/NACK lifecycle。用户已批准先完成 `SEC-EXEC-01 local_trusted_execution/v1`，再实现增强版 01B-3B-2 Transport publish/ACK/Receipt；随后 01C 实现 durable Invocation，01D 接入现有 Task/Scenario/Coding 和 Web 查询，01E 完成 INC-01 并启动四组 INC-02 Shadow。

本阶段不再从 Plan 与 HANDOFF 拼接测试证据：`PROD-01B` 的测试设计、运行结果、真实缺陷、修复、回归、Review 与决策统一学习和维护在 [`VerificationReports/PROD-01B.md`](VerificationReports/PROD-01B.md)。

01A 是已完成的纯契约批次：Backend、Capability、Context、Mailbox 只保存不透明引用，没有实现投递、SessionBinding、模型调用、Gateway、Context Compiler、SQLite Store、调度、Web 或 Runtime 执行接入。验收证据为 64 项定向协议测试、277 项默认全量回归、compileall 和差异格式检查；4 项真实浏览器测试按设计跳过。

终局口径仍是状态表作为当前业务真相源、Journal 作为不可变审计、Snapshot 作为兼容恢复检查点，并让关键状态、Event 与 Outbox 同事务提交。01B-2 只证明 Thread+Event 纵切，01B-3A 只证明 durable intent、历史 suppress 与原子入箱，01B-3B-1 只证明本地短事务 ownership；Transport publication、ACK/Receipt 仍是 3B-2 的冻结目标。它们都不代表完整跨领域 Journal、Message DeliveryAck 或 exactly-once 已存在。PROD-01 的 Invocation 只冻结输入引用与摘要、策略快照引用和预算预留，不提前假装实现完整 ContextManifest 或 Tool Grant。

还要学会把“协作身份”“一次执行”和“机器资源”分开：用户长期看到的是 Thread，AgentInstance/AgentSession 保存可恢复身份与连续性；任务专用 Specialist 是同一 Thread 下的 ChildInvocation/Attempt，不是偷偷创建的新 Thread，也不是常驻模型进程。Invocation 同时有执行状态和清理状态；`SUCCEEDED` 只说明候选结果已经可靠保存，只有执行进入终态、清理达到 `REAPED`、活动 Grant/Lease/ChildInvocation 归零并拒绝旧 fencing token 后，执行域才真正 closed。

01A 只冻结 parent/child、双状态轴、终止原因、deadline、lease、fencing 和资源引用。当前插入的 `SEC-EXEC-01` 先完成可信本地子进程的版本化 Profile、进程组监督、同步清理屏障、最小环境和输出脱敏；它不建立持久 Invocation 或生产沙箱。01C 才实现持久取消、级联取消、Watchdog、幂等 Finalizer/Reaper 和孤儿恢复；02 仍负责 Provider/模型请求流、连接和硬取消；03 再补完整 CapabilityGrant、Secret、网络、容器/进程与生产隔离。进程内线程只能证明逻辑失权，可信本地进程监督也不能伪称 OS containment。

最小练习不是“修一个 Bug”，而是：

1. 创建 Thread 并提交用户 Message；
2. 创建一个 Agent Invocation，持久记录输入版本和权限；
3. Agent 产生可见 Message 或 Artifact；
4. 在任意提交边界模拟进程退出；
5. 重启后既不丢消息，也不重复副作用；
6. 同一 Core 同时允许普通交互和当前 Coding 纵向切片中的 Task。

### PROD-02：Backend、Session、Streaming 与取消

学习 Raw Model Backend 和 Full Agent Backend 的共同事件协议与差异；建立流式增量、finish reason、usage、provider error、Session 外部引用和端到端取消。

测试必须包含半截响应、429、超时、断连、取消后迟到结果、Session 丢失和模型版本漂移。Fallback 不能静默降低隐私、工具、结构化输出或验收要求。

### PROD-03：Capability、工具和执行隔离

学习能力令牌、短期 Grant、Secret Broker、默认断网、资源配额、审批、幂等副作用和审计。所有 Tool、Artifact、Memory、Workspace 与 Route 入口必须统一验权。

Coding 的路径与命令安全在这里作为插件级实例；Core 需要的是通用副作用模型，而不是把 ProjectWorkspace 强制给所有 Agent。

### PROD-04：多 Agent 协作与可解释收敛

学习 Actor/Mailbox、结构化 Handoff、并行与依赖、独立 Review、冲突、无进展检测、用户介入和讨论停止。

动态 Specialist 和多级 Handoff 只有在父子 Invocation、取消、fencing 和资源回收语义已经可验证后才能开放；否则“创建更多 Agent”只会放大孤儿执行、迟到副作用和资源泄漏。

页面需要能看到每个 Agent 的泳道、输入 Artifact、公开理由摘要、质疑、交接和 Runtime 决定，但不展示原始思维链。收敛不是多数投票，而是 AcceptancePolicy、证据、阻断项和用户决定的结果。

### PROD-05：Context、共享记忆与多模态工作区

学习 Context Compiler、Manifest、来源、版本、新鲜度、TTL、ACL、Token 预算、Session 压缩和共享记忆晋升。

任何 Context、Memory 和检索都必须先按 Scope fail-closed，再做 Thread、Project、Role 和相关性过滤；父子关系、Artifact 引用、Acceptance Evidence 和因果引用不得跨 Scope。

图片、音频和视频只在原始视觉或听觉语义不可替代时进入相应模型；感知结果转成带来源和不确定性的 Artifact 后，后续默认切回文本 Agent。需要专门评测“该给的上下文是否给了、不该给的是否泄漏、过期事实是否被拒绝”。

### PROD-06：插件、效果与容量

将持续交互、协作分析、多模态理解和插件/工具任务作为四组独立 Workload。Coding 是首个代表，且同时包含新功能、Bug 修复和行为保持重构，防止只对修 Bug 优化。

分别报告送达与恢复、来源正确性、事实错误、accepted rate、handoff 收益、人工介入、Token、费用和延迟。多 Agent 与强单 Agent 对照；一个场景的高分不能外推到其他场景。

### PROD-07：迁移与事故运营

学习 Schema、Prompt、Plugin、Model 和 Store 的兼容迁移，golden trace replay、canary、回滚、备份恢复、Game Day、Runbook 和人工接管。

事故知识只有在证据、RCA、回归、Shadow/Canary 和人工审批完成后，才能晋升为 Guardrail、Policy、Skill 或 Memory；不能让 Agent 复盘文字自动改写运行规则。

## 6. 四组固定验收练习

### 持续交互

在同一 Thread 多轮补充与改向，重启后恢复；取消请求及时生效；另一个 Thread 看不到其私有 Message、Session 和 Artifact。

### 协作收敛

两个 Agent 先独立分析，再通过结构化 challenge/handoff 汇合。最终报告必须说明采纳了什么、拒绝了什么、尚未解决什么，以及对应证据；预算耗尽时确定性停止。

### 多模态交接

媒体 Artifact 与 Thread、Message、区域或时间片正确绑定。感知 Agent 保留不确定性，文本 Agent 只读取被授权的结构化 Evidence；错误媒体或过期结果必须被拒绝。

### 插件/工具任务（Coding 是首个代表）

同一 Runtime 处理一个新功能、一个 Bug 和一个行为保持重构。当前先复用 Coding 纵向切片；目标插件化后，Patch、构建、测试与 Review 仍由 Coding AcceptancePolicy 决定，不能把这些门禁强加给普通交互。

## 7. AI 协作实践规则

AI 可以协助搜索代码、提出协议、实现局部改动、生成测试和总结运行证据，但项目负责人需要决定产品边界、信任模型、外发数据、真实供应商、预算、不可逆副作用和上线风险。

真实模型、媒体、网络、外部仓库或系统权限每次都需要与当前批次匹配的明确授权。不要读取或提交 .env；不要保存原始思维链；Evidence Bundle 默认引用受控 Message/Artifact、hash、时间窗和脱敏摘要，而不是复制完整对话或原始媒体。

一次批次完成时，应能用几句话回答：改了什么行为、用什么证据证明、故意没有改什么、可能怎样失败、出了事故如何发现和恢复、下一批是什么。

## 8. 评价标准

| 维度 | 评价问题 |
|---|---|
| 交互持久性 | 消息、状态和用户控制在崩溃恢复后是否正确 |
| 协作可解释性 | 能否追踪 Agent 的公开输入、交接、异议和收敛依据 |
| 上下文正确性 | 是否最小、相关、新鲜、授权且可解释 |
| 权限与隔离 | 是否阻止跨 Scope、Thread、AgentSession、SessionBinding、Artifact 或执行环境污染和越权副作用 |
| 多模态来源 | 输出是否绑定正确媒体、区域或时间和不确定性 |
| 验收正确性 | 是否严格执行场景 AcceptancePolicy，保持 false accepted 为 0 |
| 插件解耦 | Coding/VisionForge 能否替换或关闭而不改变 Core |
| 恢复与事故 | 是否能检测、止损、回放、回归并防止复发 |
| 智能收益 | 多 Agent 相对强单 Agent 是否有可统计的边际价值 |
| 成本与容量 | 延迟、Token、费用、队列和资源是否在预算内 |

路线的完成标准不是“Agent 数量更多”，而是：

> 用户可以在一个可恢复的 Thread 中与多个受控 Agent 长期协作；文本与媒体证据被正确路由；每次消息、交接、工具动作和接受决定都可追溯；Coding 等专业能力可以作为插件加入或替换，而不会反向定义 Core。
