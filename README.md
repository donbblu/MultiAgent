# Multimodal Multi-Agent Harness

这是一个以通用 **Multi-Agent Runtime** 为执行内核的、可交互且可长期演进的 **Multi-Agent Harness**。项目关注的不是堆叠 Agent 或模型数量，而是让不同专业 Agent 在明确上下文、权限、预算、生命周期和验收边界内协作，并让交付可追溯、失败可恢复、事故可学习。

固定术语：

- **Harness 是项目本体**：组合角色与模型策略、任务拆分与路由、Context/Memory、工具、协作、评测、安全、事故学习和 Skill；
- **Runtime 是执行内核**：管理 Thread、Message、Invocation、AgentSession、状态、并发、取消/恢复、权限、预算、Event 和 Acceptance；
- **Plugin 是专业能力**：Coding、VisionForge 及后续场景通过受控协议接入；
- **Model/Backend 是可替换负载**：只能提交候选 Artifact/Evidence，不能拥有状态、扩大权限或自行宣告完成。

```text
Multi-Agent Harness
├── Durable Runtime Kernel
├── Agent Orchestration / Context / Memory / Tools
├── Eval / Security / Incident Learning / Skills
└── Coding / VisionForge / future Plugins
```

## 当前状态

项目目前是 **production-shaped、尚未达到生产级的原型**。现有 Coding/VisionForge 纵向切片已经具备 DAG、Artifact、角色路由、受控工具、验证和局部修复资产；Runtime Kernel 已完成 `PROD-01A` 领域协议与 `PROD-01B-1` 的组件级 SQLite Schema/Migration/UnitOfWork 事务底座，完整 `PROD-01B` 仍在进行中。下一动作是先冻结 `PROD-01B-2` 的状态变更与 append-only RuntimeEvent 原子提交口径；当前尚不能声称 State Store、Journal、Outbox、BudgetLedger、持久队列、崩溃恢复、多租户隔离或生产自主演进已经完成。

## 验证

```bash
cd demo
python3 -m unittest discover -s tests -q
```

## 权威文档

- [HANDOFF.md](HANDOFF.md)：当前事实、边界、下一批与验证记录；
- [Plan/Plan26.md](Plan/Plan26.md)：Harness 产品定位、Runtime Charter 与 PROD 路线；
- [OPTIMIZATION_BACKLOG.md](OPTIMIZATION_BACKLOG.md)：生产演进 Backlog；
- [LEARNING_PATH.md](LEARNING_PATH.md)：开发与事故驱动学习路径；
- [Plan/闭环覆盖范围.md](Plan/闭环覆盖范围.md)：事故学习闭环的覆盖边界与限制。
