# 动态任务 DAG 与分层记忆执行方案（Plan06）

## 日期

2026-08-13

## 讨论主题

优化 Coding Multi-Agent Harness 的任务拆分、并行调度、Artifact 协作和分层记忆，并让新控制面接管 CLI/Web 的真实执行路径。

## 目标与背景

原流程虽然区分 Planner、Implementer、Tester、Reviewer 和 Fixer，但真实执行仍主要是固定节点流水线，能够并行的实现任务难以动态拆分；RoleMemoryView 也只提供单次上下文裁剪，缺少跨节点持久化、恢复、长期经验和实体关系。目标是让任务 DAG 决定何时执行，让 Role 决定所需能力，让 Artifact 和受治理记忆承担节点协作，同时保证权限、状态和验收仍由 Harness 确定性控制。

## 候选方案对比

| 方案 | 核心思路 | 优点 | 缺点与成本 | 风险 | 适用条件 |
|---|---|---|---|---|---|
| 继续扩展固定 Workflow | 在 plan/implement/test/review/fix 上增加并发分支 | 改动小、兼容性高 | 任务粒度仍绑定固定节点，难以按需求动态拆分 | 并发利用率和复用能力有限 | 需求结构稳定、流程简单 |
| 动态 Task DAG + Artifact | Planner 输出 TaskSpec，Harness 校验依赖、资源和 Artifact 后并发调度 | 拆分灵活、局部重试、角色与执行拓扑解耦 | 需要 Schema、图运行时、合并器和更多状态治理 | Planner 可能输出非法图或产生语义冲突 | 当前 Coding Harness，已选择 |
| 直接引入外部工作流/图数据库 | 使用成熟平台管理 DAG、持久化和图查询 | 分布式与查询能力完整 | 部署和学习成本高，当前单机阶段过度设计 | 抽象被外部平台提前固化 | 多机生产规模 |
| 只保留对话历史作为记忆 | 将历史消息持续加入模型上下文 | 实现简单 | 噪声和 Token 持续增长，权限和证据边界模糊 | 旧信息污染决策、泄露无关内容 | 短对话原型 |
| 分层记忆 + 统一检索 | 区分感知、Working、长期和实体记忆，主动触发与被动检索并存 | 可恢复、可治理、可按 Role 裁剪 | 需要存储、整合、失效和晋升规则 | 错误经验被长期化 | 当前 Harness，已选择 |

## 最终选择

采用动态 Task DAG、不可变 Artifact 交接、集中 Integration 和分层记忆。保留 WorkflowSpec 与旧 Coordinator 作为模板和回退路径；CLI/Web 默认切换到 DAG 引擎。第一阶段使用线程池和 SQLite 验证单机闭环，不立即引入向量数据库、图数据库或外部工作流平台。

## 选择理由

- 真正提高利用率的是对可交付任务建模并按依赖调度，而不是增加固定 Agent。
- TaskSpec 的依赖、读写范围、验收标准和 Artifact 能让并行条件被 Harness 校验。
- Worker 不直接修改共享状态或共享目录，降低覆盖、竞态和越权风险。
- Working Memory 与 Checkpoint 支持暂停、恢复和交接；长期记忆只晋升经过验证且有 Artifact 证据的结果。
- SQLite 足以验证当前单机 Harness 的持久化语义，成本低且供应商无关。
- 保留 legacy 引擎可以降低迁移风险，并为新旧路径提供对照。

放弃继续扩展固定流程，是因为它无法稳定表达需求特定的并发任务；暂缓外部平台、向量库和图数据库，是因为当前更需要证明拆分、恢复和记忆治理能提高成功率；不采用完整对话历史，是因为其不可控、不可验证且权限边界薄弱。

## 架构或流程

```text
用户需求
  → StructuredTaskPlanner
  → TaskGraph 校验（依赖 / Artifact / 冲突 / 权限）
  → TaskGraphExecutor ready queue
  → Worker 并发生成 ImplementationPlan Artifact
  → PatchIntegrator 集中检查与原子合并
  → 真实验证命令
  → 成功：完成并整合长期记忆
  → 失败：记录证据并进入 failed

MemoryRecord
  ├─ perception
  ├─ working + SQLite checkpoint
  ├─ entity
  └─ verified long-term
       ↓
MemoryManager 权限过滤与预算整合
       ↓
RoleMemoryView
```

## 执行步骤

1. 增加 TaskSpec、TaskGraph、资源冲突和 Artifact 生产依赖校验。
2. 增加 TaskGraphRuntime，原子领取 ready 任务并传播失败和阻塞状态。
3. 增加 TaskGraphExecutor，通过 WorkerRegistry 并发执行且只重试失败子任务。
4. 增加 ArtifactStore 和 PatchIntegrator，将共享目录写入集中到唯一入口。
5. 增加 MemoryRecord、TaskWorkingMemory、主动触发和被动检索接口。
6. 增加 SQLiteMemoryStore，持久化 MemoryRecord 和 Working Memory Checkpoint。
7. 仅在整图成功且真实验证通过后整合长期记忆。
8. 增加 StructuredTaskPlanner，并在非法图时携带校验错误修复一次。
9. 让 CLI/Web 默认使用 DAG 引擎，保留 legacy 回退和安全可视化事件。
10. 补充并发、冲突、局部重试、恢复、整合和端到端测试。

## 约束与风险

- 资源冲突当前使用精确 scope 字符串，尚未支持可靠的 glob 交集和符号级冲突。
- Planner 生成的图经过结构校验，但语义拆分质量仍需要评测集验证。
- 线程池无法强制安全终止正在执行的模型请求；超时字段当前主要是策略元数据。
- 暂停和取消只在 Worker 边界生效，验证子进程和 HTTP 请求尚未完整接收取消信号。
- SQLite 已恢复 Working Memory，但整张图的运行状态和不确定 running 节点尚未完整恢复。
- 长期记忆目前以已验证节点摘要为主，去重、失效、supersedes 和实体索引尚未完成。
- DAG 路径当前以 Implementer Patch 为主，Reviewer、Safety 和动态 FixTask 尚未成为完整图节点。

## 待验证事项

- 动态拆分能否在真实需求上降低端到端延迟，同时不提高合并冲突率。
- Planner 如何稳定生成互斥写入范围和正确的契约依赖。
- 验证失败后生成局部 FixTask 是否优于重新规划整图。
- 进程在 Worker 运行中退出时，任务应恢复为 pending 还是 recovery_required。
- 长期记忆是否实际提高成功率、减少返工和 Token 消耗。
- 如何将文件、符号、测试与 Artifact 建立实体关系并在变更后失效。

## 待办事项

- [ ] 验证失败后动态生成局部 FixTask，并运行受影响测试。
- [ ] 将 timeout/cancel 传递到 ModelClient 和验证子进程组。
- [ ] 持久化完整 TaskGraphRuntime、尝试次数和生命周期快照。
- [ ] 增加 glob/符号级读写冲突分析。
- [ ] 将 Reviewer 与 Safety 纳入 DAG 收敛门禁。
- [ ] 增加实体索引、记忆去重、失效和 supersedes。
- [ ] 建立任务拆分质量、并发收益、返工率和记忆命中率评测。
