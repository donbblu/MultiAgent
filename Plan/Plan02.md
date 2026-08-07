# Multi-Agent Memory、并行利用率与交流协议演进方案（Plan02）

## 日期

2026-08-05

## 讨论主题

在现有 Coding Multi-Agent 串行工作流上，确定 Memory 分配方式、提高 Agent 并行利用率，并统一 Agent 间的结构化交流格式。

## 目标与背景

项目已有 Coordinator、Coding Agent、Verification Agent、受限 Workspace 和真实模型 Backend，但早期实现存在三个主要问题：

- 所有角色共享完整可变 `TaskContext`，最小权限和上下文预算不足。
- 实现、验证、返工基本串行，多个角色大部分时间闲置。
- 角色交接分散在状态事件、结果对象和自由文本中，缺少统一消息协议。

目标是在保持供应商无关设计和现有 CLI/Web 兼容的前提下，逐步增加 Memory 隔离、并行执行和可审计通信，并避免过早引入分布式系统复杂度。

## 候选方案对比

### Memory 分配方案

| 方案 | 核心思路 | 优点 | 缺点与成本 | 风险 | 适用条件 |
|---|---|---|---|---|---|
| 全局共享 Memory | 所有 Role 共用一个 `TaskContext` | 简单、交接成本低 | 权限弱、上下文膨胀、并发覆盖风险高 | 角色读取或修改无关信息 | Demo、严格串行任务 |
| Role 独立 Memory | 每个 Role 获得独立上下文，通过结果交接 | 隔离强、Token 可控、适合多模型 | 需要交接协议、版本和一致性处理 | 副本过期或信息遗漏 | Reviewer/Tester 独立执行 |
| Blackboard | Agent 向中央黑板发布和领取结构化任务 | 解耦、异步、易扩展 WorkerPool | 需租约、幂等、重复消费控制，成本高 | 重复执行、事件乱序 | 动态调度和分布式执行 |
| 分层 Memory | 分为 Global、Project、Task、Execution、Role 层 | 生命周期和权限清晰 | 需要定义各层唯一写入者和同步规则 | 层间状态不一致 | 中大型长期项目 |
| Event Sourcing | 以不可变事件恢复状态 | 审计、回放、恢复能力强 | 事件版本、快照和迁移复杂 | 敏感信息一旦写入难清理 | 生产级恢复和可观测性 |
| 检索式长期 Memory | 按任务检索历史经验和项目知识 | 节省 Token、可复用经验 | 需要索引、版本和过期机制 | 错误或过期召回污染上下文 | 长期项目、知识积累成熟后 |
| 混合方案 | 分层 Memory + Role View + Blackboard + Event Store | 安全、扩展、恢复能力完整 | 组件最多、实施成本最高 | 一次性改造容易失控 | 最终生产架构，需分阶段落地 |

关键差异在于：共享 Memory 优先简单性；Role Memory 优先最小权限；Blackboard 优先异步调度；分层 Memory 管理生命周期；Event Sourcing 管理恢复和审计；检索式 Memory 管理长期知识；混合方案整合上述能力但成本最高。

### 并行利用率方案

| 方案 | 核心思路 | 优点 | 缺点与成本 | 采用情况 |
|---|---|---|---|---|
| 保持完全串行 | Implementer → Tester → Fixer | 最稳定、无写冲突 | Agent 利用率低，模型调用等待明显 | 放弃作为长期方案 |
| Tester 与 Reviewer 并行 | 实现后同时启动只读质量 Worker | 改动小、无并行写冲突、可立即提升峰值并发 | Tester 较快，整体模型并发仍有限 | 已选择并实现 |
| 多 Implementer 并行 | Planner 按模块生成互不重叠 TaskSpec | 模型并发和实现阶段利用率提升最大 | 需要 DAG、Write Set、合并和冲突检测 | 尚未实施，作为下一阶段 |
| 完整 WorkerPool/Scheduler | Worker 动态领取任意 Role | 资源利用率和扩展性最高 | 需租约、心跳、幂等、任务恢复，复杂度最高 | 暂缓至 TaskSpec 稳定后 |

### Agent 交流格式方案

| 方案 | 核心思路 | 优点 | 缺点 | 采用情况 |
|---|---|---|---|---|
| 自由文本对话 | Agent 直接传递自然语言 | 灵活、实现快 | 不可验证、难审计、易泄露和提示注入 | 放弃 |
| 沿用分散事件 | 使用 role、implementation、verification 等不同事件 | 已有实现可复用 | 字段不统一、UI 重复、关联困难 | 仅保留为底层审计事件 |
| 统一 AgentMessage | 固定消息头、类型、Payload 和关联 ID，由 Coordinator 路由 | 可验证、可追踪、适合并行和 UI | 需要 Schema、大小和敏感字段校验 | 已选择并实现 |
| 外部消息队列 | AgentMessage 通过 Broker 传递 | 支持分布式和可靠消费 | 引入运维和一致性成本 | 当前阶段暂缓 |

## 最终选择

1. Memory 采用分阶段混合路线：当前先实现 `MemoryPolicy + RoleMemoryView + MemoryManager`，保留 `TaskContext` 为权威状态，保留 `RunRecorder` 作为持久化事件记录。
2. 并行先选择低冲突的质量阶段：Tester 与独立 Reviewer 并行，Coordinator 使用 `ResultEnvelope + Task Version` 单点校验和合并。
3. Agent 交流统一使用 `AgentMessage`，Coordinator 为唯一消息路由器；原有执行事件继续保留用于审计，但不作为 Agent 对话格式。
4. 多 Implementer、Blackboard、完整 WorkerPool 和检索式长期 Memory 暂不实施。

## 选择理由

- RoleMemoryView 能以中等改造成本立即收紧权限和上下文预算，不需要重写现有 Workflow。
- Tester 与 Reviewer 都是只读质量任务，可安全并行，不会产生 Workspace 写冲突。
- ResultEnvelope 和 Task Version 为后续并行提供必要的迟到结果与版本覆盖防护。
- AgentMessage 统一交接字段，同时适用于串行、并行和未来消息队列。
- 当前项目规模尚不足以抵消 Blackboard、向量数据库和完整分布式调度的建设与运维成本。
- 多 Implementer 并行必须先具备可靠 TaskSpec、依赖 DAG、Read/Write Set 和合并策略，否则会牺牲正确性换取表面并发。

放弃或暂缓其他方案的原因：全局共享 Memory 不满足最小权限；纯 Event Sourcing 会扩大当前改造范围；检索式 Memory 尚无足够长期知识；完整 WorkerPool/Scheduler 在子任务边界未稳定前风险过高；自由文本交流无法可靠验证和审计。

## 架构或流程

```text
User
  ↓ AgentMessage(request)
Coordinator ── TaskContext（唯一权威状态）
  ↓
MemoryManager ── RoleMemoryView（不可变、按角色裁剪）
  ↓
Implementer / Fixer
  ↓ ResultEnvelope
Coordinator
  ├───────────────┐
  ↓               ↓
Tester          Reviewer
白名单测试       只读模型审查
  └───────┬───────┘
          ↓ 同一 task_version / correlation_id
Coordinator 校验并单点合并
  ├─ 全部通过 → AgentMessage(final)
  └─ 任一失败 → AgentMessage(feedback) → Fixer
```

统一 `AgentMessage` 包含：`message_id`、`task_id`、`task_version`、`sender`、`recipient`、`message_type`、`summary`、`payload`、`correlation_id`、`created_at`。

## 执行步骤

### 已完成

1. 定义五种 Role 及其 Capability。
2. 引入 RoleMemoryView 和角色独立上下文预算。
3. Tester 与 Reviewer 接入线程池并行阶段。
4. 引入 ResultEnvelope 和 Task Version 校验。
5. Reviewer 使用供应商无关 StructuredReviewBackend。
6. 定义 AgentMessage 六种类型：request、handoff、result、feedback、status、final。
7. Web UI 改为展示 `sender → recipient` 的统一消息流。
8. 自动化测试覆盖 Memory 隔离、真实线程并发、陈旧版本拒绝和敏感消息字段拒绝。

### 后续步骤

1. 让 Planner 输出持久化 `PlanningResult` 和 TaskSpec DAG。
2. 为 TaskSpec 增加 `read_set`、`write_set`、`allowed_paths`、依赖和独立验收标准。
3. 只并行调度 Write Set 不重叠的 Implementer。
4. 增加合并前版本校验、冲突检测和失败回滚。
5. 在单机 TaskSpec 稳定后再评估 Blackboard、任务租约和 WorkerPool。

## 约束与风险

- Coordinator 必须继续作为 TaskContext 和消息路由的唯一写入者。
- RoleMemoryView 不得包含密钥；AgentMessage Payload 递归拒绝 API Key、Token、Password、Secret 和 Authorization 字段。
- Tester 与 Reviewer 并行阶段只能读项目；Tester 仅能运行预先批准的命令。
- AgentMessage Payload 必须可 JSON 序列化且不超过 64KB。
- Reviewer 是模型判断，可能漏报或误报；最终完成必须同时满足真实测试和审查结果。
- 当前线程并行仅提升质量阶段利用率，Tester 很快完成时收益有限。
- 测试子进程仍可能继承环境变量，尚缺 OS 级网络、CPU、内存和进程隔离。
- 多 Implementer 并行前不得仅依赖路径命名推断冲突，必须有明确 Write Set 和合并校验。

## 待验证事项

- TaskSpec 自动拆分能否稳定生成互不重叠的 Write Set。
- 多模型并发下 API 限流、成本和重试行为是否可接受。
- Reviewer 对真实缺陷的召回率和误报率。
- ResultEnvelope 在 Worker 超时、重试和迟到返回场景下的幂等性。
- AgentMessage 摘要和 Payload 是否足以支持 Fixer，且不会将敏感测试输出写入事件日志。
- 并行 Implementer 的实际平均利用率能否达到预期的 60%～75%。

## 待办事项

- [ ] 实现独立 Planner Backend 和 PlanningResult。
- [ ] 定义 TaskSpec、Task DAG、Read Set 与 Write Set Schema。
- [ ] 实现安全的多 Implementer 调度与合并。
- [ ] 增加消息幂等键、消费状态和失败重放策略。
- [ ] 隔离验证进程的环境变量、网络、CPU、内存和子进程权限。
- [ ] 增加 Agent 活跃时间、等待时间、Token 和成本指标。
- [ ] 根据长期项目数据决定是否引入检索式 Project Memory。
