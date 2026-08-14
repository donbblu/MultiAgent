# 任务图与记忆机制

项目第一阶段将固定 Workflow 模板与实际执行任务分离：`WorkflowSpec` 继续描述业务流程模板，Planner 产生的 `TaskSpec` 则描述可独立验收、可单独重试的实际工作。Harness 必须先用 `TaskGraph` 校验依赖、环、Artifact 生产关系和资源范围，之后 `TaskGraphRuntime` 才允许 Worker 领取 ready 任务。

## 并发边界

任务只有在以下条件全部满足时才会进入 ready 集合：

- 所有依赖任务已经成功；
- 输入 Artifact 已经存在；
- 与正在执行以及同批选中的任务没有读写或写写冲突；
- 当前状态仍是 pending 或 ready。

任务通过 `read_scopes` 和 `write_scopes` 声明资源边界，通过 `input_artifacts` 和 `output_artifacts` 交换结果。共享接口应先成为契约 Artifact，再让前端、后端和测试并发工作。当前范围匹配为精确字符串匹配，未来再引入经过验证的路径模式匹配。

## 记忆边界

`MemoryRecord` 统一表示感知、短期、长期和实体记忆，记录来源、证据、作用域、角色可见性、可信度和版本。`TaskWorkingMemory` 保存当前任务正在使用的记忆引用、Artifact、节点摘要和反馈，并可生成不可变 checkpoint。

`TaskWorkingMemory` 同时是 Harness 维护的结构化任务进度投影。它分别记录节点的 pending/running/retrying/succeeded/failed 状态和尝试次数、Artifact 的验证与替代状态、带受影响文件和证据的失败观察，以及受影响测试和完整质量门禁结果。失败只有在最终完整门禁通过后才会标记为由对应 FixTask 解决。TaskGraphRuntime、ArtifactStore 和验证器仍是权威事实来源，Working Memory 不反向改变权限、调度或验证结论。

角色不会读取整张进度表。Fixer 只获得未解决失败与相关 Artifact，Tester 获得当前 Artifact 和质量门禁状态，Implementer 只获得同角色节点，Planner 与 Reviewer 获得节点摘要和当前 Artifact。SQLite Checkpoint 已能保存和恢复这张结构化进度表，但本阶段仍不承诺恢复完整运行中的 TaskGraph 或模型请求。

所有记忆都带有稳定的 `project_id`。角色读取必须同时满足项目、任务、scope、kind 和 visibility 约束；角色写入还必须匹配当前项目与任务，并通过 `MemoryPolicy.writable_scopes`。SQLite 启动时会为旧表补充 `project_id`，旧的无归属记录默认不会进入具有项目 ID 的检索结果。

长期记忆还带有 `semantic_key` 和 active、superseded、invalidated、expired 状态。相同事实再次确认时复用原记录并合并证据；同一语义键出现新内容时，新版本通过 `supersedes` 指向旧版本，默认查询只返回 active 且未过期的记录。Harness 可以显式提供原因使错误知识失效。所有 MemoryStore 在持久化前统一扫描摘要和嵌套 content，对常见密码、Token、API Key、Authorization Header 和私钥格式进行脱敏，原始敏感值不写入存储。

Harness 使用 `MemoryManager.trigger()` 主动响应 `task_created`、`task_claimed`、`verification_failed`、`task_resumed` 和 `task_completed` 等确定性事件。Agent 可通过 `MemoryManager.query()` 被动检索，但两种入口都会执行任务作用域和角色可见性过滤。最终输入仍是不可变的 `RoleMemoryView`，因此不会破坏 Role 与 Agent 的解耦。

## 安全规则

- Agent 只获得当前 Role 可见的记忆摘要，不获得其他 Agent 的原始推理。
- `restricted` 内容以及密钥不得写入记忆。
- 任务状态、权限、安全策略和收敛条件不由记忆改变。
- 长期记忆必须保留证据引用；当前阶段只提供统一记录接口，尚未自动晋升模型推测。
- Artifact 内容不直接复制进记忆，只保存引用和可验证摘要。

## 当前实现与后续阶段

默认 `MemoryStore` 是线程安全的进程内实现；需要跨进程恢复时可以使用 `SQLiteMemoryStore`。SQLite Store 使用短事务保存 MemoryRecord 和 TaskWorkingMemory Checkpoint。`TaskGraphExecutor` 会从 ready 集合并发认领无冲突任务，通过 `ArtifactStore` 传递不可变引用，只对失败子任务执行其 `retry_limit`，并在节点完成或失败时保存 Working Memory。整张图成功后，节点摘要才会作为带 Artifact 证据的长期记忆晋升。

验证失败后，DAG Runner 会把失败报告记录为仅 Fixer 可见的感知记忆，并按首轮 Patch 涉及的文件动态创建单节点 FixTask。Fixer 只读取这些相关文件和失败反馈，修复结果仍须作为 Artifact 通过 `PatchIntegrator` 的路径检查。ArtifactStore 明确记录 unverified、failed、superseded 和 verified 状态；修复合并后旧 Artifact 会指向替代它的新 Artifact。修复 Artifact 可以建议受影响测试，但只能选择任务原先授权的验证命令；之后 Harness 始终再次运行完整质量门禁。只有最终仍生效且显式验证通过的 Artifact 对应节点结果才会晋升长期记忆，并绑定自身 Artifact 与最终验证证据。返工次数有显式上限，耗尽后任务确定性失败。

当前仍有两个明确边界：资源范围使用精确字符串匹配；暂停和取消在 Worker 调用边界生效，不能强制杀死正在运行的模型请求或子进程。后续应把超时和取消信号继续传入 ModelClient 与命令执行器，再增加实体索引。向量检索与图数据库应在精确检索场景得到验证后再引入。

## 真实执行入口

CLI 和 Web 默认使用 `dag` 引擎，也可显式选择 `legacy` 回退到固定 Coordinator。DAG 引擎由 `StructuredTaskPlanner` 生成结构化任务图；非法输出会携带校验错误要求模型修复一次。实现 Worker 只产生 `ImplementationPlan` Artifact，不直接修改共享目录。所有 Patch 在 `PatchIntegrator` 中统一检查允许路径和跨 Artifact 文件冲突，然后由 Workspace 原子应用；合并后必须通过真实验证命令，任务和生命周期才能进入 completed。Web 只展示任务图、Artifact、状态和结果摘要，不公开模型原始推理。
