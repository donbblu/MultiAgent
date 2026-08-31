# Plan30：产品优先的 Multi-Agent 可用纵切计划

日期：2026-08-27

状态：**当前产品主线。Plan29 已完成的是 Runtime 工程演示里程碑，不是用户产品完成。**

## 为什么纠偏

前一阶段把“可复现的 CLI 验收候选”误写成了“作品集产品完成”。这两个概念必须分开：

- 已完成的是内部技术底座和确定性验收工具：持久 Agent/Session、SQLite Mailbox、同 Agent FIFO、跨 Agent 并行、结构化 Handoff、Artifact/Validator，以及 scripted/offline CLI 回归。
- 尚未完成的是用户真正使用的产品：任意任务入口、真实模型驱动的 Agent 协作、可观察和可介入的 Web 控制台、结果与历史体验。
- 已有 scripted/offline CLI 只作为底座回归和故障诊断工具；它与后续接入的“自带完整 Agent Loop 的成熟 Agent CLI”不是同一个入口。用户仍通过 Web 使用产品，成熟 Agent CLI 作为后端执行器。

## 最小产品目标

交付一个本地单用户 Web 应用。用户可以输入任意任务，系统创建一个 Thread 和固定核心 Agent，通过成熟 Agent CLI 的独立非交互执行会话完成单 Agent 内部工具循环；项目自己的 Runtime 负责 Planner 分解、RoleAssignment、Message/Mailbox、Context、Handoff、收敛、审计和用户介入。用户能够看到公开的结构化 Message、Handoff、Agent 状态、Artifact 和 Validator 结果，最后获取结果并重新打开历史记录。

这条纵切必须使用现有 Runtime 真状态，不能用前端假动画、静态时间线或 scripted fixture 冒充真实 Agent 协作。

## 已确认的 Agent 执行接入决定

- 产品主路径调整为 **成熟 Agent CLI 优先**。CLI承担单 Agent 内部的思考、工具调用、观察和继续执行；项目不再把从零补齐可靠 Raw API Agent Loop 作为第一版产品前置条件。
- 项目继续独占多 Agent 外层控制面。CLI只能执行Runtime分配的一次Invocation并返回事件/结果，不能私下调用其他Agent、直接写Mailbox、决定RoleAssignment、扩大权限或自行宣布最终验收通过。
- Runtime通过统一`AgentExecutor`/`FullAgentBackend`接入CLI。首个CLI、认证方式、非交互输出格式、权限和Session策略由用户确认后再冻结；不得在未确认前复制多个供应商专用工作流。
- 现有`ModelClient`和DeepSeek真实smoke保留为`RawModelBackend`对照与后续学习资产，不删除、不回写成CLI证据。原定DeepSeek、Qwen、Kimi三家API不再阻塞第一版Web产品，是否继续作为跨Backend实验在首个CLI纵切完成后重新确认。
- CLI登录凭据、API Key和访问令牌只由CLI自身或操作系统凭据存储管理，不写入前端、SQLite Agent状态、Message/Handoff、日志、Artifact或模型上下文。Runtime只记录脱敏的Backend、CLI版本、模型标识（若可得）、Usage/额度（若可得）和执行证据。
- Cat Café 的stdout/stderr、进程退出、半帧JSON、僵尸进程、取消和Session串线问题重新成为产品接缝的真实故障实验；但CLI成功退出仍不等于任务语义通过，最终Acceptance继续由Runtime-owned Validator、独立Reviewer或用户决定。

## 禁止自主进化与用户逐次批准

本产品不提供自主进化。Agent不得根据对话、评测分数、失败记录、投票或自身判断，自动修改或激活Agent Prompt、Role/Profile、模型策略、工具/权限、通信/终止规则、Skill、Runtime路由、Validator/Acceptance规则或系统自身代码。Plan26中的Harness Evolution只是人工开发与评测纪律，不是Runtime能力，也不授权自动Evolver。

Agent最多只能生成`ChangeProposal`候选，明确列出目标、理由、精确diff或配置、风险、验证证据和不可变`change_digest`；初始状态只能是`PROPOSED / PENDING_USER_REVIEW`。只有用户看过该精确版本并对该digest单独明确批准，Runtime才可以建立一次性`USER_APPROVED`记录并允许后续应用。提案内容、目标范围、权限、依赖或digest发生任何变化，旧批准立即失效，必须重新提交用户检阅；Agent投票、Reviewer通过、Validator全绿、历史`KEEP`或一次长期授权都不能代替用户批准。

当前仓库没有自动Evolver或自动采纳路径，但现有`workspace-write`只提供文件写权限，Composition的一次性执行批准也不是上述产品级用户批准。因此在面向产品启用Developer修改本项目自身或上述治理面之前，必须先以TDD实现`PROPOSED → USER_APPROVED → APPLIED`硬门，并按精确digest绑定用户决定；门禁未实现时，这类变更只能以只读提案Artifact呈现，不能应用。

用户已确认最终关系为 **Role与Agent动态解耦，Agent通过Profile与主要Model Policy间接、版本化绑定**：

- Role描述当前任务需要的职责、约束、能力和输出合同，不永久属于某个Agent。
- Agent Profile描述稳定身份、专长、能力、工具/Context/预算策略和主要Model Policy；AgentInstance固定引用一个Profile版本，不能在Session中静默换模型。
- Runtime为Task/Invocation创建持久`RoleAssignment`，记录Role、被选Agent、候选集合摘要、选择理由和版本。一个Role可由多个Agent承担，一个Agent也可在不同任务承担不同Role。
- 调度先执行能力、权限、工具、Context、Provider健康和预算等硬过滤，再按适配度、可用性、质量、成本/延迟和当前负载排序。最佳Agent忙时，只能选择仍通过硬门槛的第二候选；没有合格Agent时进入排队或`needs_input`，不得强派。
- Assignment一旦进入执行不能静默换Agent；失败或取消后的重新分配必须形成新的Assignment/Handoff并保留因果链。排序同分使用稳定规则，确保测试和复现。
- 实际Invocation仍记录Backend、CLI/Provider、模型、版本和可取得的Usage。第一版为减少能力差异，核心Agent优先使用同一CLI/模型但隔离Session；跨模型Review后置为显式实验，不让同一Agent在Session中静默“换脑子”。

当前Runtime的`AgentProfile.role_ref`是必填，因此实现这一决定需要一个小型领域协议与SQLite迁移，把Role移到任务级`RoleAssignment`。AgentInstance/Session、Mailbox、FIFO/并行、Message/Handoff和Runtime-owned Validator保持不变，不重写执行内核。

## 通信与协作的当前提案

以下是 `PRODUCT-01A` 的通信与协作合同。通信拓扑已经用户确认，其余条目仍需逐项确认；未确认条目不因写入本计划就视为最终决定：

- 用户已确认拓扑采用 **Runtime中介的受控网状通信**：判断层Agent逻辑对等，通信层全部经过Runtime路由、持久化和审计，执行层使用SOP/DAG与受控动态Handoff。默认一对一，多接收者必须显式列出；Agent不得私下直连或自由广播任务正文。完整决策对比见 [`主产品线遇到的问题.md`](../主产品线遇到的问题.md)。
- 用户已确认团队任务采用 **Planner语义拆分＋Runtime确定性分配**：没有`@`的任务先请求Planner Role；Planner提交DAG、RoleRequirement和验收条件，Runtime通过持久RoleAssignment选择具体Agent/Session/Profile。直接`@`单Agent走单播，显式`@`多Agent走独立多播；Planner不得指定具体Agent，Runtime不得解释原始业务或生成计划。完整对比见 [`主产品线遇到的问题.md`](../主产品线遇到的问题.md) 的DEC-003。
- 通信协议首切已确认为 **结构化Action＋现有持久Message/Mailbox**。`agent-action/v1 send_message`只允许`schema_version/action/recipient_role/content`；Runtime从Invocation上下文生成Thread、发送者、具体接收Agent、ID、时间、因果和幂等身份。首切不建独立Handoff实体，不允许Artifact、多播或接收者自动回复。其他Action在对应纵切前再冻结。
- 协作底层以 **消息路由器为主、已验证事实黑板为辅、发布订阅只用于 Runtime/UI 事件**。不采用所有 Agent 共享完整聊天记录的“大黑板”，避免权限混乱和上下文污染。
- 每次调用由 **Context Compiler** 生成最小上下文包，不默认灌入完整历史。首个`context-bundle/v1`纵切已冻结并离线实现白名单`task_goal/recipient_role/trigger_message/review_subject/verified_facts/artifact_refs/constraints/allowed_actions`；评审任务的`review_subject`必须绑定已持久化Message正文，或绑定并解析该Message携带的不可变Artifact引用。两者皆无时进入`needs_input/missing_review_subject`，Artifact未绑定或不可读取时同样fail-closed。必需字段超预算显式进入`needs_input/context_overflow`，可选项按“约束→已验证事实→Artifact引用”稳定裁剪并报告`omitted_refs`。Token计数器由调用方注入，当前不冒充Provider精确Tokenizer。
- 用户已确认借鉴MCP式的 **协议无状态、业务状态显式化** 边界：每次工具或Agent Backend调用必须是独立、自描述的请求，不依赖连接内隐Session；任务进度、记忆、权限、预算和恢复点只由Runtime持久化，并通过`task_id`、`invocation_id`、`snapshot_id`、`permission_snapshot_id`和`artifact_id`等稳定引用显式传入。Backend/CLI Session ID只允许作为可替换的连续性优化，不能成为业务真相；Session丢失时Runtime必须能从快照与Artifact重建下一次调用。工具结果只能返回公开事件、Artifact引用和候选状态变化，由Runtime校验并提交，不得直接改写任务状态。
- 工作流以 **SOP/DAG 为主，关键决策和审查节点允许有限 Debate**。Master-Worker 只用于明确可拆分的并行工作，不让一个 Master 成为所有信息和判断的单点瓶颈。
- 收敛以 **状态机为主**，轮次、Token、时间、工具调用和连续无新证据次数作为硬上限；Voting 只提供建议，不能覆盖 Validator、安全门禁或用户决定。
- 所有模型主张默认是未验证候选。工具证据、Validator、独立 Reviewer 或用户确认才能升级为事实；`unknown` 永远不等于 `passed`。
- 当前 DAG 是项目自研的 `TaskGraph/Executor`，不是 LangGraph。首个产品纵切继续复用现有实现，不为框架名额外迁移。

## 已确认的交付方法

用户确认采用 **小步冻结＋垂直切片**，不再等待PRODUCT-01A所有问题一次设计完才实施：

1. 每次只讨论并冻结一个足以指导当前实现的最小决策。
2. 紧接着打通一条用户可观察的完整链路，不只实现孤立领域对象。通信首切的目标链为 `模型输出Action → Runtime校验 → Message持久化 → Mailbox投递 → Agent收到`。
3. 功能使用TDD推进；每个可运行纵切结束时至少做一次用户授权的真实CLI验证，并记录进程、流解析、超时、取消、权限、Session、额度/费用和语义结果。纯领域或持久化子步骤先使用Fake Executor，不强迫每个红绿循环触发真实Agent。
4. 用户确认结果后再进入下一个最小决策/纵切。若真实实践推翻了已冻结决定，必须追加记录证据、原因和修订，不得把历史改写成“一开始就是对的”。

小步不表示可以跳过不可逆的基础边界。当前切片开始前仍必须冻结它直接依赖的结构化输出、权限/私密、幂等/因果和最小终止规则；其他Context、Debate、Voting和前端细节在首次使用前再分别冻结。

## 压缩后的实施批次

### PRODUCT-01A：产品流程与通信合同冻结

原预估的 **2～3 个专注小时** 作为分散在各纵切之前的总讨论预算，不再是编码前的一次性阶段门。Role/Agent/Model关系、通信拓扑、Planner/Runtime分权、SEND_MESSAGE与一跳ContextBundle现已确认并分别实现；任务冲突、Debate、更完整的收敛/评估和前端投影仍在对应纵切开始前再冻结。

### PRODUCT-01B：统一 AgentExecutor 与首个成熟 CLI

在首个CLI确认前暂估 **4～7 个专注小时**。冻结Runtime可观察的`AgentExecutor`合同，先用Fake Executor打通一次Invocation，再接一个成熟CLI的非交互模式；第一版不同时接多个CLI。最小合同必须覆盖：输入Context/Workspace/权限/预算、流式公开事件、最终结果、CLI/Session引用、错误分类、取消和脱敏。

本批恢复并落实[Cat Café第二课CLI工程化自检](https://github.com/zts212653/cat-cafe-tutorials/blob/main/docs/lessons/02-homework.md)在当前产品接缝中的真实含义：

1. 同时读取stdout/stderr，但只把规范事件或最终信封当协议；日志活跃不等于任务完成。
2. 处理半帧、粘包、空行、非协议日志、畸形JSON、输出上限和进程提前退出。
3. 分离首事件超时、空闲超时、单Invocation总时长和Thread总预算。
4. 取消使用`SIGTERM → 宽限期 → SIGKILL`并清理进程组；取消结果必须持久进入Invocation状态。
5. 明确工作目录、可写范围、网络、工具和审批策略；默认不继承与任务无关的环境变量或秘密。
6. CLI Session必须按Agent/Thread隔离；恢复只能使用Runtime持久化且匹配的Session引用，禁止串线。
7. 重试受Invocation幂等和预算控制；CLI失败不能重复投递Message或接受迟到结果。
8. CLI退出0、生成文本或修改文件都不是最终Acceptance；继续使用Artifact、Validator、独立Reviewer或用户证据。

已完成的Provider API自检和DeepSeek真实smoke保留为Raw API对照实验。Qwen/Kimi API Adapter、三Provider真实smoke和跨Provider协作从第一版产品硬门槛移至首个CLI纵切后的可选比较，不删除历史记录。

`PRODUCT-01B-CODEX-AGENT-EXECUTOR-CONTRACT`的Fake合同、受控进程Transport与认证桥接纵切均已完成。新增公开`CodexCliAgentExecutor.run(AgentExecutionRequest) → AgentExecutionResult`合同：请求携带Invocation/Thread/Agent、Workspace、read-only或workspace-write权限、超时和可选Session ID；Adapter只通过stdin传Prompt，消费`codex exec --json`的JSONL，公开Session、状态、脱敏工具/消息事件、Usage和最终消息，过滤reasoning、完整stderr及工具原始输出。新Session与显式Session ID resume均已通过公共边界测试。`SupervisedCodexCliTransport`复用现有唯一进程Owner、清理屏障和一次性Composition批准；Codex Profile只允许固定可执行文件、`never`审批策略、两档Sandbox、精确Workspace、新建或显式Session恢复，并使用`--ignore-user-config`减少不可复现差异。Prompt摘要绑定到执行批准但正文不进入argv或公开Manifest；其他既有Profile仍保持`stdin=DEVNULL`。认证桥接只向Codex主进程提供宿主`CODEX_HOME`路径，私有`HOME`保持不变。本机无模型探针已证明私有HOME单独运行是`Not logged in`，加入该桥接后恢复`Logged in using ChatGPT`，固定exec参数也通过CLI解析。

用户授权的首次真实read-only smoke已经执行且必须保留为失败证据。一次订阅Invocation成功返回Session、完整JSONL终态、shell公开事件和Usage，Sandbox报告`read-only`，Agent报告未修改Workspace；但Agent工具判断`CODEX_HOME`仍然存在，因此`status=failed / SMOKE_ACCEPTANCE_FAILED`。这推翻了“legacy `shell_environment_policy.exclude`参数可保证工具环境剔除该变量”的离线推断。官方当前配置参考也将`exclude`标为legacy并建议新配置使用`filters`，但仅凭一次模型观测尚不能断言是参数版本行为还是Agent对工具结果判断错误。首次调用耗时22689 ms，Usage为输入30574、缓存输入22016、输出139、reasoning输出42，说明成熟CLI即使执行极短任务也有明显固定上下文与延时成本。

离线候选修复已按TDD把唯一legacy规则替换为canonical `shell_environment_policy.filters={CODEX_HOME="exclude"}`，并明确禁止两种形式同时存在。本机同版本Codex在`--strict-config`下成功解析完整exec参数，定向55项及全仓615项（9 skip）通过；本批真实模型调用为0。该证据只证明规范配置已生成且当前CLI接受，不证明真实工具环境已转绿。PRODUCT-01B仍处于`CANDIDATE FIX / REAL VERIFICATION PENDING`，下一次真实复验必须由用户单独授权，不自动resume或重试。

用户随后授权一次新的真实复验；canonical filters下结果仍为`env_codex_home_present=true / SMOKE_ACCEPTANCE_FAILED`。机械链路继续全部通过，耗时16638 ms，Usage为输入30598、缓存输入22016、输出163、reasoning输出68。两次独立新Session均得到相同安全失败，因此“H1仅替换legacy配置即可修复”已经被否定，不再继续调换过滤语法或自动重试。下一纵切先让Runtime从shell事件的固定哨兵中提取布尔证据，而不是只相信模型最终短答；随后再用差分观测区分配置层未生效、CLI特殊注入和模型误判。PRODUCT-01B验收仍未通过。

`PRODUCT-01B-RUNTIME-OWNED-SAFE-SENTINEL`离线纵切已完成。Codex工具输出只允许通过整行固定哨兵投影`codex_home_present: bool`；其他原始输出立即丢弃，不进入Event/报告/异常。缺失或同时出现true/false时不选择任何值。Smoke验收现在要求恰有一条Runtime观察，并单独检查模型最终短答是否与工具观察一致；模型单方面声称安全不再能通过。新增三条公共行为测试经历独立红绿，定向58项、全仓618项（9 skip）、编译和diff-check通过，真实调用0次。

环境继承差分已经冻结并完成离线候选：Agent工具子进程从`inherit=core`收紧为`inherit=none`，只用`set`注入仓库既有固定安全`PATH`；`CODEX_HOME` canonical filter和默认秘密名排除继续保留为纵深防御。Codex主进程的认证桥不变，因此“CLI能使用订阅登录”与“模型调用的工具拿不到宿主认证环境”仍是两个独立边界。公共Executor红测先精确捕获旧`inherit=core`，最小修改后转绿；当前Codex严格解析完整参数成功，定向58项、全仓618项（9 skip）、编译和diff-check均通过，真实调用0次。该结果仍是`OFFLINE CANDIDATE`，只有Runtime在下一次用户单独授权的真实read-only smoke中直接观察`codex_home_present=false`，且权限、写入、公开输出和进程门槛同时通过，才可关闭这项安全验收。

用户随后授权且只执行了一次新的真实read-only smoke。Runtime从shell工具事件直接提取到唯一`codex_home_present=false`，模型固定短答与该观察一致；Session、shell事件、turn终态和read-only Sandbox均被观察到，Agent报告Workspace未修改。报告`status=passed`且进程退出0，耗时17912 ms，Usage为输入30671、缓存输入26112、输出153、reasoning输出32；没有resume、retry、第二Agent或第二次调用，也没有公开环境值、认证信息、工具原文、完整stderr或私有推理。由此可以关闭“Codex主进程认证桥泄漏到Agent工具环境”这一具体安全缺口；这不是PRODUCT-01B全部失败语义、产品级安全或生产认证完成。

### PRODUCT-01C：真实 Agent 协作后端与本地 API

预计 **6～9 个专注小时**。已完成的`RoleAssignment`、SEND_MESSAGE和Context纵切继续有效；下一阶段先把现有Executor请求扩展为显式状态引用信封，再把CLI Invocation接入现有Agent Runtime/Mailbox/Validator，并提供创建任务、状态/消息读取、暂停/恢复、用户补充/纠正、关闭和历史API。首个最小切片不批量重命名现有Thread/Invocation，也不先建通用MCP Server；只证明相同快照可重放、CLI Session丢失可重建、错误Task/Snapshot/Permission组合在调用前fail-closed。保留六个关键故障实验：不合格次优Agent被错误选中、同分选择不确定、双路由重复触发、Agent乒乓与不可取消、CLI公开输出泄漏、Session漏带Thread导致跨线程污染。每个实验必须先红、再修复、再留下回归和结构化教训。

新增`PRODUCT-01C-BACKEND-SESSION-BINDING`纵切：明确Harness的`agent_session_id`是业务侧Agent生命周期与Mailbox隔离标识，Codex CLI在`thread.started`事件中返回的标识统一称为`backend_session_id`，两者不得混用。Runtime按`scope_id + thread_id + agent_instance_id + backend_id`私有持久化唯一绑定；同一Agent后续Invocation可以显式resume，跨Agent、跨Thread或跨Backend复用必须在调用CLI前拒绝。`backend_session_id`不得进入Agent Message、模型Context或前端公开输出，审计只允许脱敏引用。该绑定仅是上下文连续性和性能优化：缺失、失效或无法恢复时，Runtime必须从权威Task Snapshot、Permission Snapshot、Message和Artifact引用重建Context并创建新Backend Session，任务正确性不得依赖CLI隐藏状态。验收至少覆盖首次调用捕获并保存ID、同绑定恢复、错误绑定零CLI调用、Session丢失重建，以及重建结果仍受相同Snapshot和权限约束。

`PRODUCT-01C-EXPLICIT-AGENT-STATE-ENVELOPE-V1`首个内存纵切已完成。`AgentExecutionRequest`现在必须携带单一Scope内的typed Task ref、带content hash的Task Snapshot和Permission Snapshot refs、带content hash的Artifact refs，以及本次声明权限。`AgentExecutionRuntime`按Invocation ID从只读Authority获取权威信封；不存在返回`state_not_found`，请求权限与信封不一致或任一ref/version/hash/Artifact顺序不同返回`state_mismatch`，均在底层AgentExecutor调用前拒绝。两条公共行为测试分别经历RED→GREEN：合法信封恰好调用Fake Executor一次；错误Permission Snapshot时Fake零调用。既有Codex smoke和Executor请求已机械补齐显式信封；定向60项、全仓620项（9 skip）、编译和diff-check通过，真实Agent调用0。本切还没有SQLite Authority、相同Snapshot持久重放或Session丢失后的Context重建，不能把内存Authority冒充恢复闭环。

`PRODUCT-01C-PERSISTED-AGENT-STATE-REPLAY`已完成离线纵切。Runtime SQLite schema升至v7，新增不可变`runtime_agent_execution_states`和`runtime_agent_execution_results`；权威信封与结果均以canonical JSON和SHA-256 digest持久，禁止update/delete/replace。`SQLiteAgentExecutionStateStore`同时实现Authority和Replay Store：Runtime先比对显式状态，再读取已完成结果；第一次Fake Backend完成后关闭并重建Database/Store/Runtime，相同Invocation重放仍返回原结果且Fake总调用数为1。Task、Task Snapshot、Permission Snapshot或Artifact ref任一变化，在重启后仍返回`state_mismatch`且Backend零调用。新重放测试先ImportError红后最小实现转绿；四类不匹配是已有精确比较在新SQLite Authority上的持久回归，未伪造新红测。定向63/63、全仓排除专用expected-red后630/630（9 skip）、py_compile和diff-check通过，真实CLI/模型/网络调用0。本切仍未实现`backend_id + backend_session_id`绑定、Session丢失Context重建或Backend完成后但结果提交前崩溃的claim/fencing闭环。

用户单独授权的`PRODUCT-01C-ROLE-ASSIGNMENT`纵切已完成：`RoleRequirement → RoleAssignment → 可选Mailbox投递`公共接口、role-neutral Profile兼容迁移、SQLite v6、确定性硬过滤/排序、等待或次优决策、同代防重/显式supersede、提交时Agent快照复核、选择证据和Assignment+Mailbox同事务均已实现。等待秒数由显式Policy传入，没有偷设产品常量。其他Action、本地API和Web仍留在后续讨论/批次；ContextBundle首切状态见下文。

`PRODUCT-01C-SEND-MESSAGE-V1`已按TDD和真实DeepSeek复验完成。首次真实smoke证明API鉴权和JSON输出成功，但模型Role字符串未与规范键`reviewer`精确匹配，Runtime按设计进入`needs_input/no_eligible_agent`且没有Message副作用。该真实踩坑的最小修复随后TDD转绿：Runtime把当前`role_candidates`规范键稳定排序后写入`recipient_role.enum`，并增加“必须原样复制、不得翻译/改写/使用显示名”的系统指令；列表外Role仍安全进入`needs_input`，不做模糊猜测。修复后复验使用`deepseek-v4-pro`，一次模型调用即返回可路由Action：RoleAssignment为`assigned`，接收者为`reviewer-agent`，Message成功持久化且Mailbox恰有一条消息；未触发协议修正，输入212、输出42、总计254 Token，Provider延时1310 ms。当前切片7/7、相关41/41、Runtime 184/184、全仓非expected-red 596/596（9 skip）、编译和diff-check通过；首次真实失败继续作为历史踩坑证据保留。

`PRODUCT-01C-RECIPIENT-CONTEXT-V1`已完成离线TDD实现并由用户运行一次真实DeepSeek双Agent smoke。机械链路全部通过：Planner和Reviewer各一次调用、零协议修正，两个Assignment均`assigned`，父子Message正文原样持久化，Reviewer触发消息已消费、Planner回复未消费，`auto_hops_used=1`且没有第三次调用；合计输入579、输出117、总计696 Token，Provider延时合计3351 ms。真实实践同时暴露语义缺口：Reviewer Context只有任务、触发消息、约束和“发送链已验证”事实，没有通信协议正文或可读取Artifact；Reviewer因此建议“明确JSON Schema”，而动态Schema已经实现。这次只能判定`TRANSPORT PASS / REVIEW QUALITY INCONCLUSIVE`，不能把runner的机械`status=passed`解释为有依据的协议评审。当前仍复用Mailbox“领取即推进、无ACK/崩溃重投”边界，没有SQLite v7持久pending Invocation Store。

该语义缺口已按用户确认的最小合同完成修复。短评审对象明确绑定触发Message正文，并在Context中以`review_subject.source=inline_message`原样提供；长对象使用Message持久化的`core:artifact`引用，由注入的Runtime解析器受控读取，并以`source=artifact`提供引用与正文。缺失对象返回`needs_input/missing_review_subject`，未随Message持久化的引用返回`needs_input/subject_artifact_unbound`，均不消费Mailbox、不调用Reviewer；评审对象属于必需Context，超过预算时不能被静默裁掉。离线新增纵切11/11、相关45/45、全仓非expected-red 607/607（9 skip）通过。

用户随后运行修复后的真实DeepSeek smoke并通过。Planner逐字把实际协议写入Message，Reviewer Context的`review_subject`与Message完全一致；Reviewer明确引用协议中的“Runtime持久化Message并记录parent与causation”，再指出协议正文没有说明重复投递/重试处理，因此本次可判定`TRANSPORT PASS / SUBJECT BINDING PASS / GROUNDED REVIEW PASS`。但这条建议不能直接解释为Runtime缺少幂等：现有SEND_MESSAGE已经使用确定性message ID、Assignment重放与Mailbox防重复；真正暴露的是被评审协议摘要遗漏了既有事实，并且Reviewer把分布式/网络重试扩展到了当前本地单进程范围之外。Context当时还把同一协议同时放在`review_subject.content`与`trigger_message.content`，形成可量化的重复输入。

该重复已按用户确认的兼容方案修复：持久Message、SQLite和通用`trigger_message.content`保持不变；inline评审对象的模型Payload改为`review_subject.content_ref="trigger_message.content"`，不再复制正文。Artifact模式仍保留Trigger的原始消息正文，并在`review_subject.content`提供独立解析内容。默认真实smoke协议也补充了已验证的确定性message ID与幂等重放事实。红测先以旧重复结构失败，最小投影修改后转绿；相关45/45、全仓非expected-red 607/607（9 skip）和编译通过。

用户随后完成修订后的真实DeepSeek复验：公开Bundle中inline Subject只含`content_ref`且Trigger正文完整，Reviewer仍能准确引用正文；Context估算由上次455降至378，Reviewer Provider input由553降至520。由于本次协议正文更长、Provider请求还包含系统Prompt等内容，只能判断去重方向有效，不能把差值全部归因于该字段。Reviewer进一步建议让输出content参与message ID并把同Invocation的不同content视为新消息；该建议有材料依据，但方案不采纳。同一Invocation/step定义同一幂等操作，若调用方用相同身份提交不同请求，正确语义应是`idempotency_conflict`，不是创建第二条消息；而当前重放在模型调用前返回原Message，也没有新的模型content可用于ID。请求摘要冲突检测作为后续“双路由/幂等键误用”故障实验候选，不阻塞本切片关闭。

用户随即授权完成该幂等冲突纵切。Runtime现在于模型调用前对规范请求信封生成摘要，覆盖ModelRequest消息/能力/Schema、Thread/Turn/Invocation/发送者/父消息/Artifact引用、允许Role ID和Assignment Policy；图片只纳入Artifact元数据与数据哈希。数据库只持久化64位摘要作为RoleRequirement ID的一部分，不新增Prompt副本或SQLite迁移，动态Agent忙闲/候选状态不参与摘要。相同`scope + invocation + step`且摘要一致时仍返回原Message；摘要不一致时返回`rejected/idempotency_conflict`，模型零调用、Message/Assignment/Mailbox零新增。TDD红测先复现错误返回旧Message，最小实现后转绿；SEND_MESSAGE 8/8、相关46/46、全仓非expected-red 608/608（9 skip）和编译通过。

### PRODUCT-01D：Web 协作控制台

预计 **4～6 个专注小时**。实现任务输入、Thread页面、Agent泳道、Agent/Profile/CLI状态、公开Message/Handoff、Artifact/Validator、运行状态、用户介入控件和历史入口。第一版不要求用户在页面切换供应商模型；页面只展示后端持久状态，不伪造模型私有推理。内嵌“AC全绿但不符合原始愿景”的上下文漂移实验，并让最终验收回读用户原始目标。

### PRODUCT-01E：产品 E2E 与可展示收口

预计 **2～3 个专注小时**。首个成熟CLI完成一次最小真实smoke；至少两个隔离Agent Session通过同一Runtime在一个Thread中完成真实协作；验证失败/需要用户输入、预算耗尽或取消中的至少一条路径，以及刷新/重开历史、启动说明和必要回归。第二CLI或跨Provider协作是后续对照实验，不阻塞第一版。只修复阻塞产品纵切的问题。

## 时间与取舍

在首个成熟CLI可用、认证方式明确且真实执行获得当次授权的前提下，从当前检查点完成第一版产品暂估还需 **16～25个专注小时**。其中包含首个CLI Executor、剩余协作/故障纵切、本地API、Web和产品E2E；建议另留 **3～5小时风险缓冲**，处理CLI版本、登录、非交互协议、权限、Session恢复和前端联调。首个CLI确定后再根据其实际接口校正，不把当前暂估冒充固定工期。

如果要求把Cat Café教程00～15课和全部作业都真实复刻，包括PWA/Rich Blocks、知识系统、Voice、完整Feature/Pack纪律等非当前产品关键内容，总时间仍按 **35～50个专注小时** 单独计算，不包含在当前16～25小时产品暂估内。第一版产品完成后再继续这些扩展，不让非关键课程阻塞产品入口。

### 可调整时间表

用户已选择 **压缩版**：目标为约4天、每天5～7个专注小时。稳健版只保留为发生重大外部阻塞时的回退参考，不作为当前计划。

| 时段 | 压缩版（每天5～7小时，约4天） | 稳健版（每天3～4小时，约7天） | 交付检查点 |
|---|---|---|---|
| 第1段 | Day 1 | Day 1 | 冻结最小Action合同并开始首条真实通信纵切；其余合同按切片前置门逐项冻结 |
| 第2段 | Day 1～2 | Day 2～3 | PRODUCT-01B首个成熟CLI通过统一Executor合同和真实smoke |
| 第3段 | Day 2～4 | Day 4～6 | PRODUCT-01C CLI Invocation、真实Agent消息链和六个关键故障实验通过 |
| 第4段 | Day 3～4 | Day 6 | PRODUCT-01D Web能够观察Agent/CLI状态并介入 |
| 第5段 | Day 4 | Day 7 | PRODUCT-01E双Agent隔离Session协作、失败路径、刷新历史和演示收口 |

压缩版仍以约4天为目标，但CLI进程、Session和权限接缝可能消耗第5天风险缓冲。压缩版要求首个CLI、认证、权限策略尽快确认，且只做基础视觉；任何CLI版本不兼容、额度限制或重大协议改动都应消耗风险缓冲，不通过删掉故障实验来追回时间。

用户已于2026-08-31把主路径从API-only修订为CLI-first：先用一个成熟CLI打通Executor→Runtime→Web完整纵切，并让多个Agent使用隔离Session；不同时接多个CLI。DeepSeek/Qwen/Kimi API计划保留为后续RawModelBackend学习和对照，不再阻塞前端或PRODUCT-01E。

为压缩时间，本轮明确不做：多用户/多租户、动态 Agent 市场、分布式队列、ACK/重试与崩溃重投、生产 Lease/Reaper、完整事故系统、移动端、原生桌面端、复杂可视化、任意Provider市场、生产安全认证和大规模效果评测。

## 产品后学习提醒门禁

PWA/Rich Blocks、Voice、完整知识系统、Pack纪律及其余未进入产品关键路径的Cat Café 00～15课程和作业统一后置为`LEARNING-POST-01`。它们不是取消，也不得静默标成完成。

当`PRODUCT-01E`准备声明完成时，执行者必须先明确提醒用户：第一版产品已到门槛，是否现在启动`LEARNING-POST-01`完整课程复刻。用户未确认前只保留待办，不自动扩大范围。这个提醒由产品里程碑触发，没有虚构日期，因此不创建定时提醒。

## 产品完成门槛

只有同时满足以下条件，才可以称为“第一版用户产品可用”：

1. 新用户能用一条明确命令启动本地 Web，并输入不是固定 fixture 的任意任务。
2. 首个成熟Agent CLI通过一次真实非交互smoke；至少两个隔离Agent Session通过持久化Message/Handoff协作，且公开状态可以刷新后重建。
3. 用户能看到真实 Agent、消息、产物和验证状态，并能至少执行暂停/恢复或补充/纠正中的一种介入。
4. Validator 或独立 Reviewer 给出可定位的证据，模型不能自行宣布任务通过。
5. 最终结果和历史可重新打开；失败、超时、预算耗尽和缺少输入有明确状态。
6. 离线 scripted CLI 继续通过，但只作为回归证据；真实Agent CLI smoke必须使用任意非fixture任务，二者不得混淆。
7. 仍然不宣称生产级、Runtime Acceptance、通用多 Agent 效果提升或对外发布完成。

## 下一动作

`PRODUCT-01C-SEND-MESSAGE-V1`及Context/幂等纵切继续有效；Codex AgentExecutor、受控Transport、Runtime-owned安全哨兵和默认拒绝工具环境已通过一次真实read-only安全复验。显式状态引用信封及调用前内存Authority门已经TDD转绿。下一步继续同一垂直方向：把Authority接到Runtime持久状态，先证明同一Snapshot可确定重放且错误Task/Snapshot/Permission/Artifact组合在Backend前拒绝；随后再做CLI Session丢失时从Snapshot/Artifact重建Context。以上继续只用Fake，不自动真实调用或应用系统演进。面向产品启用Developer对本项目自身或治理面workspace-write之前，必须先实现逐次用户批准的精确ChangeSet门禁。之后再处理CLI失败/重试语义、Mailbox ACK、ASK_USER/FINISH和本地产品API。
