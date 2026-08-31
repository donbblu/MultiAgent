# Coding Agent 优化记录

> 每日自动记录 Coding Multi-Agent 的改进、问题修复和待优化事项。

## 2026-08-02

### 已优化

- 完善 Coordinator、Coding Agent、Verification Agent 三模块协作。
- 增加受限工作区、真实文件生成和自动测试返工流程。
- 完成第一阶段：任务协议、Schema 校验、上下文筛选、命令白名单、逐条验收和运行记录。

### 已解决

- 解决原 Demo 仅模拟修改和验证、未真实操作项目的问题。
- 增加路径穿越防护、命令超时及失败反馈。
- 解决模型计划未经校验、整个代码库可能进入上下文、任务过程无法持久化的问题。

### 待优化

- 接入真实大模型 Coding Backend。
- 增加资源隔离和高风险操作审批。
- 增加工作区快照与回滚、敏感信息脱敏和模型调用指标。

## 2026-08-03

### 已优化

- 明确 Coordinator、Coding Agent、Verification Agent 与 Runtime 的职责边界。
- 接入 DeepSeek API CodingBackend，并增加精确命令授权、受保护路径和独立验收脚本。
- 将模型层重构为供应商无关接口、工厂、注册表和 OpenAI-compatible 协议适配器。
- 引入与 Agent、模型供应商解耦的 RoleSpec 和角色注册表，支持 planner、implementer、reviewer、tester、fixer 动态职责。
- Coordinator 按规划、实现、验证和返工阶段注入角色，并记录角色切换历史。
- Worker 在运行时检查写入或验证能力，避免只依靠角色提示词控制权限。
- 增加 Multi-Agent 可视化界面，支持直接输入需求并实时查看角色交接、实现、验证和返工事件。
- 提取 CLI 通用执行服务，使命令行和界面共享供应商配置、工作区权限与验证策略。
- 增加当前架构与权限边界信息图，覆盖角色分工、模型层、受控 Runtime 和禁止操作。
- 离线回归测试扩展至 20 项，角色切换、能力拒绝、运行记录和原工作流均通过。

### 已解决

- 解决模型可能接触密钥、修改验收脚本、自选验证命令和多文件写入失败的问题。
- 解决角色仅靠提示词约束的问题，增加写入与验证能力的运行时检查。
- 解决 CLI 与展示界面执行逻辑重复的问题，统一使用同一任务执行入口。

### 待优化

- 增加操作系统级网络、CPU、内存和进程隔离。
- 增加工作区版本快照、并行任务租约和集成冲突检测。
- 将 Reviewer 接入主流程，并实现 TaskSpec、WorkerPool 和 Scheduler 以支持安全并行调度。

## 2026-08-04

### 已优化

- 引入不可变 RoleMemoryView、MemoryPolicy 和 MemoryManager，按角色裁剪任务上下文。
- 为 implementer、fixer、reviewer 设置独立项目文件预算，tester 不接收项目源码。
- Coding Backend 和 Verification Worker 改为消费角色 Memory View，保留现有 CLI 与 Web 工作流。
- 将 Tester 与独立 Reviewer 接入并行质量阶段，减少实现后串行等待。
- 引入 ResultEnvelope 和 Task Version，由 Coordinator 校验并单点合并并行结果。
- Web UI 增加双角色并行状态、Reviewer 结果和结果封装事件展示。
- 统一 AgentMessage 交流协议，覆盖请求、交接、结果、反馈、状态和最终通知。
- Agent 消息增加任务版本、发送方、接收方、关联 ID、JSON Payload 与 UTC 时间。
- Web UI 改为展示 sender → recipient 消息流，旧执行事件仅保留用于审计。
- 自动化测试扩展至 29 项并全部通过。

### 已解决

- 解决 Worker 可直接读取完整 TaskContext、角色间上下文缺少最小权限隔离的问题。
- 禁止任何角色配置密钥访问，并限制 fixer 仅接收验证反馈、tester 仅接收获准命令。
- 解决 Reviewer 只注册未工作的资源闲置问题，并防止迟到结果覆盖新任务版本。
- 解决 Agent 交接格式不一致的问题，并递归拒绝消息 Payload 中的敏感字段。

### 待优化

- 将 Planner 接入真实执行流程，并为其持久化独立 PlanningResult。
- 基于 TaskSpec 拆分互不重叠的实现子任务，进一步并行多个 Implementer。
- 隔离验证子进程的环境变量、网络、CPU、内存和进程权限。

## 2026-08-05

### 已优化

- 无新增。

### 已解决

- 无新增。

### 待优化

- 无新增。

## 2026-08-14

### 已优化

- 新增仓库级 HANDOFF.md，将当前架构、设计决策、限制、关键文件和下一步压缩为新任务可直接读取的交接状态。

### 已解决

- 解决长对话切换到新窗口时需要复制全部历史、Token 消耗高且关键上下文容易遗漏的问题。

### 待优化

- 后续阶段结束时同步更新 HANDOFF.md，并以代码、测试和 Git 状态校验摘要是否过期。

## 2026-08-15

### 已优化

- 将产品方向聚焦为 VisionForge，多模态完成参考图、Vue 页面生成、浏览器验收、视觉审查与自动修复闭环。
- 增加模型能力声明与 DeepSeek/Qwen 按角色路由，并完成经授权的文本、视觉最小真实烟测。
- 建立图片内容寻址、UI Spec/Visual Review 协议、Playwright 浏览器门禁、视觉 Fixer、恢复 Checkpoint 和 Web Artifact 调用链。
- 建立三个固定页面、三种交付方案的评测框架与真实调用预算门禁；默认回归扩展至 123 项通过。

### 已解决

- 解决页面生成只看代码测试、缺少真实浏览器和视觉验收的问题。
- 解决视觉修复副作用重复、Workspace 漂移、模型自行宣告通过及不同方案反馈边界不清的问题。
- 首次真实基线暴露并修复嵌套布局区域协议缺陷，供应商连接失败按原样保留为诊断证据。

### 待优化

- 用户重新确认额外调用预算后，以新 Run ID 重跑校准后的真实模型基线，不覆盖首次失败报告。
- 对少量结果执行人工盲审，校准视觉阈值、P1/P2 严重级别及 VLM 与人工判断差异。
- 补充 Web 任务持久化、并发端口隔离和运行中取消。

## 2026-08-13

### 已优化

- 引入动态 Task DAG、资源冲突检测和 ready 队列，并发执行相互独立的子任务。
- 建立 Artifact 交接与集中 Patch 合并，CLI/Web 默认接入 DAG 引擎并保留 legacy 回退。
- 增加感知、Working、长期和实体记忆，使用 SQLite 持久化 Checkpoint，并仅晋升已验证结果。
- 单元测试扩展至 58 项，覆盖拆图、并发、重试、恢复、合并冲突和端到端验证。

### 已解决

- 解决固定工作流难以并行、Agent 直接写共享目录及失败后整链重跑的问题。
- 解决任务恢复缺少持久记忆、未经测试结果可能被标记完成及合并失败状态悬挂的问题。

### 待优化

- 验证失败后动态生成局部 FixTask，并只重跑受影响验证。
- 将超时和取消传递到模型请求与验证子进程，支持运行中安全中断。
- 增加路径模式级冲突分析、完整图状态恢复和实体索引。

## 2026-08-07

### 已优化

- 将项目定位调整为供应商无关的 Coding Agent Harness，并新增声明式 WorkflowSpec、NodeSpec、WorkerRegistry 与 CancellationToken。
- 保留 Coordinator 兼容入口，新增 CodingHarness 控制面，按 Role 动态解析 Worker。
- Web 界面增加工作流 DAG、实时事件时间线和节点详情，展示状态、权限、耗时、产物及关联事件。
- 增加 Harness、可视化和 Fixer 条件节点测试，自动化测试扩展至 36 项并全部通过。

### 已解决

- 解决固定 Agent 拓扑与控制职责混杂的问题，并增加工作流依赖缺失和环检测。
- 解决未触发 Fixer 长期显示“等待”造成的误解，区分无需返工与尝试次数耗尽。
- 修复可视化界面初始节点详情为空和最终结果区域提前显示的问题。

### 待优化

- 让 WorkflowSpec 真正驱动通用 DAG Executor，继续移除 CodingHarness.run 中的硬编码分支。
- 增加节点级超时、执行中取消、Checkpoint 恢复和统一 Execution Gateway。
- 将轮询事件更新升级为 SSE，并补充 Token、模型调用和关键路径指标。

## 2026-08-08

### 已优化

- 新增 Harness 架构边界问题清单，按 P0、P1、P2 记录 11 项已确认问题、风险、目标和完成标准。
- 明确优先从 NodeInput/NodeResult、移除 active_role 隐式依赖和并行不可变输入开始演进。

### 已解决

- 无新增。

### 待优化

- 让 WorkflowSpec 真正驱动执行，并明确 Planner 是真实节点还是 Harness 内部准备阶段。
- 统一 Worker 契约与 Execution Gateway，落实 Memory Scope 和运行中取消。
- 按实际复杂度评估拆分 ProjectWorkspace 与 RunRecorder 的混合职责。

## 2026-08-09

### 已优化

- 无新增。

### 已解决

- 无新增。

### 待优化

- 评估引入受控 Task Board、版本化通用 Artifact 和固定 Coding 评测集。
- 补充 Docker、本地模型适配与可量化的成功率、返工率、Token 和耗时指标。

## 2026-08-11

### 已优化

- 无新增。

## 2026-08-12

### 已优化

- 无新增。

### 已解决

- 无新增。

### 待优化

- 无新增。

## 2026-08-18

### 已优化

- 无新增。

### 已解决

- 无新增。

### 待优化

- 无新增。

## 2026-08-19

### 已优化

- 统一使用 ScenarioRuntime、ScenarioProfile 和 ConvergenceDecision 控制多轮 DAG、收敛与终态。
- WebVisualScenario 接入通用 TaskGraphExecutor，并使用 SQLiteScenarioRunStore 持久化轮次、Artifact 和恢复状态。
- Web 与评测入口切换至统一场景 Runner，移除旧 Coordinator、WorkflowSpec 及重复通信、录制和审查路径。

### 已解决

- 解决通用 Coding 流程与 VisionForge 各自维护 Runner、恢复和 Artifact 接纳逻辑造成的重复与状态分叉。
- 解决 Worker 可直接接纳共享 Artifact 的边界问题，改由 Runtime 统一接纳 ArtifactDraft。

### 待优化

- 实现 RequirementEvidence、通用 CodingRequirement 和 Runtime 拥有的 Validator Profile。
- 建立固定本地 Coding 任务、隐藏验收和单 Agent/多 Agent 对照评测。

## 2026-08-20

### 已优化

- 完成 Core 场景插件、事实权、RequirementEvidence、CodingRequirement、Validator Profile 与 Role-first Worker 路由。
- 建立固定离线 Coding 任务、隐藏验收、三方案消融、模型 Worker、调用预算和可复现报告。
- 将 VisionForge 适配为可选 web_visual 插件，并接通图片需求证据的授权、完整性和幻觉检查。
- 默认回归扩展至 192 项通过，4 项真实浏览器类保持显式运行。

### 已解决

- 解决模型可混淆事实与建议、角色路由退化为固定 Agent、插件能力污染 Core，以及不同方案验收条件不一致的问题。
- 解决图片证据可能未经授权外发、引用损坏或被模型补造内容的问题。

### 待优化

- 接入音频转录与录屏证据，同时保持最终代码结果由同一确定性 Validator 裁决。
- 扩充固定任务与真实消融样本，评估多 Agent 对成功率、修复率、Token 和延迟的实际影响。

## 2026-08-21

### 已优化

- 增加供应商无关的音频转录与视频感知证据链，保留时间戳、来源、不确定性和原始 Artifact 引用。
- 建立统一 Multimodal Intake，将 text/image/audio/video 并行转换为可追踪 Evidence Bundle，再交给普通文本 Planner。
- 更新 Web 界面、FAQ 与项目交接资料，默认回归扩展至 213 项通过。

### 已解决

- 解决普通 Coding Agent 重复读取原始媒体、媒体处理失败后被静默忽略以及模型感知结果被误当作事实或验收的问题。
- 解决不同输入模态改变最终代码完成标准的问题，所有路径继续复用同一确定性 Validator。

### 待优化

- 对当前 Core 多模态 MVP 进行里程碑验收，由用户决定提交后的真实供应商适配、产品入口或固定多模态评测方向。
- 真实媒体调用前继续要求明确授权、费用预算和不可覆盖的运行证据。

### 已解决

- 无新增。

### 待优化

- 无新增。

## 2026-08-22

### 已优化

- 将项目方向明确为生产导向的 Multi-Model Coding Agent Harness，并建立 PROD 主线与 INC 事故学习双轨联动规则。
- 规划事故学习闭环的一等领域模型，覆盖 Runtime Event、Detector、Incident Ledger、Evidence、Replay、Guardrail 与 SLI/SLO。
- 补充闭环覆盖率、漏检概率、故障注入和统计置信口径，避免用已知测试通过率代替生产风险结论。

### 已解决

- 澄清 Core 多模态 MVP 完成不等于生产 Runtime 就绪，事故闭环也不能退化为 Agent 反思 Prompt 或直接写入 Memory。
- 明确 Agent、Runtime、Artifact、Verification、Memory 与人工审批的事实权和权限边界。

### 待优化

- 完成 PROD-00 对 Backlog、Learning Path、Runtime Charter、事故目录和验收口径的统一冻结。
- 按 PROD/INC 双轨顺序实现 Event Journal、Incident Ledger、Detector、Evidence、Replay 和 Guardrail；当前仍为规划，未改变 Runtime 行为。

## 2026-08-23

### 已优化

- 将 Core 从 Coding 修复流程纠偏为可长期交互、支持多 Agent 协作的多模态 Runtime，Coding 与 VisionForge 保留为专业场景能力。
- 新增 Scope、Thread、Turn、Message、Agent Profile/Session、Invocation/Attempt、Acceptance 和 RuntimeEvent 的严格领域协议。
- 建立 Coding 单向兼容适配器，并以 lease、fencing、双状态轴和幂等 mutation 约束取消、迟到结果与资源清理。

### 已解决

- 解决 Coding 语义反向定义 Core，以及 Invocation 成功、Outcome accepted 和 Thread archived 相互混淆的问题。
- 解决跨 Scope/Thread 引用、伪造 Acceptance、输入漂移和旧 Attempt 迟到写入缺少确定性拒绝协议的问题。
- 64 项定向协议测试和 277 项默认单元测试通过，4 项真实浏览器测试按设计跳过。

### 待优化

- 实现 PROD-01B 的 SQLite 状态 Store、append-only Journal、Outbox、最小 BudgetLedger 和持久查询。
- 继续 PROD-01C 的 durable enqueue、claim/lease/heartbeat、Finalizer/Reaper、重启恢复和级联取消。

## 2026-08-24

### 已优化

- 建立 Harness Evolution Protocol，以冻结 Baseline、可证伪假设、单一 Mutation、Validation/Held-out 和保留或回滚决定约束行为优化。
- 冻结 `local_trusted_execution/v1` 的本地可信执行边界、Profile、清理屏障和 A～H 验收口径，并明确它不是生产 Sandbox。
- 整理影响项目策略的关键问题与当前回答，并将 Skill 自动沉淀收敛为延后的、只读可审计候选箱。
- 复核默认回归口径为 277 项执行、273 项通过、4 项真实浏览器测试跳过。

### 已解决

- 解决脚本、Fake Model 或小样本结果可能被误述为真实模型、Held-out 或生产收益的问题。
- 解决三个子进程入口安全语义不一致却可能被笼统描述为沙箱的问题，并区分 Incident Replay 与 Harness 泛化评测。
- 明确自动发现的重复经验不能直接激活为 Skill，必须先分类正确落点并经过人工批准、独立评测和 Shadow。

### 待优化

- 继续 PROD-01B 的 SQLite Store、Journal、Outbox 与 BudgetLedger，不让评测协议或 Skill 候选箱改变当前优先级。
- 获得明确授权后实现并验收 `local_trusted_execution/v1`；当前仅完成契约冻结。
- 扩充任务家族和密封 Held-out 后再运行真实 Harness Evolution Pilot；Skill 候选箱等待 PROD-05/INC-04。

## 2026-08-25

### 已优化

- 完成 SQLite UnitOfWork、Thread/Event 原子纵切、Outbox intent 三写及 claim/NACK/过期重领生命周期，并以 VerificationReport 固化切片证据。
- 补充事务、崩溃、并发和跨进程攻击回归；3B-1 收口时 397 项全量测试执行，393 项通过、4 项真实浏览器测试跳过。
- 冻结 `SEC-EXEC-01` 本地可信执行 A～H 验收红卡，并明确后续顺序为安全门禁、Transport 发布闭环、预算与查询恢复。

### 已解决

- 修复 UoW 绕过、Event 历史改写、损坏数据被幂等快路径掩盖，以及 Outbox 跨行 ownership/lifecycle 校验缺失等问题。
- 将 `codex/multimodal-coding-mvp` 以 Fast-forward 合入 `main` 并推送；两个分支最终指向同一提交和目录树。

### 待优化

- 实现并验收 `SEC-EXEC-01` 统一执行 Supervisor；当前只有冻结契约和预期失败证据，不能称为生产 Sandbox。
- 完成 01B-3B-2 的 Transport publish/ACK/Receipt、最小 BudgetLedger、持久查询与 CLI/Web 主链接入。
- 补充 Outbox 1k/10k 容量、Writer Lock、p95 与长时稳定性证据。

## 2026-08-26

### 已优化

- 实现 `local_trusted_execution/v1` 的统一 Supervisor、一次性 Admission/Approval、五类执行 Profile、清理隔离恢复与限长脱敏输出。
- 将 Core、Legacy Workspace、VisionForge、CLI、评测和 Web 入口迁移到统一执行边界，并收敛为单一生产 `Popen` owner。
- 完成 25 项结构/行为门禁、39 项 POSIX 安全卡、零 target 证据，以及各一次可信 fixture 和 ProjectWorkspace 真实 happy-path smoke。

### 已解决

- 关闭父进程环境和假秘密继承、任意 argv/Profile 漂移、Workspace/Browser 路径逃逸、清理失败后继续执行及原始输出下游泄漏等缺口。
- 修正 EXPECTED_RED 中与 Admission 顺序、fixture 输入和静态扫描实体绑定有关的测试设计矛盾，并保留修订前后证据。

### 待优化

- 当前仍为 `INCONCLUSIVE / KEEP_NOT_ISSUED`；需完成用户可见 Composition Root、失败/超时/隔离路径、真实 Browser 对照和最终独立 Review。
- 在当前最终哈希上运行完整回归、compileall、静态无旁路检查和质量门禁后，才能决定是否保留该安全切片。
- 安全切片收口后继续 01B-3B-2 的 Transport publish/ACK/Receipt，不提前扩张为生产 Sandbox。

## 2026-08-27

### 已优化

- 提交 `local_trusted_execution/v1` 统一执行边界及聚焦验证报告，形成可复现的本地安全检查点。
- 新增持久 Agent/Session、SQLite Mailbox、同 Agent FIFO、跨 Agent 并行泳道和结构化 Handoff，并接入离线 Portfolio Demo。
- 将报告升级为 v2，记录 9 个 Thread、21 个 Agent/Session、42 条 Mailbox 收发、21 条阶段消息和 12 条跨 Agent Handoff；579 项非红卡全仓回归通过，9 项跳过。
- 将工程演示与用户产品重新分层，建立以本地 Web、真实模型 API 和可介入协作为目标的 PRODUCT-01A～01E 主线。

### 已解决

- 解决 Portfolio Demo 只把 DAG StageAudit 投影成 Agent、缺少真实身份、生命周期、Mailbox、私有状态和独立执行泳道的问题。
- 纠正“离线 CLI 工程候选等于用户产品完成”的口径，并以独立 Review 关闭文档与发布证据新鲜度问题。
- 确认角色与模型解耦、DeepSeek 优先纵切后接 Qwen/Kimi，以及 Runtime 中介的受控网状通信拓扑。

### 待优化

- 完成 PRODUCT-01A 的通信协议、ContextBundle、冲突处理、收敛条件和前端公开字段合同。
- 按同一 Provider 合同接入 DeepSeek、Qwen、Kimi，再将真实 Agent 协作接入本地 API 与 Web 控制台。
- Mailbox ACK/重投、崩溃恢复、多进程协调、真实 Browser、生产安全认证和大规模效果评测继续后置，不得由当前 MVP 冒领。

## 2026-08-28

### 已优化

- 将 Role、Agent 和 Model 的关系修正为任务级动态分配：Role 描述职责，Agent 保持稳定 Profile/Model Policy，Runtime 持久记录具体 Assignment。
- 新增 RoleRequirement、候选过滤与确定性 Scheduler、不可变 RoleAssignment，以及 SQLite v6 Assignment Store 和 Assignment+Mailbox 原子投递。
- 保持旧 AgentProfile JSON 兼容，并补充同分稳定选择、忙碌次优、强连续性等待、重复防护、显式替换和重开查询证据。
- 完成 10 项定向、184 项 Runtime 和 589 项非红卡全仓回归；9 项跳过，离线 Portfolio smoke 通过且真实模型调用为 0。

### 已解决

- 解决 Role 永久绑定 Agent 导致忙碌阻塞、能力错配和模型切换污染职责边界的问题。
- 明确 Planner 负责业务理解、拆分、DAG/RoleRequirement/验收条件，Runtime 独占具体 Agent/Session/Profile 的选择与调度权。
- 对 v6 checksum 漂移保持 fail-closed，保留旧运行库备份后重建当前库，没有改写迁移账本绕过校验。

### 待优化

- 冻结 Planner 输出、Runtime 拒绝、Message/Handoff、ContextBundle 和公开/私有字段的完整通信协议。
- 明确无合格 Agent、Planner 方案被拒、Assignment 修订循环、冲突处理和收敛上限。
- 后续产品编排器仍需接入 Provider 健康、预算、工具、Context 和实时负载证据；当前尚无真实模型/API/Web 链路。

## 2026-08-29

### 已优化

- 将产品推进方式调整为“小步冻结＋垂直切片”：每次只确定一个最小协议，立即完成 TDD、完整链路和一次真实 Provider 验证。
- 实现 `agent-action/v1 send_message`，把模型结构化 Action 经 Runtime 严格校验、RoleAssignment、Message 持久化和 Mailbox 原子投递串成首条真实通信链。
- 为 `recipient_role` 动态生成规范 Role ID 枚举和同值 Prompt 约束；切片 7 项、相关 41 项、Runtime 184 项和 596 项非红卡全仓回归通过，9 项跳过。
- 完成两次获授权的 DeepSeek smoke：首次真实暴露 Role ID 不匹配，修复后一次调用即完成 assigned、Message 持久化和单条 Mailbox 投递。

### 已解决

- 解决模型输出自然语言 Role 名称而 Runtime 只接受规范键，导致 Provider 调用成功但消息无法路由的问题。
- 保持 Runtime 精确匹配和未知 Role 的 `needs_input` 语义，没有用大小写猜测、模糊匹配或二次模型路由掩盖合同缺口。
- 解决先完整设计所有协议再编码造成反馈过晚的问题，改为保留失败证据并由真实纵切驱动后续修订。

### 待优化

- 由用户选择下一条最小产品纵切；当前不自动扩展到接收 Agent 回复、Handoff、Artifact、多播或其他 Action。
- ContextBundle、冲突/Debate、收敛与前端公开信息仍需逐项冻结和实现。
- Qwen、Kimi、真实协作 API 与 Web 控制台尚未接入；现有 DeepSeek smoke 只证明单条 SEND_MESSAGE 链路。
## 2026-08-30

### 已优化

- 实现一跳接收 Runtime，以 `context-bundle/v1` 在 Planner 与 Reviewer 间传递最小上下文，不携带完整历史或私有推理。
- 将审查对象绑定到消息正文或消息关联的不可变 Artifact；缺失、越权、不可用及上下文超限均按 fail-closed 处理。
- 增加内联内容引用去重与 `SEND_MESSAGE` 幂等冲突检测；完成 DeepSeek 双 Agent 实跑及回归验证。
- 补充 Provider API 工程自检清单，覆盖超时、取消、流式解析、凭据隔离、重试与健康快照。

### 已解决

- 解决传输成功但 Reviewer 没有实际审查对象、只能给出泛化建议的问题。
- 解决审查正文重复注入，同时保留消息审计与历史可追溯性。
- 解决同一幂等标识对应不同请求时错误重放旧消息的问题。

### 待优化

- 确定下一条产品主线：ACK/claim、ASK_USER/FINISH、冲突处理或本地 Product API。
- 将 Provider API 自检项落实到 DeepSeek、Qwen、Kimi 等供应商无关适配层。
- Mailbox 仍缺少 ACK、失败重试、进程崩溃后的重新投递机制，Web/API 入口仍待建设。

## 2026-08-31

### 已优化

- 将首版执行路线调整为 CLI-first，并以统一 AgentExecutor 保持 Backend 与供应商解耦；Codex CLI 的认证、JSONL、只读 Sandbox 和脱敏结果链路完成真实复验。
- Agent 工具环境改为 `inherit=none` 加固定安全 PATH，配合 Runtime-owned 布尔哨兵验证 `CODEX_HOME` 不向工具进程泄漏。
- 增加显式 Task/Snapshot/Permission/Artifact 状态信封、SQLite 不可变 Authority、完成结果重放以及 Backend Session 私有持久绑定。
- 增加不可变 Recovery Context、单次 Session 重建合同与显式失败证据门；全仓非 expected-red 回归达到 647/647，9 项跳过。

### 已解决

- 解决仅靠模型短答或环境变量黑名单无法可靠证明工具环境隔离的问题。
- 解决 Runtime 重启后任务状态、完成结果和 Agent Backend Session 连续性无法可靠恢复的问题。
- 解决 stderr 文本可能被误判为 Session 失效并触发错误恢复，以及损坏 JSONL 可能泄漏原始诊断的问题。

### 待优化

- 当前 Codex CLI 未提供可安全映射的结构化 Session 失效信号，需确定人工确认恢复或一次有界自动 fallback 策略。
- 真实有效 Session resume、恢复、取消及其余 CLI 错误分类仍待验证。
- 精确 ChangeSet 逐次用户批准门、Mailbox ACK/重投、本地 API 与 Web 产品入口仍待实现。
