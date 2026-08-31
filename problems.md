# 项目关键问题与当前答案

这不是聊天问题清单，而是项目开发过程中真正困难、并且改变了整体策略的问题复盘。每个问题都记录了为什么它重要、当前采用的答案、它对架构造成的影响，以及仍未完成的部分。

本文已吸收原拼写错误文件 `prombles.md` 中仍然有效且不重复的内容；此后只维护 `problems.md`。若本文与实现冲突，优先级为：**代码与测试 → `HANDOFF.md` / `Plan/Plan26.md` → 本文 → 旧聊天回答**。“已实现”表示有代码或测试证据，“已冻结”只表示方向已进入当前计划。

## 当前实现状态速查

| 范围 | 当前状态 | 不能误解为 |
|---|---|---|
| 交互式多模态 Multi-Agent Harness 产品方向 | 已冻结 | 通用产品入口和 Runtime 已完整落地 |
| PROD-01A 领域对象、不变量和 Coding 单向适配 | 已实现并有测试 | 已有持久队列、Journal 或恢复运行时 |
| VisionForge `visionforge:web_visual` | 已注册为独立 Scenario Plugin | 已嵌套在尚未完成的 CodingPlugin 中，或代表 Core |
| Coding 纵向切片 | 已实现，目标迁为插件 | CodingPlugin 已完成 |
| SQLite 事务底座 | PROD-01B-1 已完成组件 Schema/Migration/UoW | 权威 State Store、Journal、Outbox 或 BudgetLedger 已完成 |
| PROD-01B 权威 Store/Journal/Outbox/BudgetLedger | 继续进行；下一动作冻结 01B-2 | 事务底座已经等于完整持久事实链 |
| Durable claim/lease/heartbeat 与 Watchdog/Reaper | 待 PROD-01C | PROD-01A 的值协议已等于实际回收机制 |
| Backend v2、SessionBinding、客户端缓存治理 | 待 PROD-02 | 当前同步 ModelClient 已满足生产语义 |
| Mailbox、持久 Handoff、Agent 泳道 | 待 PROD-04 | 当前 Coding DAG 页面已是目标协作控制面 |
| ContextBundle/Manifest 与共享记忆治理 | 待 PROD-05 | 现有 Coding Memory 已是通用 Thread Context |

## 1. 项目到底在做什么

### 问题

项目最初从三个 Agent 的 Coding Demo 起步，随后加入 DAG、Memory、Web、多模态和模型接口。能力越来越多，但定位一度在“Coding Agent”“Multi-Agent Demo”“Harness”“AI Infra”之间摇摆。最大的困难不是继续增加功能，而是判断什么属于产品本体，什么只是验证本体的一条业务链路。

### 回答

当前项目定位为一个**可交互、可长期运行、单机优先的多模态 Multi-Agent Harness**。Harness 是项目本体；通用 Multi-Agent Runtime 是核心执行内核，负责 Thread、Message、Invocation、Session、Artifact、权限、预算、恢复、审计和场景化验收。角色/模型策略、Context/Memory、工具、协作、评测和事故学习属于 Harness 控制层；Agent、模型和业务场景均可替换。

Coding 仍然重要，但它不再定义 Core。现有 Coding 和 VisionForge 代码被保留为已经验证过的纵向切片，目标是逐步迁移成专业场景或插件，而不是为了“通用化”直接重写。

网页与 Bug 修复之所以经常被用作样例，是因为编译、单测、CLI 行为、DOM 和截图较容易提供确定性证据；它们只验证一条业务链，不能反向定义整个产品。最终分层评测还必须覆盖长期持续交互、多 Agent 协作分析、多模态理解以及浏览器/文档等工具任务。

VisionForge 当前是独立注册的 `visionforge:web_visual` Scenario Plugin，并复用现有 Coding 能力；现有 Plugin SPI 尚不支持插件嵌套，真正的 CodingPlugin 也未完成。UI Spec、视觉评分、P1/P2 问题和页面修复循环归 VisionForge 所有，Core 只保留多模态 Evidence、Artifact 引用、能力路由、场景化 Acceptance 和受控工具执行等通用机制。

### 策略影响与现状

这次纠偏避免了普通交互、多模态分析和其他工具任务被迫套用 build/test/Fixer 语义。新的产品边界已经在 [`Plan/Plan26.md`](Plan/Plan26.md) 和 [`HANDOFF.md`](HANDOFF.md) 冻结；但持久 Thread、通用 Agent Workspace 和真正的 CodingPlugin 仍未完成，所以现在是“方向和协议已调整，产品入口尚未完全迁移”。

## 2. Multi-Agent 不能只是把固定 Workflow 换成多个名字

### 问题

早期三个 Agent 更像固定节点：Planner 做完才轮到 Developer，Developer 做完再轮到 Tester。虽然进程里存在多个 Agent 名称，但缺少长期身份、独立判断、结构化交接和动态任务认领，因此利用率低，也很难说明这与普通 Workflow 有什么本质区别。

### 回答

项目将 **Role、Agent、Worker 和模型拆开**：Role 表示职责、权限和输入输出契约；AgentInstance 是 Thread 中可寻址的协作者身份；Worker 是一次可被路由的执行能力；模型只是 Worker 的一种 Backend。一个 Role 可以有多个 Worker，一个模型也可以承担多个 Role，二者不永久绑定。

Agent 只能提交 Artifact、Message 或 `HandoffProposal`。是否创建新的 RouteEdge、ChildInvocation 或 Task，必须由 Runtime 校验目标、权限、依赖、预算、链深、循环和资源冲突后决定。内容判断可以由多个 Agent 对等完成，但状态权、权限权和副作用权始终属于中央 Harness。

路由先用 Role 做职责和权限硬过滤，再检查 required capabilities、输入输出协议、principal separation、Runtime policy、可用性和预算，最后才按健康度、成本和延迟选择 Worker。没有合格 Worker 时必须返回结构化 `MISSING_CAPABILITY` 并进入 blocked/needs_input，不能用“最接近”的不合格模型兜底，也不能让模型用文字猜测缺失的工具结果。

### 策略影响与现状

这使项目仍然是 Multi-Agent，即使它当前部署在一台机器上；“单机”描述部署边界，不描述协作模型。Role-first Worker 路由和职责隔离已经实现，通用 AgentProfile/AgentInstance/AgentSession 协议也已建立；Mailbox、持久 Handoff、Agent 泳道和动态协作控制面仍属于后续 PROD-04。

## 3. 如何提高并行度，又不让任务互相破坏

### 问题

三个 Agent 经常只有一个在工作，说明固定流水线没有暴露真实并行性。但简单地让更多 Agent 同时运行，会带来文件覆盖、读取过期输入、重复副作用、验证对象不一致和合并顺序不确定等问题。提高利用率本身不是目标；降低关键路径延迟，同时保持结果可重复，才是目标。

### 回答

任务先被表达为带依赖、输入 Artifact、输出契约和资源范围的 DAG。Executor 只并发调度依赖已经满足、输入证据齐全且资源范围不冲突的 ready 节点。每个执行基于不可变输入快照，Worker 不直接修改共享项目，只提交候选 Artifact；共享写入统一经过 PatchIntegrator 和 Workspace 门禁。

可并行的是独立调查、不同文件或无冲突资源上的实现、静态分析和准备工作；需要共享 Workspace、依赖前序产物或产生外部副作用的步骤仍然串行。任务拆分还必须计算通信和模型调用成本，不能为了表面利用率把小任务无限切碎。

### 策略影响与现状

当前 TaskGraph、ready queue、Artifact 接纳、资源冲突检查、局部重试和集中 Patch 合并已经落地。这解决了 Coding 纵向切片中的大部分并行安全问题。下一步需要把同样的输入版本、lease、fencing 和资源租约语义放进持久 Invocation Store，才能跨进程、重启和长任务继续成立。

## 4. Task 的生命线如何做到确定、可恢复和可退出

### 问题

只用 `running/completed/failed` 无法描述真实长期任务。模型响应结束不代表资源已经释放，取消请求不代表子进程已经停止，重试还可能让旧 Attempt 的迟到结果覆盖新结果。若没有确定性生命周期，暂停、恢复、取消和故障重启都会变成偶然行为。

### 回答

Runtime 将一次工作拆成 Invocation 和 Attempt，并把**执行状态**与**清理状态**分开。Invocation 只有在执行进入终态、清理达到 `REAPED`、活动 Grant/Lease/ChildInvocation 为零，并且旧 Attempt 已被 fencing 后，才算真正 closed。

每次 Attempt 使用 deadline、lease、heartbeat、单调 fencing token 和幂等 mutation key。取消或超时先撤销新增副作用的资格，再级联取消子 Invocation，记录终态意图，终止 Backend/进程并释放资源。Finalizer 必须幂等，Watchdog/Reaper 负责回收没有完成清理的孤儿执行；旧 token、过期 lease 和取消后的迟到结果必须被确定性拒绝。

### 策略影响与现状

这套协议已经在 `runtime_domain` 中实现，并通过非法状态、跨 Scope、过期 lease、旧 fence、重复 mutation 和迟到结果等负向测试。PROD-01B-1 又完成了组件级 SQLite Migration/UnitOfWork 事务底座，但还没有 durable queue、权威 SQLite Invocation Store、Journal、真正的 heartbeat/Reaper 或 Backend 硬取消。完整 PROD-01B 仍在进行中，下一动作是先冻结 01B-2；01C 再把 claim/lease/heartbeat 与回收协议接成持久运行行为。

## 5. 谁可以宣布结果完成，Multi-Agent 何时停止

### 问题

如果让 Agent 自己说“测试通过”或让多个 Agent 投票，就可能出现循环自证。另一方面，如果没有停止条件，Planner、Reviewer 和 Fixer 会不断往返，消耗 Token 却没有增量。Fixer 有时不工作，也暴露了“失败、阻塞、未知和需要修复”之间没有清晰区分时，路由会不稳定。

### 回答

项目不使用多数投票作为收敛条件。每种交互或插件场景必须注册版本化 `AcceptancePolicy`，Runtime 根据绑定到正确 subject/version 的新鲜证据签发 `AcceptanceRecord`。Agent 和 Evaluator 只能提供 Evidence，不能签发 accepted。

必须区分三件事：`Invocation` 技术上结束、某个 `Outcome` 被接受、长期 `Thread` 被归档。Outcome 只有 `unknown / needs_input / accepted / rejected`；`continue` 只是保持 unknown 并继续调度，blocked/cancelled 属生命周期状态。

Fixer 也不是常驻必跑节点。它只应在独立 Validator 产生可修复的失败证据、策略允许重试、预算和轮数未耗尽时生成局部 FixTask；需求不清应进入 needs_input，能力缺失应 blocked，证据不足应保持 unknown，而不是盲目调用 Fixer。

### 策略影响与现状

Coding 路径已经有动态 FixTask、受影响测试和最终完整门禁，通用 Acceptance 协议也已实现。仍需把 Acceptance 的 Runtime 独占写入权放进 PROD-01B 的事务 Store，并为不同业务场景建立独立、可校准的收敛策略。

## 6. Memory 不是把所有聊天永久保存

### 问题

最初讨论了短期、长期、感知、实体和知识图谱等多种记忆，但如果没有边界，系统会把模型推测、过期事实、私有 Session 和错误检索一起传播给其他 Agent。记忆越多并不一定效果越好，反而可能增加污染和 Token 成本。

### 回答

Runtime 把记忆分为调用内工作上下文、AgentSession 私有连续性、经过验证的共享事实，以及可失效和替代的长期/实体记录。Agent 只能提交候选记忆；只有具有来源、版本、权限、新鲜度和验证证据的内容才能晋升共享视图。

每次 Invocation 应由 Context Compiler 生成最小、不可变的 ContextBundle，并由 ContextManifest 记录加入、排除、裁剪和拒绝的内容及原因。所有检索先按 Scope fail-closed，再按 Thread/Project、Role、任务依赖、证据等级、新鲜度和 Token 预算过滤。知识图谱或向量数据库只有在固定检索评测证明必要时才引入。

### 策略影响与现状

当前已经有感知、Working、长期和实体四类 MemoryRecord、SQLite 持久化、Role 过滤、幂等、过期、失效和 `supersedes`。但通用 ContextBundle/Manifest、长期 Thread 上下文压缩和检索质量评测仍属于 PROD-05。换窗口时则采用更简单可靠的办法：读取 HANDOFF、Plan、Git 状态和关键测试，不把整段聊天重新塞进模型。

## 7. 接入模型时如何保持供应商无关

### 问题

项目曾纠结订阅、CLI、SDK 和 API，也担心三个 Agent 是否必须接三个模型。如果 Role 直接绑定 DeepSeek、OpenAI 或某个 CLI，后续更换供应商、做消融实验和恢复 Session 都会非常困难。

### 回答

ChatGPT Plus 等产品订阅与 API 额度不是同一授权边界。对于需要程序化路由、预算、结构化输出和可重复评测的 Harness，主路径选择模型 API；CLI 型 Coding Agent 可以在未来作为 `FullAgentBackend` 接入，但不应成为 Core 协议。

Role 不绑定供应商。AgentProfile 组合 Role、Backend/Model Policy、Tool Capability、Context Policy、输出契约和预算；Runtime 根据能力和策略选择 Worker。项目可以先用一个模型承担多个 Role，只有当独立评测证明不同模型在成本、质量或能力上有价值时再拆分。API Key 只放在被 Git 忽略的 `.env` 或 Secret Broker 中，不能写入代码、Artifact、日志或模型上下文。

模型选择也不取决于“输入是否带图片”，而取决于当前步骤是否必须理解原始视觉语义。OCR、DOM、媒体元数据或普通程序能稳定提取的信息先由确定性工具处理；只有布局、空间关系、图标含义、画面事件或视觉缺陷等语义才调用 VLM。VLM 输出的是带来源、区域/时间、hash 和不确定性的 Evidence，后续需求拆解、代码生成和修复默认仍交给 LLM。

未来 Backend Factory 可以按需创建并缓存传输客户端/连接池，但不能把 Thread 事实、Agent 私有上下文、永久权限或供应商 Session 藏进客户端缓存。缓存键至少绑定 provider/backend、model、endpoint/transport、credential ref/version、config digest 和 auth scope；idle TTL、最大寿命、最大缓存数与主动 close 必须显式配置。凭据轮换、配置/能力版本变化、连续健康检查失败或寿命到期时立即失效；供应商 Session 单独由 `SessionBinding` 管理。

### 策略影响与现状

当前 ModelClient 已支持能力声明、结构化输出、多模态请求和用量元数据，并有 DeepSeek 文本与 Qwen 视觉配置及最小真实烟测。下一阶段要把同步客户端演进为可流式、可取消、可恢复的 Backend v2，同时保留供应商特有差异，禁止 fallback 静默降低隐私、工具或验收要求。

## 8. 权限控制必须早于智能能力

### 问题

Coding Agent 会写文件、运行命令、读取媒体并可能访问网络或凭据。早期还出现过目录混淆、输出不可见和把自然语言直接传给 `python3` 的问题。这些看似是 CLI 小故障，实质上暴露了输入协议、Workspace 边界和用户可见路径没有统一。

### 回答

有效权限应是 RolePolicy、WorkerCapabilities、Thread/Task Policy、Invocation Grant 和 RuntimePolicy 的交集。Agent 只能提出工具请求，不能扩大权限。文件写入必须解析到明确 Workspace 内，拒绝绝对路径、路径穿越和符号链接逃逸；命令使用 argv 白名单、禁用 shell、清理环境、限制网络和资源，高风险副作用需要短期 Approval 和幂等记录。

自然语言需求必须传给项目 CLI，而不是传给 Python 解释器当作文件名。CLI 应明确显示仓库根、输出目录、生成 Artifact 和验证结果，使终端、Finder 与 Web 对“文件写到了哪里”使用同一事实来源。

用户上传或要求分析图片、音频、视频，不等于授权 Runtime 主动截屏、录屏、打开摄像头、读取其他窗口或修改宿主 App。这些属于独立的 Tool Capability，必须针对当次操作获得明确 Approval，不能从“任务包含媒体”自动推导。

### 策略影响与现状

Coding 路径已经实现 Workspace、PatchIntegrator、路径权限、受控命令、环境清理、超时进程组清理和 `.env`/运行输出忽略规则。通用 Invocation CapabilityGrant、Tool Gateway、Secret Broker 和副作用账本尚未完成，归 PROD-03；因此当前安全能力不能被夸大为敌对多租户隔离。

## 9. 如何可视化 Agent 工作，又不泄露思维链

### 问题

用户需要看到每个 Agent 是否在工作、为什么等待、如何交接以及 Fixer 为什么没有触发。但直接展示模型原始推理既不可靠，也可能泄露隐私、提示词和安全策略；只展示最终答案又无法排查 Runtime 问题。

### 回答

界面展示结构化、可审计的运行事实，而不是原始 Chain-of-Thought：Thread/Turn、Agent 泳道、Invocation 状态、输入 Artifact、公开的简短理由、Handoff 的 What/Why/Tradeoff/Open Questions/Next Action、权限与预算、Validator Evidence、Runtime Decision 和收敛状态。

内部事件应先写入统一 RuntimeEvent/Journal，再由 Web 投影，而不是让前端从日志文本猜状态。这样“可视化”同时服务调试、审计和用户介入，而不仅是动画效果。

### 策略影响与现状

当前 Web 已能展示 Coding DAG、状态、Artifact 和验证事件，并明确不展示模型原始推理。它仍然是偏 Coding 的工作台。目标 Thread 页面、长期 Agent 身份、Mailbox、持久事件流、权限抽屉和用户暂停/批准入口要等 PROD-01～04 的后端事实源完成后再实现，避免 UI 假装系统已有这些能力。

## 10. 如何证明 Multi-Agent 真的比单 Agent 有价值

### 问题

更多 Agent 往往意味着更多 Token、通信和延迟。只展示一次冒泡排序、二分查找或漂亮页面，不能证明架构有效；Mock 测试全通过也不能证明真实模型在真实仓库中有收益。

### 回答

必须把 Harness 可靠性与模型智能效果分开评测。前者验证状态机、权限、恢复、幂等、证据和验收不变量；后者使用版本化任务、隐藏验收和相同预算，对比单 Agent、Planner+Developer 和完整 Reviewer/Fixer 等策略的成功率、首次通过率、修复率、人工介入、Token、费用和端到端延迟。

Core 评测不能让单一网页审美或 Bug fixture 代表整个产品。持续交互、多 Agent 协作、多模态理解和插件/工具任务应分层报告，不汇总成一个掩盖差异的总分；Coding 插件至少同时包含新功能、Bug 修复和行为保持重构。视觉美学存在多解，VLM 分数与 P1/P2 Gate 只能在 VisionForge 内通过固定任务和人工标注校准，不能晋升为 Core 的通用完成条件。

开发样例、可见 Validation 与隔离 Held-out 必须分开；只对某个 fixture 有效的规则留在插件。任何 Harness 行为改动应按当前 Evo-style 纪律记录 Baseline、失败证据、单一可证伪 Mutation、固定控制项、Validation/Held-out、成本与回归，再决定 KEEP、ROLLBACK 或 INCONCLUSIVE。

Mock/Fake 证明协议和 Harness 按设计工作；真实模型 Canary 评价模型与 Adapter；真实仓库 Dogfood 才能评价工程落地。三者不能互相替代。若多 Agent 没有可测的边际收益，应减少 Agent、降低触发频率或把复杂机制降为可选策略。

### 策略影响与现状

项目已经有固定 Coding 任务、隐藏验证、三方案统一预算和脚本化 dry-run。当前这些结果不能用于宣称“Multi-Agent 更优”，因为真实消融仍未在足够样本上完成。MindBridge 等项目的参考价值主要在共享事实、任务认领、安全审查、评测和部署闭环，而不是复制它的 Agent 数量或产品结构。

## 11. 事故如何真正变成系统能力

### 问题

许多 Agent 项目在失败后只调整 Prompt、写一段复盘或让 Agent “反思”。这种做法不能回答事故是否复现、是否止损、修复是否有效、同类问题是否再次发生，也无法估计漏检和误报。

### 回答

事故学习被设计为一等横向子系统：`RuntimeEvent → Detector/Invariant → IncidentLedger → EvidenceBundle → Replay/FaultInjection → ChangeSet → Shadow/Canary → LearningItem → GuardrailEvaluation`。事故事实、调查推测、修复提案和已激活规则必须分开；Agent 可以整理证据和提出根因假设，但不能批准 Guardrail、篡改事故事实或宣布事故关闭。

覆盖率也必须按故障族统计 detected、prevented、missed、escaped、false-positive、复发率、MTTD、MTTC 和 MTTR，不能用“测试全部通过”推导生产零风险。

### 策略影响与现状

完整设计见 [`Plan/Plan25.md`](Plan/Plan25.md) 和 [`Plan/闭环覆盖范围.md`](Plan/闭环覆盖范围.md)。目前只是 INC-00 文档和部分 Runtime 协议完成，Event Journal、Incident Ledger、Detector、Replay 和 Guardrail 仍未实现。后续每个 PROD 批次必须同步交付与新增风险对应的 INC 增量，不能把事故能力推迟到功能全部完成后补做。

## 12. 如何避免长对话和日常开发丢失决策

### 问题

随着窗口越来越长，继续依赖聊天历史会消耗大量 Token，而且代码已经变化后，旧回答可能反而误导。另一方面，如果只看最后一次提交，又会丢失为什么选择某个方案、放弃了哪些候选以及还有哪些假设没有验证。

### 回答

项目把知识分成四层：代码和测试保存实现事实，HANDOFF 保存当前方向与下一步，Plan 保存重要策略讨论和取舍，Track 保存每日实际变化。新窗口先读取 HANDOFF，再检查 Git 状态、最近提交、关键代码和测试；不把旧聊天当成事实源。

每日自动化严格按 Track、Plan、Tests、Commit、Push 串行执行。Track 不编造当天没有发生的内容；只有存在未归档策略才创建新 Plan；测试或敏感文件检查失败就禁止 commit/push。这使日常记录成为质量门禁的一部分，而不是事后补写的日报。

### 策略影响与现状

当前交接和自动归档流程已经建立，也成功用于跨窗口恢复。不过文档仍可能过期，因此每次恢复都必须以代码、测试和 Git 为最终校验。本文本身也应只保留会影响设计或推进顺序的问题，普通命令错误和重复追问不再持续累积。

## 13. 通用请求、场景协议和模型真相权如何划分

### 问题

项目同时出现过 `CodingRequirement`、`RequirementEvidence`、UI Spec 和 TaskSpec。如果每个输入都升级为 Task，Core 会被插件语义污染；如果模型可以生成一个字段完整的 JSON，又可能把 `passed=true`、`verified` 或“测试已执行”伪装成事实。

### 回答

Core 使用 `Message + Turn` 表达普通交互；只有需要显式交付、依赖和验收的工作才创建 Task/ScenarioRun。文本、媒体和工具结果通过带 Scope、来源、模态、hash、时间/区域与不确定性的 Evidence/Artifact 引用进入。Coding 可扩展 RepositoryScope/CodingRequirement，VisionForge 可扩展 UI Spec，但插件只能收紧输入、输出与验收，不能扩大 Core 的权限、预算、Scope 或事实写入边界。

真相权属于 Runtime，不属于字符串。Agent 产出一律是候选 Claim、Proposal、Patch 或 Evidence；用户原始输入和工具原始回执可以作为来源记录，但它们的语义解释不会自动 verified。只有 Runtime 执行/验证的工具证据、Artifact hash、独立 Validator 与匹配的 AcceptancePolicy 才能签发 verified Claim 或 AcceptanceRecord。`unknown != passed`，执行成功也不等于验收成功。

### 策略影响与现状

PROD-01A 已实现相关值协议和负向不变量，但 Runtime 独占权威写入仍需 PROD-01B 的事务 Store 才能成为真正安全边界，而不只是 dataclass 约定。

## 14. Artifact 能否承担全部通信，长期 Agent 又如何同步

### 问题

“所有 Agent 都通过 Artifact 通信”有利于重放和替换模型，但对话顺序、收件人、路由、验收和运行状态如果也塞进任意 JSON，会形成第二套控制面。长期 Agent 还会遇到另一个问题：其他 Agent 的新事实是否应直接热插入正在执行的 Prompt。

### 回答

内容和大证据通过 Artifact 传递，控制面通过类型化协议传递。Message 独占正文、sender/recipient、顺序、parent/causation 和可见性；HandoffProposal 表达交接意图；RuntimeEvent 表达审计事实；AcceptanceRecord 表达权威验收结论。它们可以引用 Artifact，但不能被任意 Artifact 替代。Artifact 本身还需要 Schema 版本、内容寻址、引用完整性、ACL、失效和垃圾回收。

长期存在的是 AgentInstance 身份、Mailbox、AgentSession 和事件游标，不是永不结束的模型调用。每次 Invocation 使用固定的 Thread/Context/Workspace 版本；其他 Agent 的新结果先经 Runtime 校验写入 Message/Artifact/Event，再投递 Mailbox，并在下一次 Invocation 编译 ContextBundle 时增量消费。只有取消、安全/权限事件、用户改向或输入已经失效时，Runtime 才在安全检查点撤销副作用资格并结束或重启当前 Invocation，普通信息不能热修改可重放的输入快照。

### 策略影响与现状

AgentInstance/AgentSession 的值协议已经建立；Journal/Event 持久化属于 PROD-01B，Mailbox/Handoff/游标消费属于 PROD-04，ContextBundle 重装属于 PROD-05。

## 15. 何时才应该升级为分布式、微服务或向量数据库

### 问题

长期 Agent、共享记忆、Journal 和多 Worker 很容易让方案提前加入消息队列、PostgreSQL、对象存储、向量库、知识图谱和多个微服务。但基础设施数量并不等于生产成熟度，过早拆分会增加新的故障面。

### 回答

第一阶段保持 self-hosted、单组织/单信任域、单机优先的 production-shaped modular monolith。模块可以按 Thread/Message、Invocation/Session、Event/Incident、Artifact/Context、Capability/Tool、Scenario/Plugin 和 Web/API 划分，但不因存在模块边界就立即拆进程。

只有固定评测或生产指标证明本地方案在锁竞争、吞吐、可用性、数据规模、隔离或检索质量上达到预先冻结的阈值，才升级外部队列、PostgreSQL、对象存储、向量库或远程 Worker。当前 SQLite 中旧纵向切片的 Memory、TaskGraph/Scenario Snapshot 和 Checkpoint，也不能冒充 PROD-01B 新 Runtime 的权威 Store。

### 策略影响与现状

当前目标仍是先把单机事务事实链、恢复语义和权限边界做实；微服务、复杂语义检索和远程 Worker 不应插队。

## 当前最重要的未解决问题

项目现在最需要的不是继续增加 Agent、模型或页面，而是把已经冻结的协议变成可恢复的运行事实。下一阶段顺序是：

1. PROD-01B：01B-1 事务底座已完成；下一动作冻结 01B-2，再逐片实现 SQLite 状态 Store、append-only Journal、Outbox、最小 BudgetLedger 和持久查询，并保证同事务提交。
2. PROD-01C：实现 durable enqueue、claim/lease/heartbeat、fencing、Finalizer/Reaper、级联取消和重启恢复。
3. PROD-01D/01E：把现有 Coding/Scenario 路径接入 Thread，提供持久 Web 查询，并建立首批事故 Observe/Shadow 链。
4. PROD-02：Backend v2、SessionBinding、Streaming、usage 和硬取消。
5. PROD-03：CapabilityGrant、Tool Gateway、Secret Broker 和执行隔离。
6. PROD-04：Mailbox、结构化 Handoff、动态协作控制面和 Agent 泳道。
7. PROD-05/06/07：Context/共享记忆、插件与效果/容量评测，以及迁移和事故运营。

在这三步完成前，不应优先扩展更多模型、自由 A2A、复杂向量数据库、微服务或完整 Agent 泳道 UI。

## 已被后续讨论修正的旧答案

| 旧答案或默认 | 当前结论 |
|---|---|
| 项目中心是 Coding Bug 修复 | Coding 是目标插件/纵向切片；项目本体是多模态 Multi-Agent Harness，Runtime 是 Core 执行内核 |
| VisionForge 可以代表整个项目 | 它只是独立视觉 Web 场景，UI Spec 和视觉评分都留在场景内 |
| 只要有图片就让所有 Agent 使用 VLM | 只在确定性工具无法替代的视觉语义节点调用 VLM |
| build/test 是所有 Thread 的统一完成条件 | 每个场景注册 AcceptancePolicy；普通交互不强制产生代码或 TaskGraph |
| Agent 成功或多数投票可以宣布完成 | Agent 只提供 Evidence；Runtime 依据 Policy 签发 AcceptanceRecord |
| 所有通信都塞进任意 Artifact | 大内容/证据用 Artifact；Message/Handoff/Event/Acceptance 使用类型化控制协议 |
| Agent 长期存在等于模型调用或供应商 Session 永不结束 | 长期的是身份、Mailbox 和 Runtime AgentSession；模型调用是短生命 Invocation |
| 记忆越多越好，应尽快加向量库 | 先完成 Scope、ACL、来源、时效、失效和检索评测，再按证据升级存储 |
| 先做 Agent 泳道页面就能体现 Multi-Agent | 先建立持久 Message/Event/Handoff 事实源，页面只做可审计投影 |

## 相关来源

- [`HANDOFF.md`](HANDOFF.md)：当前产品方向、已实现事实、下一批和验证命令。
- [`Plan/Plan26.md`](Plan/Plan26.md)：产品定位、Runtime Charter、Evo-style 开发纪律与 PROD 顺序。
- [`Plan/Plan25.md`](Plan/Plan25.md)：事故学习闭环、Evo 边界和运营约束。
- [`Plan/闭环覆盖范围.md`](Plan/闭环覆盖范围.md)：事故覆盖的可证明范围与当前缺口。
- [`FAQ.md`](FAQ.md)：历史细节参考；与 HANDOFF/Plan26 冲突时不可覆盖当前决议。

## 16. 2026-08-25 窗口增量：测试转绿以后，问题才真正开始暴露

本节记录本窗口从 `PROD-01B-1` 推进到 `01B-3B-1`、再进行技术选型与计划审查时实际遇到的问题。它不覆盖前文历史，只补充“设计了什么测试、首绿后又发现了什么、为什么原计划需要重新看”的工程事实。精确受测哈希、命令、计数和修复位置仍以 [`VerificationReports/PROD-01B.md`](VerificationReports/PROD-01B.md) 为准。

### 16.1 单元测试全绿，不代表事务和持久化边界已经安全

| 切片 | 首次实现看起来已经满足的能力 | 首绿后独立挑战实际发现的问题 | 当前处理 |
|---|---|---|---|
| `01B-1` SQLite UoW | 显式 commit/rollback、WAL、migration 和故障回滚 | 原始 SQLite connection/transaction SQL 可绕过 UoW；Cursor iterator 泄漏底层连接；`INSERT OR ROLLBACK` 可让外层事务丢失后继续自动提交；DDL authorizer 对 `ALTER TABLE` 参数位置理解错误；schema/WAL 检查存在 TOCTOU；ledger 的 REAL 版本被 `int()` 静默接受；rollback failure 一度被吞掉 | 已改为受控 Result、事务状态复核、严格 schema 类型、正确 authorizer、事务内二次检查和显式 rollback error；均有回归 |
| `01B-2` Thread+Event | State/Event 原子提交、Event append-only、exact retry | SQLite `REPLACE` 可绕过 UPDATE/DELETE trigger；隐式 rowid collision 可改写历史；Store 可以拿异库 UoW 静默写错数据库；跨线程 abort 覆盖 typed error；幂等快路径会把已损坏的历史 Event、当前 Thread 或最新 head Event 误报为已提交 | 通过 `WITHOUT ROWID`、碰撞 trigger、Store/UoW 绑定、owner-thread 门禁和完整解码复核关闭；均有持久回归 |
| `01B-3A` Event+Outbox intent | Thread+Event+Outbox 三写、Policy、v2→v3 migration | 超大整数与非法 Unicode 直到 SQLite/编码阶段才泄漏底层异常；错绑 Outbox collision 泄漏裸 SQLite error；current-head Outbox 缺失时仍允许下一 mutation；读路径缺 Policy 时先产生文件副作用/错误错误码；并发初始化在 WAL bootstrap 暴露锁竞态和不一致快照；orphan/future managed object 可能在拒绝前改变 WAL；busy deadline 与单次 PRAGMA 超时不一致；生命周期 CHECK 不够完整 | 首绿后的 adversarial 分两轮击穿并关闭 10 组产品缺陷；3A 最终只在其声明范围内 `KEEP`，不外推为可靠发布 |
| `01B-3B-1` claim/NACK | 本地 claim、NACK、expiry-reclaim 和 ownership CAS | 时钟回拨检查放在 eligibility 之后，某些行会被静默跳过；NACK ownership 未先绑定 Store 自身 publisher；同 aggregate 的后序行已进入 CLAIMED 时仍可能领取前序，形成双 owner 风险；`verify_integrity()` 只做逐行解码，漏掉跨行生命周期腐败 | 抽出共享 aggregate-history validator，让 claim、NACK、integrity scan 复用；最终 25 项 3B-1、98 项定向、184 项 Runtime、397 项全量门禁通过（其中 4 skip），决定仅为 `KEEP (3B-1 only)` |

这几轮最重要的结论是：**测试数量不是覆盖证明，绿色结果也不是 Acceptance。** 真正有价值的是在候选已经声称满足契约后，由不同测试设计者主动寻找可绕过路径，并把反例固化成回归。

### 16.2 EXPECTED_RED、产品缺陷和测试缺陷必须分开

本窗口反复出现三类“红色”，含义完全不同：

1. **EXPECTED_RED**：能力尚未实现前，先冻结 Oracle 并证明测试确实失败。例如 Outbox/claim 类型尚不存在。这是开发起点，不是事故，也不能算“测试通过”。
2. **PRODUCT_DEFECT**：实现已经首绿并声称满足不变量后，新的独立反例将其击穿。例如 append-only 被 `REPLACE` 绕过、损坏的 current head 被 exact retry 掩盖、跨行 lifecycle 腐败未被 integrity scan 发现。这才进入缺陷与事故候选记录。
3. **TEST_DESIGN_DEFECT**：测试期待比冻结契约更窄或无法真正区分旧实现。例如并发 loser 只接受一种合法 typed conflict、把 schema metadata corruption 强制要求为另一错误类型、WAL 测试只证明事务前检查也会失败。此时应修正 Oracle，不能为了过测改变正确实现。

首轮 7/7 或 15/15 绿色以后，独立攻击仍继续发现缺陷；3B-1 一度因没有持久的跨进程与 `os._exit` Oracle 而不能收口。后续补齐双进程单赢家、claim/NACK commit 前强退回滚和 commit 后重开持久证据，说明**临时手工演练不能替代仓库中的可重复证据**。

### 16.3 架构层暴露出“基础设施先走、真实消费者后补”的风险

技术选型审查发现，新 `runtime_persistence` 已经实现大量协议、迁移、完整性和 Outbox 代码，但实际 CLI/Web 主链仍使用 legacy Snapshot/内存状态：

- [`demo/coding_workflow/dag_runner.py`](demo/coding_workflow/dag_runner.py) 仍装配旧 `SQLiteRuntimeStore`；
- [`demo/web_server.py`](demo/web_server.py) 的任务索引仍主要位于进程内；
- 新 Outbox 的 claim 热路径会在 SQLite Writer 事务内加载一个 Scope 的 Event/Outbox，再由 Python 分组和排序，尚无 1k/10k、Writer Lock、p95 或 soak 证据；
- 当前没有 `pyproject.toml`、Python 依赖锁和 CI，历史验证难以仅凭 commit 在干净环境复现；
- 模型层目前只有 OpenAI-compatible 同步适配，且曾声明 `TOOL_CALLING` 能力，但公共 request/payload 尚无完整 tool schema/call/result 语义。

因此技术栈本身不需要推倒：继续使用 Python 模块化单体、自研 Harness/Runtime 和单机 SQLite。真正需要控制的是顺序：3B-2 不能删除，因为 ACK/Receipt/PUBLISHED 必须闭合现有状态机；但它也不能只靠 Fake Transport 自证，应先冻结首个实际本地 Sink 和最小容量门。PostgreSQL、Broker、Temporal、向量库和微服务仍要等真实主链与容量证据触发。

### 16.4 已知执行风险不能因为“不属于当前功能依赖”就长期后置

当前 CLI 会在受信任控制面加载模型凭据，而 legacy Workspace/Browser 子进程尚未统一使用从空映射构造的环境和同一监督语义。该问题的详细威胁边界、A/B 顺序争议和 A～H 验收已单独写入 [`SecurityProblem.md`](SecurityProblem.md)，避免在本文件复制过多安全细节。

这里保留的工程结论是：

- `SEC-EXEC-01` 不是纯 SQLite 3B-2 的代码依赖；使用可信本地 fixture 时可以独立开发 Outbox；
- 但它是再次运行真实模型生成代码、执行候选代码或把 legacy CLI/Web 接入新 Runtime 前的发布门禁；
- “命令白名单”“cwd 位于 Workspace”“普通子进程”均不能被描述成生产沙箱；
- 安全契约已冻结不等于实现已经通过，必须保存环境 sentinel、旁路扫描、进程树清理和正常对照的可执行证据。

### 16.5 计划、交接和证据也会产生一致性事故

本窗口出现了几类非代码问题：

1. 3B-1 已经提交到 `f66e71e` 后，HANDOFF、Backlog 和 VerificationReport 仍有“旧 HEAD + dirty + 尚未提交”的当前时态，历史受测快照与当前 clean checkpoint没有分开。
2. 第一次更新 Handoff 时，把技术审查的建议写成了已经批准的 Plan 门禁；独立 Review 指出“分析建议 ≠ 用户授权”，随后才改成先请求 A/B 决定。计划状态只能由明确用户决定和权威文档共同确认，不能因多个 Agent 赞成而自动生效。
3. 多个任务同时编辑 HANDOFF、Plan、Backlog、问题文档时，单文件内容可能在审查期间变化。任何 Review 都必须绑定最终文件 hash；同一事实的多个文档更新应有明确 owner/顺序，避免一个文件已写“已批准”，另一个仍写“待确认”。
4. Codex UI 曾显示网络安全内容预警。可以确认它不是仓库测试、Git 或文件系统权限错误，但精确分类原因未知；后续安全讨论应保存防御目标、修复位置和回归证据，避免把猜测写成事故根因。

### 16.6 当前状态与剩余动作

| 问题 | 当前状态 | 后续证据/动作 |
|---|---|---|
| 01B-1/2/3A/3B-1 已发现的产品缺陷 | 已修复并有回归；各 KEEP 仅限各自切片 | 继续以 VerificationReport 的精确哈希和分层门禁为准 |
| `SEC-EXEC-01` 本地可信执行门禁 | Plan/Backlog/SecurityProblem 存在未提交增量；是否最终生效以用户决定和后续 clean commit 为准 | EXPECTED_RED → A～H → 独立攻击 → 正常回归 → 独立 Review |
| 3B-2 真实发布闭环 | 未实现 | 保留既有契约；在开始红卡前冻结 Sink、ACK/Receipt 恢复和容量 Oracle |
| 新 Runtime 接入 CLI/Web 主链 | 未完成 | 01C/01D 必须提供真实请求、终态、持久查询和重启恢复纵切，禁止长期双写两套真相 |
| Outbox 容量与 SQLite Writer 占用 | 未测 | 先做定向 1k/10k；完整背压、公平性、压力和 soak 仍归 PROD-06 |
| Python 环境与 CI 可复现性 | 未建立 | 单独冻结 `ENG-01`，不要在 Runtime Patch 中顺手扩依赖 |
| 当前文档一致性 | 工作区存在并发、未提交增量 | 收口前核对 `git status`、最终 hash、当前/历史时态和权威下一动作；`git diff --check` 只证明格式，不证明事实一致 |

## 17. 2026-08-26 窗口增量：本地可信执行、安全证据和“看得见的变化”

今天这一整批工作，表面上没有增加一个新页面，也没有出现一个可点击的新按钮。实际解决的是更靠下的一层问题：**当项目需要在本机运行 Python、构建命令或浏览器进程时，谁有权启动它，启动前检查什么，运行中如何限制，结束后又由谁证明它真的收干净。**

这件事很重要，但今天也暴露出一个同样重要的流程问题：原本只是“进行下一步”的普通开发，后来逐渐膨胀成了接近生产级安全认证的工作，包含多轮红卡、冻结哈希、双人审查、打回和重修。技术上得到了一批有价值的结果，节奏上却明显超出了正常开发批次。下面同时记录成果与教训。

### 17.1 原问题不是“能不能启动进程”，而是“进程启动以后谁负责”

旧实现中，Core Validator、Legacy Workspace 和 VisionForge 各自直接启动子进程。它们的命令规则、环境变量、超时、输出处理和清理方式并不完全相同。最危险的不是 `Popen` 这一个函数，而是这些差异组合起来以后会产生几类问题：

- 只限制可执行文件名，却没有精确限制完整参数，合法解释器仍可能执行未登记的代码；
- 子进程继承父进程环境，把模型凭据、临时变量或宿主机配置带进去；
- 父进程退出、超时或异常时，只回收直接子进程，孙进程或整个进程组可能继续存活；
- stdout、stderr、日志和异常对象在不同层各自处理，秘密可能在“显示结果已经脱敏”以后仍藏在原始字段或磁盘里；
- 一个清理动作失败以后，如果系统仍允许同一 Workspace 再启动新任务，就会把未知旧资源与新任务混在一起。

当前候选把静态扫描发现的 4 个原始进程调用点收敛到一个统一的 `subprocess.Popen` owner。其他入口只描述“要执行什么”，不再各自拥有底层进程能力。这个变化不是为了少写三行代码，而是为了让授权、环境、输出和清理都经过同一条路径。

### 17.2 当前答案：把一次本地执行当成一笔受控事务

现在形成的候选路径可以直白地理解为下面六步：

1. Runtime 先核对固定 Profile、精确 argv、绝对 executable、Workspace 路径和限制；非法请求在启动进程前就拒绝。
2. 对合法但尚未批准的请求，Runtime 返回由 Workspace、输入和 Profile 三个 digest 组成的 challenge。
3. 只有 Composition Root 明确批准后，才能签发一个 opaque、一次性、短期有效的 token；普通字典、布尔值或模型输出不能冒充它。
4. 子进程只得到固定的最小环境，并使用每次执行独有的 HOME/TMP，而不是复制父进程的全部环境。
5. stdout/stderr 在统一边界内做长度、SHA、截断和脱敏；后台日志也先经过同一规则，再允许持久化。
6. 每条终态都经过 Finalizer。清理无法证明时，Workspace 进入 quarantine，后续执行继续拒绝，直到独立恢复流程确认旧资源已经消失。

这里必须保留边界：这仍是**单用户、可信本地命令的受控执行候选**，不是容器、虚拟机或多租户沙箱。macOS/Python 3.9 没有 pidfd，`killpg`、文件系统 syscall 和 PID 复用也没有被形式化消除。敌对代码、setsid/double-fork、native extension 和硬实时清理仍应得到 `SANDBOX_REQUIRED`，不能因为今天的测试通过而降级放行。

### 17.3 证据终于从“全是 mock”跨到了两条真实 happy path

今天早些时候的大量测试主要用于证明“不会误启动”“错误路径会被挡住”和“状态机在脚本化反例下自洽”。它们很有用，但用户看不到真实命令是否真的跑过。窗口末尾把范围压回普通开发批次后，只执行了两条最小真实路径：

| 阶段 | 是否启动真实 target | 结果 | 它实际证明了什么 |
|---|---:|---|---|
| 行为红卡、Supervisor 与 POSIX safety | 否 | 当前登记的组合门禁、39 项 POSIX safety 等通过 | 证明 mock/static 下的拒绝、授权和清理逻辑；不证明真实 OS 生命周期 |
| watchdog-only | 否 | watchdog clean、joined、零 target | 证明守护进程自己能启动和退出 |
| arm → ACK → disarm | 否 | ACK 后主动取消，clean、joined、零 target | 证明“准备执行但最终不启动”的控制链 |
| fixture `stdout_short` | 是，1 次 | 1/1 通过，`0.614s`；stdout/stderr 各 21 bytes；direct child exit/reap=0；watchdog joined；PGID/PID 消失；残留 root=0 | 第一次证明可信 fixture 与 Guard 的真实 happy path能够闭合 |
| `ProjectWorkspace.run(["python3", "-V"])` | 是，1 次 | 1/1 通过，`0.014s`；challenge/issuer=1，spawn=1；输出 `Python 3.9.6\n`；exit=0；cleanup verified；批准对象复用在 spawn 前被拒绝 | 第一次证明一个真实 production adapter 能完成 challenge → 批准 → 一次执行 → 结果 → 清理 |

两份默认禁用的入口分别是 [`demo/tests/test_local_execution_posix_target_smoke.py`](demo/tests/test_local_execution_posix_target_smoke.py) 和 [`demo/tests/test_project_workspace_production_smoke.py`](demo/tests/test_project_workspace_production_smoke.py)。精确命令、输出、哈希和停止条件保存在 [`VerificationReports/STEP-LOG.md`](VerificationReports/STEP-LOG.md) 的 `TRACE-20260826-119`～`124`。

这两个结果比“搭完了但完全没跑真实场景”向前了一步，但仍然只是两个最小 happy path。它们没有跑真实 Browser E2E，没有覆盖 timeout/crash/quarantine 的真实路径，也没有把完整 CLI/Web 业务流程走一遍。因此准确状态是：**底层主路径已搭成，两个最小真实点已经打通，真实产品场景和最终验收尚未完成。**

### 17.4 为什么直观上仍然看不到变化

用户说“直观地看不到变化，也没有数据”，这个判断是对的。原因不是完全没做事，而是今天的交付形态仍然偏向内部基础设施：

- 真实入口默认关闭，只能通过精确测试 ID 显式运行；
- 结果散落在 unittest 输出、Step Log 和 cleanup evidence 中，没有汇成一个用户可读页面；
- CLI/Web 还没有稳定展示“为什么拒绝、批准了什么、启动几次、耗时多久、清理是否完成”；
- 当前工作树仍是 dirty/uncommitted，没有形成可部署版本；
- 没有持续指标，因此也看不到拒绝数、批准数、spawn 数、超时数、quarantine 数和清理耗时趋势。

这说明一个工程事实：**后端变化即使技术上重要，如果没有可见入口和数据投影，也不能算一个容易感知的产品增量。** 下一次若继续这条线，最有价值的不是再增加一轮认证，而是做一个最小可见演示：同一条命令先展示默认拒绝和 `spawn=0`，再由明确批准执行一次，并把 argv、exit、duration、stdout、cleanup 和残留检查同时输出成终端摘要与 JSON/Markdown 报告。

### 17.5 这批最大的流程问题是范围膨胀

今天的技术问题确实有安全敏感性，但执行方式一度把每个中间候选都按发布认证处理：冻结哈希、双审、发现边角反例、修订、重新冻结、再双审。这样能发现真问题，例如过期 ACK、namespace/pyc import 绕过、输出脱敏逃逸和 cleanup 竞态；但它也造成三个副作用：

1. 普通“下一步”失去了清晰终点，用户很难知道今天到底交付了什么。
2. 大量时间花在证明测试工件自身没有旁路，而不是尽快跑通一个能看见的业务结果。
3. 每次修正又产生新的审查对象，形成自我延长的 Gate，而不是按批次交付。

窗口中还发生过一次明确的合规偏差：误跑了整个 `tests.test_command_validators`，启动了多次真实 Python workload，timeout 路径还执行了真实 cleanup signal。它没有访问网络、模型或真实秘密，但违反了当批“只运行 pure mock”的预注册边界，因此 `10/11` 的结果被整体排除，不能算通过证据。这个事件反过来说明，危险测试必须默认禁用、要求精确选择，并把真实执行和 pure mock 分开。

后续默认采用普通开发节奏：

- 一批只选一个能说清楚的目标；
- 一次实现、一个聚焦测试、一次简短复核；
- 目标时间 30～90 分钟，到点就停；
- 非阻塞问题进入 Backlog，不在当前批无限展开；
- 只有发布里程碑、真实高风险边界或用户明确要求时，才启用双人审查、完整哈希冻结和认证矩阵。

过程记录仍然保留 What / Why / Effect / Evidence，但记录服务于决策，不再让记录本身变成主要工作。

### 17.6 数据还暴露出一个新的运维问题

运行 `ProjectWorkspace` 真实 happy path 前，`/private/tmp` 已经存在 100 个 `local-trusted-execution-*` 目录；运行后仍然是完全相同的 100 个，`added=[]`、`removed=[]`。因此本次真实运行没有新增残留，也没有删除旧数据。

这 100 个目录不能归因于今天最后这一次执行，也不能在来源不明时直接清理。但它们说明当前还缺一层长期可见性：谁创建了临时目录、属于哪次 execution、是否已经 terminal、为什么仍然存在、何时可以安全回收。后续应先做只读 inventory 与 provenance 报告，再决定是否清理；不能因为目录名字相似就批量删除。

### 17.7 现在停在哪里，原来还要做什么

| 项目 | 当前状态 |
|---|---|
| 单一进程 owner、五个 Profile、一次性批准、最小环境、统一输出与 cleanup | 已在 dirty 工作树形成候选，尚未提交/部署 |
| 可信 fixture 真实 happy path | 已执行一次并通过 |
| `ProjectWorkspace` 真实 happy path | 已执行一次并通过 |
| 用户可见 CLI/Web Composition Root | 还没有形成可直观看见的完整演示 |
| timeout、crash、quarantine/recovery 的真实路径 | 未执行 |
| 真实 Browser E2E、Renderer/browser binary Profile | 未完成 |
| 当前最终哈希的全量回归 | 未完成；历史结果不能冒充当前结果 |
| `KEEP`、Runtime Acceptance、commit、push、deploy | 均未签发或完成 |

所以今天不是“什么都没发生”，也不是“已经生产认证完成”。更准确的说法是：**受控本地执行的底座已经搭起来，并用两条最小真实命令确认主路径可走；产品可见层、失败场景、Browser、全量回归和发布收口仍在后面。**

如果继续，下一小批应优先完成一个用户看得见的 Composition Root 演示和自动数据报告，而不是立刻开启新的认证 Gate。完成这个可见点以后，再从真实 timeout/quarantine 或 Browser E2E 中只选一个继续。
