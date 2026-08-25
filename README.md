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

项目目前是 **production-shaped、尚未达到生产级的原型**。现有 Coding/VisionForge 纵向切片已经具备 DAG、Artifact、角色路由、受控工具、验证和局部修复资产；Runtime Kernel 已完成 `PROD-01A` 领域协议、`PROD-01B-1` SQLite Schema/Migration/UnitOfWork 事务底座、`PROD-01B-2` concrete Thread current-state + append-only RuntimeEvent 原子纵切，以及 `PROD-01B-3A` 的显式 `OutboxPolicy`、Schema v3、真实 v2→v3 迁移和 Thread+RuntimeEvent+Outbox durable intent 原子三写。完整 `PROD-01B-3` 与 `PROD-01B` 仍在进行中；下一切片是 `01B-3B` claim/publish/NACK/ACK/Receipt。当前不能声称可靠 Outbox 发布、完整 State Store/Journal、BudgetLedger、持久队列、崩溃恢复、多租户隔离或生产自主演进已经完成。

## 验证

`PROD-01B` 的版本、环境、命令、计数、故障/并发结果、真实缺陷、修复与回归位置、未覆盖风险、独立 Review 和最终决策统一记录在 [VerificationReports/PROD-01B.md](VerificationReports/PROD-01B.md)。Plan 定义契约，HANDOFF 只保存摘要；不要再从多个文档拼接测试结论。

```bash
cd demo
python3 -m unittest discover -s tests -q
```

当前 3A 门禁为 adversarial 22/22、directed 73/73、Runtime 159/159；默认全量执行 372 项，其中 368 通过、4 个既有真实浏览器 E2E 按环境门禁跳过，exit code 0。实现后独立挑战实际击穿并关闭 10 组产品缺陷；历史 5 项 EXPECTED_RED 只作为能力实现前的失败证据保留。精确命令、哈希和限制见上述 VerificationReport。

## 权威文档

- [HANDOFF.md](HANDOFF.md)：当前事实、边界、下一批与验证记录；
- [Plan/Plan26.md](Plan/Plan26.md)：Harness 产品定位、Runtime Charter 与 PROD 路线；
- [OPTIMIZATION_BACKLOG.md](OPTIMIZATION_BACKLOG.md)：生产演进 Backlog；
- [LEARNING_PATH.md](LEARNING_PATH.md)：开发与事故驱动学习路径；
- [Plan/闭环覆盖范围.md](Plan/闭环覆盖范围.md)：事故学习闭环的覆盖边界与限制；
- [VerificationReports/PROD-01B.md](VerificationReports/PROD-01B.md)：当前生产批次的权威开发验证与真实缺陷证据。
