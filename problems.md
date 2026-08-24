# 项目关键问题与当前答案

这不是聊天问题清单，而是项目开发过程中真正困难、并且改变了整体策略的问题复盘。每个问题都记录了为什么它重要、当前采用的答案、它对架构造成的影响，以及仍未完成的部分。代码、测试和 Git 是最终事实来源；本文记录的是决策脉络。

## 1. 项目到底在做什么

### 问题

项目最初从三个 Agent 的 Coding Demo 起步，随后加入 DAG、Memory、Web、多模态和模型接口。能力越来越多，但定位一度在“Coding Agent”“Multi-Agent Demo”“Harness”“AI Infra”之间摇摆。最大的困难不是继续增加功能，而是判断什么属于产品本体，什么只是验证本体的一条业务链路。

### 回答

当前项目定位为一个**可交互、可长期运行、单机优先的多模态 Multi-Agent Harness**。Harness 是项目本体；通用 Multi-Agent Runtime 是核心执行内核，负责 Thread、Message、Invocation、Session、Artifact、权限、预算、恢复、审计和场景化验收。角色/模型策略、Context/Memory、工具、协作、评测和事故学习属于 Harness 控制层；Agent、模型和业务场景均可替换。

Coding 仍然重要，但它不再定义 Core。现有 Coding 和 VisionForge 代码被保留为已经验证过的纵向切片，目标是逐步迁移成专业场景或插件，而不是为了“通用化”直接重写。

### 策略影响与现状

这次纠偏避免了普通交互、多模态分析和其他工具任务被迫套用 build/test/Fixer 语义。新的产品边界已经在 [`Plan/Plan26.md`](Plan/Plan26.md) 和 [`HANDOFF.md`](HANDOFF.md) 冻结；但持久 Thread、通用 Agent Workspace 和真正的 CodingPlugin 仍未完成，所以现在是“方向和协议已调整，产品入口尚未完全迁移”。

## 2. Multi-Agent 不能只是把固定 Workflow 换成多个名字

### 问题

早期三个 Agent 更像固定节点：Planner 做完才轮到 Developer，Developer 做完再轮到 Tester。虽然进程里存在多个 Agent 名称，但缺少长期身份、独立判断、结构化交接和动态任务认领，因此利用率低，也很难说明这与普通 Workflow 有什么本质区别。

### 回答

项目将 **Role、Agent、Worker 和模型拆开**：Role 表示职责、权限和输入输出契约；AgentInstance 是 Thread 中可寻址的协作者身份；Worker 是一次可被路由的执行能力；模型只是 Worker 的一种 Backend。一个 Role 可以有多个 Worker，一个模型也可以承担多个 Role，二者不永久绑定。

Agent 只能提交 Artifact、Message 或 `HandoffProposal`。是否创建新的 RouteEdge、ChildInvocation 或 Task，必须由 Runtime 校验目标、权限、依赖、预算、链深、循环和资源冲突后决定。内容判断可以由多个 Agent 对等完成，但状态权、权限权和副作用权始终属于中央 Harness。

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

### 策略影响与现状

当前 ModelClient 已支持能力声明、结构化输出、多模态请求和用量元数据，并有 DeepSeek 文本与 Qwen 视觉配置及最小真实烟测。下一阶段要把同步客户端演进为可流式、可取消、可恢复的 Backend v2，同时保留供应商特有差异，禁止 fallback 静默降低隐私、工具或验收要求。

## 8. 权限控制必须早于智能能力

### 问题

Coding Agent 会写文件、运行命令、读取媒体并可能访问网络或凭据。早期还出现过目录混淆、输出不可见和把自然语言直接传给 `python3` 的问题。这些看似是 CLI 小故障，实质上暴露了输入协议、Workspace 边界和用户可见路径没有统一。

### 回答

有效权限应是 RolePolicy、WorkerCapabilities、Thread/Task Policy、Invocation Grant 和 RuntimePolicy 的交集。Agent 只能提出工具请求，不能扩大权限。文件写入必须解析到明确 Workspace 内，拒绝绝对路径、路径穿越和符号链接逃逸；命令使用 argv 白名单、禁用 shell、清理环境、限制网络和资源，高风险副作用需要短期 Approval 和幂等记录。

自然语言需求必须传给项目 CLI，而不是传给 Python 解释器当作文件名。CLI 应明确显示仓库根、输出目录、生成 Artifact 和验证结果，使终端、Finder 与 Web 对“文件写到了哪里”使用同一事实来源。

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

## 当前最重要的未解决问题

项目现在最需要的不是继续增加 Agent、模型或页面，而是把已经冻结的协议变成可恢复的运行事实。下一阶段顺序是：

1. PROD-01B：01B-1 事务底座已完成；下一动作冻结 01B-2，再逐片实现 SQLite 状态 Store、append-only Journal、Outbox、最小 BudgetLedger 和持久查询，并保证同事务提交。
2. PROD-01C：实现 durable enqueue、claim/lease/heartbeat、fencing、Finalizer/Reaper、级联取消和重启恢复。
3. PROD-01D/01E：把现有 Coding/Scenario 路径接入 Thread，并建立首批事故 Observe/Shadow 链。

在这三步完成前，不应优先扩展更多模型、自由 A2A、复杂向量数据库、微服务或完整 Agent 泳道 UI。
