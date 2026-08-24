# 聊天窗口中影响项目策略的问题与当前回答

> 整理日期：2026-08-24
> 文件名按聊天中指定的 `prombles.md` 保留。

本文只收录会改变产品定位、Core 边界、Agent 协作、上下文、权限、验收、评测和实施顺序的问题。它不保存普通命令错误、临时 API 配置或页面字段解释。

如果本文与实现冲突，以下优先级为准：**代码与测试 → `HANDOFF.md` / `Plan/Plan26.md` → 本文 → 旧聊天回答**。本文中的“已实现”表示有对应代码或测试；“已冻结”只表示方向已写入当前计划，不代表运行能力已完成。

## 当前实现状态速查

| 范围 | 状态 | 不能误解为 |
|---|---|---|
| 交互式多模态 Multi-Agent Harness 产品方向 | 已冻结 | 通用产品入口已全部迁移，或 Runtime 内核已经完整落地 |
| PROD-01A Runtime 领域对象、不变量和 Coding 单向适配 | 已实现并有测试 | 已有持久队列、Journal 或恢复运行时 |
| VisionForge `visionforge:web_visual` | 已是独立 Scenario Plugin | 已嵌套在 CodingPlugin 中，或代表 Core |
| Coding 纵向切片 | 已实现，目标迁为插件 | `CodingPlugin` 已完成 |
| SQLite 权威 Store、Journal、Outbox、BudgetLedger | 当前下一批 PROD-01B | 已有 Runtime-only 持久写入边界 |
| Durable claim/lease/heartbeat、Watchdog/Reaper | 待 PROD-01C | PROD-01A 的值协议已等于实际回收机制 |
| Backend v2 / SessionBinding / 客户端缓存 | 待 PROD-02 | 现有同步 ModelClient 已满足生产语义 |
| Mailbox、持久 Handoff、Agent 泳道 | 待 PROD-04 | 当前 Coding DAG 页面已是目标协作控制面 |
| ContextBundle/Manifest 与通用共享记忆治理 | 待 PROD-05 | 现有 Coding Memory 已是通用 Thread Context |

下面每个问题都按“产生背景 → 真正要解决的问题 → 当前回答”来写。“产生背景”说明它在聊天里为什么会出现；“真正要解决的问题”把讨论压缩成一句不依赖架构名词的话。

## 1. 项目的中心到底是什么？

### 产生背景

最开始的项目是一个可以让多个 Agent 协作、并且具备 Coding 能力的 Harness。后来先优化了任务拆分和记忆，又为了找到更容易演示的业务链引入 VisionForge。当测试、Web 页面和验收逐渐都围绕代码 Bug 和 UI 页面时，聊天中开始反复出现“这还是不是原来的 Multi-Agent 项目”的质疑。

### 真正要解决的问题

项目最终在做什么？哪些是产品本体，哪些只是一条可替换的业务场景？

### 当前回答

项目中心已纠正为**交互式、多模态、可长期运行的 Multi-Agent Harness**。通用 Multi-Agent Runtime 是核心执行内核，负责 Scope、Thread、Message、Agent 身份、Invocation、Artifact、权限、预算、恢复、审计和场景化 Acceptance；Harness 还包含角色/模型策略、任务路由、Context/Memory、工具、评测和事故学习。Coding、VisionForge、浏览器、文档处理等是 Harness 通过 Runtime 调度的专业能力或插件场景。

Multi-Agent 不再是“展示有多少个 Agent”的目的，而是处理独立判断、并行调查、权限分离、异议审查和专业能力交接的手段。简单任务可以只调用一个 Agent，不应为了形式强行多 Agent 讨论。

### 影响

这是对旧方向的明确修正。现有 Coding/VisionForge 纵向链保留，但不再定义 Core。当前方向已写入 `HANDOFF.md` 和 `Plan/Plan26.md`，产品入口与执行路径仍在渐进迁移中。

## 2. 项目是专门写网页或检查代码 Bug 的吗？

### 产生背景

在 VisionForge 阶段，用户打开网页后发现测试用例几乎都是“写一个页面”，获授权运行的真实基线也仍是固定页面与 VLM 验收链。之后离线 Core Coding 评测又主要使用失败 starter 和 Bug 修复任务；更后面的计划还一度提出再准备第二个真实代码仓库，但这个验证没有执行，后来已延后到 CodingPlugin 的 Canary/dogfood 阶段。这几个阶段叠加后产生了一个很具体的担心：为了找到“容易判分”的测试，项目是不是已经反过来变成只会修 Bug 和写网页的 Agent。

### 真正要解决的问题

为什么使用网页和 Bug 作为验证场景，又怎么保证这些测试不会反向定义整个 Core？

### 当前回答

不是。Coding 是第一个较成熟的专业场景，因为编译、单元测试、CLI 行为和 Patch 边界容易提供确定性证据；这只证明 Harness 在一条业务链上能工作，不证明产品只能做 Coding。

产品边界与最终分层评测必须同时覆盖四类业务模式：长期持续交互、多 Agent 协作分析、图片/音频/视频等多模态理解，以及 Coding/浏览器/文档等工具与专业能力。它们按 PROD 顺序逐步接入，不代表 PROD-01 就要一次实现四条完整产品链。真实仓库只在 Coding 插件进入 Canary/dogfood 时使用，不再是 Core 方向完成的前置条件。

## 3. VisionForge 应该放在哪一层？UI Spec 为什么不放进 Core？

### 产生背景

当 VisionForge 被提出时，最初的计划是把 UI Spec、Vue 项目、Playwright、Visual Reviewer 和 Fixer 直接加入现有 Harness。随后用户明确指出：“VisionForge 应该是框架上的插件式场景，不应该成为永久组成部分”，并进一步质疑 UI Spec 为什么出现在通用 Core 讨论里。

### 真正要解决的问题

VisionForge 与 UI Spec 应该属于 Core、Coding 场景，还是独立插件？

### 当前回答

VisionForge 是**插件式场景**，不是框架的永久组成。它当前已以 `visionforge:web_visual` 独立 Scenario Plugin 注册，并复用现有 Coding 能力；但 CodingPlugin 本身尚未实现，现有 Plugin SPI 也不支持插件嵌套，所以不能把“VisionForge 已位于 CodingPlugin 之上”当成事实。Core 只保留多模态 Evidence、Artifact 引用、能力路由、场景化 Acceptance 和受控工具执行等通用机制。UI Spec、视觉评分、P1/P2 问题和页面修复循环归 VisionForge 所有。

UI Spec 的作用是把截图中布局、组件、文字和交互等视觉语义转换为 VisionForge 可消费的结构化 Artifact。它不是通用 Requirement 的上位协议，也不应被普通对话、音频理解或后端开发强制使用。

## 4. 什么时候用 LLM，什么时候用 VLM？是否只要有图片就用 VLM？

### 产生背景

在回正产品定位后，用户给出了一条更精确的原则：VLM 只是 Harness 可调度的感知能力，不是项目中心。这是因为旧 VisionForge 链路很容易把“输入里有图片”等同于“后面所有 Agent 都应该用 VLM”，导致模型分工、成本和速度都不合理。

### 真正要解决的问题

一个带图片或视频的任务，哪一步真的需要 VLM，哪些步骤应该使用普通程序或 LLM？

### 当前回答

模型选择不取决于“是否附带图片”，而取决于**当前步骤是否必须理解原始视觉语义**。能由 OCR、DOM、媒体元数据或普通程序稳定提取的信息，应先用确定性工具提取，再交给 LLM。只有布局、空间关系、图标含义、画面事件或视觉缺陷等无法被普通工具可靠替代的任务才调用 VLM。

VLM 是感知能力，输出带来源、区域/时间、哈希和不确定性的结构化 Evidence。需求拆解、代码生成、错误分析和修复决策默认仍由 LLM 处理。后续 Agent 优先消费来源绑定且获授权的 Evidence Artifact，不重复传递原始媒体；observation/inference 仍保留它的验证状态和不确定性，只有独立 Validator/Runtime 证据可以将对应 Claim 晋升为 verified。信息不足或视觉复验失败时才再升级调用 VLM。

## 5. ModelClient 能否按需创建？客户端缓存如何失效？

### 产生背景

开始设计供应商无关的 ModelClient 后，用户连续追问了两件很实际的事：能不能只在任务真正需要时创建客户端；如果为了性能缓存客户端，怎么知道它什么时候已经过期。这又与“每个 Agent 有独立 Session”的设想交叉，容易把连接池、供应商 Session 和 Agent 上下文混为同一个对象。

### 真正要解决的问题

什么时候创建、复用和关闭 ModelClient，以及它与 AgentSession/SessionBinding 的边界在哪里？

### 当前回答

可以按需创建，但缓存的是**传输客户端和连接池**，不是 Thread 事实、Agent 私有上下文或永久权限。未来 Backend Factory 应使用不可变 `client_config_id`，至少覆盖 `backend/provider + model + endpoint/transport options + credential_ref/version + config_digest + policy/auth_scope`；原始 secret 和供应商 Session 绝不进入缓存键。连接池还需对 idle TTL、最大生命时间、最大缓存数和主动 close 建立显式规则。TTL 不应在 Core 里写死一个全局数字，而应由 Backend Policy 根据凭据有效期、连接特性和运行数据配置，实际寿命取凭据、配置和缓存策略中最早的失效点。凭据或配置版本变化应立即失效，不等 TTL。

凭据旋转、Endpoint/模型配置版本变化、能力声明变化、连续健康检查失败或超过缓存寿命时必须失效。供应商 Session 不放在 ModelClient 缓存键里偷偷复用，而是由可替换的 `SessionBinding` 管理。这是 PROD-02 的设计约束，当前还不能声称完整 Backend v2 已实现。

## 6. 缺少业务能力或没有合适 Worker 时怎么办？如何防止幻觉？

### 产生背景

在讨论“按能力路由模型”时，用户问了一个容易被忽略的情况：如果 Harness 根本没有完成某个任务所需的业务能力，下一步由谁决定。例如需要读视频时没有时间戳能力，需要编译时没有受控命令工具，模型很可能用文字猜一个看似完整的答案。

### 真正要解决的问题

系统缺少能力时应该请求补充、改派、阻塞还是拒绝，又如何阻止模型用猜测填补缺口？

### 当前回答

Runtime 先比较 Role、必需能力、输入协议、运行策略、职责分离和 Worker 可用性。当前代码在没有合格 Worker 时返回 `WorkerSelectionError` 与 `WorkerSelectionCode.MISSING_CAPABILITY` 等结构化原因，Executor 进入 blocked，不调用“最接近”的不合格模型。未来若新增通用 `CapabilityGap`，必须作为新协议显式定义，不得把候选名当成现有类。后续只能走受控路径：选择已配置的兼容 Worker、请求启用插件/工具或新权限、请求用户补充输入，或把工作对象标为 blocked/needs_input/unknown。

防幻觉不依靠 Prompt 中的“请诚实”。Observation、Inference 和 Proposal 必须分类；工具结果、媒体感知、测试通过和 Acceptance 都由 Runtime 核对来源、哈希、时效、subject/version 和执行证据。模型只能提交 Claim/Evidence/候选 Artifact，不能写权威事实或签发 accepted。

## 7. Harness 的通用需求协议是什么？访问边界怎么定？

### 产生背景

在拆分视觉感知和需求分析时，聊天中同时出现了 `CodingRequirement`、`RequirementEvidence`、`UI Spec` 和 `TaskSpec` 等多个名字。用户因此问：Harness 到底有没有一个通用需求协议，以及 UI Spec 既然明显是 VisionForge 的东西，为什么会像 Core 对象一样被讨论。

### 真正要解决的问题

普通对话、结构化任务和插件专用需求分别用什么协议，插件可以读写到哪一层？

### 当前回答

Core 不再另建一个重叠的 `InteractionRequest`。`Message + Turn` 表达通用交互输入；只有需要显式交付、依赖和验收的工作才创建 Task/ScenarioRun。输入中的文本、媒体和工具结果通过带 Scope、来源、模态、hash、时间/区域和不确定性的 Evidence/Artifact 引用进入。

场景可以在这些通用对象上扩展自己的协议：Coding 使用 RepositoryScope、CodingRequirement 和固定 Validator；VisionForge 使用 UI Spec 和 Visual Review。插件扩展只能收紧它的输入、输出和验收，不能扩大 Core 的 Scope、权限、预算或事实写入边界。

## 8. Role 仍然是第一路由键时，为什么还需要 Agent、Worker 和 Backend？

### 产生背景

旧架构里 Planner、Implementer、Tester、Fixer 等 Role 几乎与固定 Worker 一一对应。当讨论“一个 Role 应该有多个 Worker 实现，再按能力和策略选择”时，用户觉得 Role-first 本身是对的，但担心一旦加上其他路由条件，Role 就变成了没用的标签。

### 真正要解决的问题

如何既保留 Role 作为第一路由和权限边界，又能在同一 Role 中合理、稳定地选择不同 Worker？

### 当前回答

Role 仍是第一路由键，它定义职责、允许的输入/输出、权限上限和职责分离，因此不是装饰性人设。`AgentInstance` 是 Thread 中可寻址的长期协作者身份；`Worker` 是某个 Role 的一种可调度实现；`Backend/ModelClient` 是 Worker 使用的模型或工具后端。

分配过程先用 Role 做硬过滤，再检查 required capabilities、input/output protocol、Runtime policy、principal separation、可用性和预算，最后才用健康度、成本、延迟和稳定 tie-break 选择同 Role 的一个 Worker。这样既保留 Role-first 的可预期性，又能替换模型、使用不同工具或为同一 Role 配置多个实现。

## 9. 是否应该通过 Artifact 完成所有 Agent 通信？

### 产生背景

用户明确提出过一个理想：“通过 Artifact 来进行所有通信”，因为这样会更容易替换 Agent、追踪问题和重放过程。但继续向下设计时发现，对话顺序、收件人、路由、验收和运行状态也是通信，如果全部塞进任意 Artifact，反而会出现第二套消息和状态真相源。

### 真正要解决的问题

哪些信息应该存成 Artifact，哪些必须使用 Message、Handoff、Event 或 Acceptance 等受控协议？

### 当前回答

理想方向是**内容与证据通过 Artifact 传递，控制面通过类型化协议传递**，而不是字面上“所有东西都是 Artifact”。Message 独占正文、sender/recipient、顺序、parent/causation 和可见性；HandoffProposal 表达交接意图；RuntimeEvent 表达审计事实；AcceptanceRecord 表达权威验收决策。它们可以引用 Artifact，但不能被一个任意 JSON Artifact 替代。

优点是大内容不重复复制，模型与 Worker 可替换，中间结果可缓存、回放和单独验证。代价是需要 Schema 版本、内容寻址、引用完整性、过期/失效、ACL 和垃圾回收。因此 Artifact 是数据面单一事实源的重要部分，不是绕过 Runtime 控制面的通道。

## 10. 每个 Agent 长期存在并有独立 Session 时，其他 Agent 的新信息如何同步？

### 产生背景

在参考 Cat Café 的协作方式时，用户希望每个 Agent 都像一条长期存在的线程，有自己的 Session 和独立上下文。紧接着产生了一个实际同步问题：Agent A 正在工作时，Agent B 发现了新事实，到底是立即把新内容塞进 A 当前的 Prompt，还是等 A 下一次工作再看到。

### 真正要解决的问题

如何让长期 Agent 及时看到其他 Agent 的新结果，同时不热修改一次正在执行、应当可重现的输入快照？

### 当前回答

长期存在的是 AgentInstance 身份、Mailbox、AgentSession 和事件游标，不是一个永不结束的模型调用。每次 Invocation 使用固定的 Thread、Context 和可选 Workspace 版本。其他 Agent 的结果先由 Runtime 校验并写入 Message/Artifact/Event，再通知相关 Mailbox。

普通新信息在下一次 Invocation 生成 ContextBundle 时增量消费，不修改正在执行的输入快照。只有取消、权限/安全事件、用户改变方向或新信息使当前输入失效时，Runtime 才在安全检查点撤销继续产生副作用的权利，结束或重启 Invocation。

当前已建立 AgentInstance/AgentSession 的值协议，但上述同步模型尚未成为完整运行路径。Journal/Event 持久化属于 PROD-01B；Mailbox 投递、持久 Handoff 和协作游标消费属于 PROD-04；ContextBundle 重装属于 PROD-05。

## 11. 共享记忆和庞大上下文应该如何管理？

### 产生背景

项目早期已经优化过记忆机制，聊天中也讨论了结构化工作记忆、记忆评测和调优。后来设想每个 Agent 都有长期 Session、还能读取共享信息时，用户进一步问：在一个很大的系统里，怎样维护和扩展上下文，才能让 Agent 每次都准确拿到真正相关的信息。问题因此不再只是“记住更多”，而是要避免把不同 Thread、不同项目、旧结论和未经验证的推测混在一起。

### 真正要解决的问题

哪些信息属于当前调用、单个 Agent、当前 Thread 或长期共享记忆？系统又如何防止取到过期、越权或不相关的内容？

### 当前回答

记忆分为调用内工作上下文、AgentSession 私有连续性、经过验证的 Thread/Project 共享事实，以及可失效/替代的长期与实体记录。Agent 只能提交 candidate memory；具有明确来源、Scope、版本、ACL、新鲜度和验证证据的内容才能进入共享视图。

未来 Context Compiler 应为每次 Invocation 产生最小、不可变的 ContextBundle，ContextManifest 记录加入、排除、裁剪和拒绝的内容及原因。检索顺序先 Scope fail-closed，再考虑 Thread/Project、Role、Task/Route 依赖、实体关联、证据等级、版本、新鲜度和 Token 预算。向量库或知识图谱只有在固定检索评测证明现有方法不足时才引入。

记忆调优不应早于来源绑定、失效、权限和可观测性。正确顺序是先建立固定检索任务和无 Memory 基线，再分别测量命中率、错误引用、过期信息、泄露、Token 和延迟。通用 Context/Memory 产品化仍属于 PROD-05，当前不应抢在持久 Runtime 之前扩展。

## 12. 多 Agent 应该如何并行、交流并最终收敛？

### 产生背景

用户希望多个 Agent 像多条长期存在的工作线程：没有依赖的事情可以同时做，有依赖的事情必须等待前序结果，而且交流过程应该真实发生。现有 Planner→Developer→Tester 更像固定流水线；但如果只是把所有 Agent 一起启动，又会出现重复调查、同时改同一文件、拿着旧输入继续工作，甚至一直讨论却没人负责停下来。

### 真正要解决的问题

哪些工作可以并行、哪些必须按顺序，Agent 用什么方式交换结果，以及谁根据什么条件决定已经收敛？

### 当前回答

需要独立判断、不同证据源、无冲突资源或审查分离的工作可以并行；依赖前序 Artifact、共享 Workspace、相同外部资源或有不可逆副作用的工作保持顺序。每个 Agent 只提交不可变候选结果，Runtime 根据依赖、版本、资源范围、权限和预算决定是否创建后续 Invocation。

Agent 通过 proposal、question、challenge、response、handoff、review_blocked/approved 和 human_required 等结构化 Message/Handoff 交流。Runtime 设置最大轮数、链深、并发、重复消息、无进展、Token、费用和人工升级门禁。收敛不依赖多数投票；Agent 负责提案、反驳和审查，Runtime 根据 AcceptancePolicy、阻断问题、证据和用户决定生成结果。

当前 TaskGraph 的依赖、并发和冲突检查是 Coding 纵向切片已有能力；普通 Thread 的持久 Handoff、RouteEdge、DiscussionPolicy 和动态协作运行路径仍待 PROD-04。

## 13. Runtime 如何确保模型没有“把推测变成事实”的权力？

### 产生背景

在讨论“缺少能力怎么办”和“通用需求协议是什么”时，用户直接追问过：如何让 Runtime 没有给模型把推测写成事实的权力。触发这个问题的原因很实际：模型完全可以生成一段格式正确的 JSON，并在里面写 `passed=true`、`verified`、`accepted` 或“测试已经执行”，但一个字段写得像真的，并不能证明命令、浏览器或验证器真的运行过。

### 真正要解决的问题

模型的陈述怎样一直保持为候选或推测，只有什么主体、拿着哪些证据，才有权把它升级成已验证事实或已验收结果？

### 当前回答

真相权属于 Runtime 而不是字符串。Agent 产出的对象一律是候选 Claim、Proposal、Patch 或 Evidence。用户原始 Message、上传媒体和工具原始回执可以作为权威的“来源记录”持久化，但它们的语义解释并不因此自动 verified。派生事实、verified Claim 和 accepted Outcome 必须来自 Runtime 执行/验证的命令、工具证据、Artifact hash、媒体绑定、独立 Validator 与匹配 Policy。`unknown` 不等于 passed，执行成功不等于 Acceptance。

AcceptancePolicy 由 Runtime 或可信插件注册。AcceptanceRecord 必须绑定正确 subject/id/version、Policy hash、新鲜且不重复的 Evidence 和必要的独立 Evaluator。当前 PROD-01A 已实现值协议和负向不变量；“只有 Runtime 可写入”的真正安全边界必须由 PROD-01B 的权威 Store 和事务授权完成，不能只靠 dataclass 声称已解决。

## 14. Invocation、Attempt、Outcome 和 Thread 为什么必须分开？

### 产生背景

当项目开始考虑长期 Agent、并行执行、取消、超时、自动重试和事故恢复时，旧的 `running/completed` 一组状态不够用了。一次模型调用返回，可能只是某次尝试结束；用户任务还没验收，进程和端口也可能没回收。反过来，已经取消或超时的旧尝试还可能迟到返回结果，如果没有独立身份和失权机制，它会把新一轮执行的状态覆盖掉。

### 真正要解决的问题

如何分别表示长期对话、一次逻辑工作、它的某次实际尝试、最终验收和资源清理，确保失败、重试和取消都能确定退出？

### 当前回答

Invocation 是一次逻辑执行意图，Attempt 是它的一次实际领取/执行，Outcome 是 Turn、Task、ScenarioRun 或外部动作的验收结果，Thread 是用户长期可见的协作空间。它们的生命周期不能互相推导。

Invocation/Attempt 又分开 execution state 和 cleanup state。只有执行已终止、清理达到 `REAPED`、活动 Child/Grant/Lease/Resource 为零并且 fencing 已让旧 Attempt 失权，才能 closed。重试之间 Invocation 可以保持 RUNNING，但必须没有活动 lease 和有效 fence。超过 deadline 的结果不能仍记为 succeeded，旧 fence 的迟到 Artifact/副作用也必须被拒绝。

这些值协议和同步 admission 不变量已在 PROD-01A 实现；权威索引、事务状态、真实 claim/heartbeat 和资源回收仍属于 PROD-01B/01C。

## 15. 权限分离、安全和隔离怎么设计？

### 产生背景

用户先提出了每个 Role 的工作边界、权限分离和隔离问题。随后桌面系统又弹出“修改 Mac 上的 App”和“请求录屏”的授权提醒，用户分别追问为什么需要这些权限。这让抽象的安全设计变成了一个清楚的产品边界：用户上传一张图片，不代表允许系统录制屏幕；用户要求 Coding，也不代表 Agent 可以修改宿主 App、读取其他窗口或获得整台机器的权限。

### 真正要解决的问题

每个 Role、Worker 和 Invocation 到底能读什么、写什么、调用什么？哪些操作默认禁止，哪些必须由用户针对当次操作单独批准？

### 当前回答

有效权限是 `RolePolicy ∩ WorkerCapabilities ∩ Thread/Task Policy ∩ Invocation CapabilityGrant ∩ RuntimePolicy`。Role 是职责和权限上限，不是授权本身。每个 Invocation 获得短期 Grant，Agent 只能提出工具或路由请求，不能扩大权限。高风险或不可逆操作需要 Approval、短期凭据、幂等键和回执。

Scope、Thread、AgentSession、Project/Workspace、Artifact、Memory、媒体和秘密都必须有明确边界。默认禁止网络、秘密和共享写入；文件操作需要 Workspace 路径验证，命令使用 argv 白名单、环境清理和资源限额。Producer、Reviewer 和 Validator 的 principal 需要按场景分离。当前 Coding 纵向切片已有一部分边界；通用 CapabilityGrant、Tool Gateway、Secret Broker 和副作用账本属于 PROD-03。

用户上传、引用或要求分析图片/视频，不等于授权 Runtime 主动截屏、录屏、打开摄像头、读取其他窗口或修改宿主 App。这些行为必须是独立的 Tool Capability，并在当次操作获得明确 Approval，不能从“任务包含媒体”自动推导权限。

## 16. 如何把 Agent 交流和“为什么最后收敛”展示在 Web 页面，又不泄露思维链？

### 产生背景

用户打开当前 `127.0.0.1:8765` 页面后，希望参考 CatCafe：每个 Agent 像一条线程存在，页面既能看出并行和先后依赖，也能看到它们提出了什么意见、发生了哪些交接，以及为什么最后选择当前结果。用户要的不是几张会动的 Agent 卡片，而是一条能解释最终结论的真实协作记录。

### 真正要解决的问题

Web 页面应该展示哪些可核验事实和公开理由，才能解释 Agent 如何收敛，同时不伪造过程、不暴露原始思维链？

### 当前回答

页面应该展示 Thread/Turn 时间线、Agent 泳道、Invocation/Attempt 状态、输入与输出 Artifact、公开的简短理由、Handoff 中的 What/Why/Tradeoff/Open Questions/Next Action、权限/预算、Validator Evidence、未解问题和 Runtime Decision。`Why` 是可公开、可验证的决策摘要，不是原始思维链。

界面不从日志文本猜测状态，而是投影 Runtime 已持久的 Message、Artifact、RouteEdge、RuntimeEvent 和 AcceptanceRecord。因此完整 Agent 泳道 UI 应放在 Thread/Journal/Mailbox 之后，不能在后端事实源尚未存在时先制作一个动画假象。

当前 Web 仍主要展示 Coding DAG、Artifact 和验证事件；它是兼容纵向切片，不是已完成的 Thread/Agent 协作控制面。

## 17. 如何设计评测，避免网页美学标准过于抽象和任务集过拟合？

### 产生背景

在真实基线评测中，用户允许了最多 51 次调用和 60 万 Token，随后追问“对比评价结果的基准是什么”。看到视觉评分后，用户指出同一个网页本来就可以有很多合理设计，单一相似度很抽象；又进一步质疑为什么测试集总是写网页或修 Bug，认为这种评测已经开始把整个项目塑造成一个专用 Agent。

### 真正要解决的问题

应该选什么样的任务和客观证据，才能公平比较单 Agent、工具反馈和多 Agent 协作，又不会让某一个测试场景反过来定义 Core？

### 当前回答

Core 评测不使用单一网页或 Bug 场景代表全产品，而是分层报告持续交互、多 Agent 协作、多模态理解和插件/工具任务。每层分开记录成功率、事实错误、恢复/取消、人工介入、成本和延迟，不汇总成一个掩盖差异的分数。

Coding 插件应同时包含新功能、Bug 修复和行为保持重构。Core 与新的跨场景评测不依赖抽象视觉审美；但当前 VisionForge 插件仍会把 VLM `passed`、分数阈值和未解 P1/P2 作为它自己的硬 Quality Gate。未来是否把视觉相似度降为辅助证据，必须在 VisionForge 场景内用固定任务、人工标注和校准数据决定，并同步 Backlog 与测试，不能把新策略写成当前行为。固定样例与隐藏保留样例分开；只对某个 fixture 有用的规则留在插件，不进 Core。

单 Agent、带浏览器/工具反馈和完整多 Agent 方案必须在相同预算下做消融对比。Mock/Fake 只证明 Harness 协议正确，真实模型 Canary 评估 Adapter 和模型效果，真实仓库 dogfood 才评估工程价值。三者不能互相代替。

## 18. 线上出 Bug 后如何排查？事故学习闭环在产品纠偏后是否还需要？

### 产生背景

用户先问过“线上出了 Bug 应该如何排查”，项目因此规划了事故记录、回放和防复发闭环。后来产品中心从 Coding Bug 修复回正为以通用 Runtime 为执行内核的交互式 Multi-Agent Harness，用户又追问：既然不再围绕修代码 Bug，事故学习闭环是否也应该修改。真正的矛盾是，旧表述确实偏 Coding，但取消这条链又会失去 Runtime 对超时、权限越界、迟到结果和资源泄漏的审计与恢复能力。

### 真正要解决的问题

事故闭环应该保留哪些通用能力、移除哪些 Coding 假设，又如何用可回放证据证明问题确实被修复且没有引入新的误判？

### 当前回答

事故学习闭环仍然必要，但必须从“Coding 修复回顾”泛化为 Runtime 横向能力：`RuntimeEvent → Detector/Invariant → IncidentLedger → EvidenceBundle → Replay/FaultInjection → ChangeSet → Shadow/Canary → LearningItem → GuardrailEvaluation`。事故事实、根因假设、修复提案和已激活规则必须分开。Agent 可以整理证据和提案，不能篡改事故事实、自己批准 Guardrail 或宣布事故关闭。

排查顺序应先冻结 Scope/Thread/Invocation/Attempt/Artifact/版本和时间窗，再沿 correlation/causation 追踪事件，区分业务失败、权限拒绝、超时、迟到结果、上下文污染和清理失败。只在可回放证据重现后修复，并在 Shadow/Canary 阶段同时测量漏检和误报。当前只完成 INC-00 文档与 PROD-01A 的部分协议地基；持久 Journal、Detector、Ledger、Replay 和运营指标仍未实现。

## 19. Core 应该立即分布式、微服务化或引入向量数据库吗？

### 产生背景

随着讨论扩展到长期 Agent、共享记忆、事件 Journal、多 Worker 和大规模上下文，很容易顺势加入消息队列、PostgreSQL、对象存储、向量数据库、知识图谱和多个微服务。但此前 VisionForge 和网页评测已经展示过一次“实现手段反过来带偏产品方向”的风险，所以用户又明确要求先修改 Core，不要继续无边界扩展基础设施。

### 真正要解决的问题

当前最小可用的基础设施是什么？出现哪些可测量的瓶颈后，才有充分理由升级到分布式存储、远程 Worker 或语义检索？

### 当前回答

第一阶段保持 self-hosted、单组织/单信任域、单机优先的 production-shaped modular monolith。模块按 Thread/Message、Invocation/Session、Event/Incident、Artifact/Context、Capability/Tool、Scenario/Plugin 和 Web/API 划分，但不因为有模块边界就立即拆进程。

PROD-01B 选用 SQLite 建立本地权威 Thread/Message/Invocation Store；当前 SQLite 主要服务于旧纵向切片的 Memory、TaskGraph/Scenario Snapshot 和 Checkpoint，它们不等于 01B 的新 Runtime 权威 Store。只有真实锁竞争、吞吐、可用性、数据规模、隔离或检索评测达到事先冻结的阈值，才引入外部队列、PostgreSQL、对象存储、向量库或远程 Worker。基础设施数量不是项目成熟度指标。

## 20. 接下来应该按什么顺序推进？

### 产生背景

整个聊天中，工作先后经过记忆机制、VisionForge、Vue 页面、真实模型评测、线上事故和通用 Harness/Runtime 内核，用户也多次用“下一批”“还有几批”控制一次只实施一个批次。方向发生偏移后，用户明确要求“先修改 Core”，并要求重新读取和同步 HANDOFF，目的就是避免每出现一个新想法就插队，让后面的实现再次跑偏。

### 真正要解决的问题

当前唯一应该做的下一批是什么？哪些看起来有价值的功能必须暂缓，直到 Runtime 的持久化、恢复和权限基础完成？

### 当前回答

当前顺序已冻结为：

1. `PROD-01B`：SQLite 权威状态 Store、append-only Journal、Outbox、最小 BudgetLedger 和持久查询，状态/Event/Outbox/预算同事务提交。
2. `PROD-01C`：durable enqueue、claim/lease/heartbeat、fencing、Watchdog/Reaper、级联取消和重启恢复。
3. `PROD-01D/01E`：将当前 TaskGraph/Scenario/Coding 纵向切片接入 Thread，提供持久 Web 查询，并建立第一批 Observe/Shadow 事故信号。
4. `PROD-02`：Backend v2、SessionBinding、Streaming、usage 和硬取消。
5. `PROD-03`：CapabilityGrant、Tool Gateway、Secret Broker 和执行隔离。
6. `PROD-04`：Mailbox、结构化 Handoff、动态协作控制面和 Agent 泳道。
7. `PROD-05/06/07`：Context/共享记忆、插件与效果/容量评测，以及迁移/事故运营。

PROD-01A 已经完成纯领域协议、Coding 单向兼容映射和负向不变量测试，但它没有接入 SQLite Store、队列、旧 Executor、Mailbox、模型 Backend 或 Web 执行路径。在 01B/01C 完成前，不优先扩展更多模型、自由 A2A、复杂长期记忆、向量数据库、微服务或完整 Agent 动画界面。

## 已被后续讨论修正的旧答案

| 旧答案或默认 | 当前结论 |
|---|---|
| 项目中心是 Coding Bug 修复 | Coding 是目标插件/纵向切片，项目本体是多模态 Multi-Agent Harness，Runtime 是 Core 执行内核 |
| VisionForge 可以代表整个项目 | VisionForge 只是显式注册的视觉 Web 场景，UI Spec 和视觉评分都留在场景内 |
| 只要有图片就让所有 Agent 使用 VLM | 只在无法由工具替代的视觉语义节点调用 VLM，之后回到 LLM |
| build/test 是所有 Thread 的统一完成条件 | 每个场景注册 AcceptancePolicy；普通交互不强制产生代码或 TaskGraph |
| Agent 成功或多数投票可以宣布完成 | Agent 只产生 Evidence，Runtime 依据 Policy 签发 AcceptanceRecord |
| 所有通信都塞进任意 Artifact | 大内容/证据用 Artifact，Message/Handoff/Event/Acceptance 使用类型化控制协议 |
| Agent 长期存在等于模型调用或供应商 Session 永不结束 | 长期的是身份、Mailbox 和 Runtime AgentSession；模型调用是短生命 Invocation |
| 记忆越多越好，应该尽快加向量库 | 先做 Scope/ACL/来源/时效/失效和检索评测，只在证据足够时升级存储 |
| 先做 Agent 泳道页面就能体现多 Agent | 先有持久 Message/Event/Handoff 事实源，页面只做可审计投影 |

## 相关来源

- `HANDOFF.md`：当前产品方向、已实现事实、下一批和验证命令。
- `Plan/Plan26.md`：交互式多模态 Multi-Agent Harness 产品定位、Runtime Charter 与 PROD 顺序。
- `Plan/Plan25.md`：事故学习闭环和运营边界。
- `Plan/闭环覆盖范围.md`：事故覆盖的可证明范围与当前缺口。
- `FAQ.md`：Spec、Artifact、UI Spec、Worker 路由和 Acceptance 等历史细节参考；其中仍有 VisionForge 中心和旧批次表述，与 `HANDOFF.md` / `Plan/Plan26.md` 冲突时不可覆盖当前决议。
