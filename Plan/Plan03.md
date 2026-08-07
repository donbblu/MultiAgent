# Coding Agent Harness 定位、架构与可视化方案（Plan03）

## 日期

2026-08-07

## 讨论主题

将现有 Multi-Agent Coding Demo 明确定位并演进为 Coding Agent Harness，同时建立可解释的学习边界、成熟度标准和第一阶段工作过程可视化。

## 目标与背景

项目已经具备角色、模型适配、Memory、并行质量检查和安全工作区，但流程主要硬编码在 Coordinator 中，容易把“增加 Agent”误认为架构进展。讨论目标是明确 Harness 作为确定性控制面的职责，判断其与 Agent Infra、AI Infra 的关系，并在不推倒现有实现的前提下调整代码结构和展示方式。

## 候选方案对比

| 方案 | 核心思路 | 优点 | 缺点与成本 | 风险 | 适用条件 |
|---|---|---|---|---|---|
| 继续固定 Agent 工作流 | 保持 Coordinator 直接调用固定 Agent | 简单、现有 Demo 可运行 | 流程、角色和执行实例耦合，扩展困难 | Agent 数量增加后维护性快速下降 | 一次性演示 |
| 一次性建设完整生产架构 | 同时引入服务网格、GraphRAG、MicroVM 和动态路由 | 目标能力完整 | 复杂度、学习和运维成本过高 | 当前规模下过度设计，难以验证收益 | 已有生产负载与团队 |
| 兼容式 Harness 演进 | 保留旧入口，逐步抽离 Workflow、Worker、Lifecycle、Gateway、Memory 和 Registry | 风险可控，可用测试证明每步演进 | 迁移期间仍存在兼容层和部分硬编码 | 新旧概念可能短期并存 | 当前单机学习项目，已选择 |

可视化讨论包含两种表达：展示模型隐藏思维链，或展示结构化可审计过程。前者不可验证且存在敏感信息风险；后者通过节点状态、消息、工具操作和结果证据解释 Harness 决策，因此选择后者。

事件传输候选包括轮询、SSE 和 WebSocket。当前先复用现有 HTTP 轮询，以最低成本完成 DAG、时间线和节点详情；SSE 作为下一步，WebSocket 留到需要双向审批和人工干预时评估。

## 最终选择

选择兼容式 Harness 演进。将 `CodingHarness` 作为确定性控制面，`Coordinator` 保留为兼容别名；使用 `WorkflowSpec/NodeSpec` 描述流程，使用 `WorkerRegistry` 完成 Role 到 Worker 的映射，使用 `CancellationToken` 保留中断控制权。第一阶段界面展示工作流 DAG、实时事件时间线和节点详情，不展示模型私有推理。

项目定位采用：Coding Agent Harness 是具体项目，Agent Runtime/Agent Infra 是具体技术方向，广义上属于应用侧 AI Infra，但不涉及模型训练和 GPU 基础设施。

## 选择理由

- 当前代码和 CLI/Web 已经可用，兼容迁移可以保留已有能力与测试证据。
- Harness 应拥有状态、调度、重试、中断和结果合并权，Role、Worker 与 Model 不应承担全局控制职责。
- 声明式流程和 Worker 注册表为后续动态角色、并行调度和模型路由提供边界。
- 当前项目缺少生产负载与长期知识数据，不适合优先建设 GraphRAG、MicroVM 或完整分布式 WorkerPool。
- 可审计事件比拟人化思维链更真实、安全，并能支持故障定位和项目评价。

放弃固定工作流作为长期方案，是因为它不能证明框架可扩展；暂缓完整生产架构，是因为成本与当前学习目标不匹配；暂缓 WebSocket，是因为当前过程展示主要是服务端单向推送。

## 架构或流程

```text
CLI / Web / API
       ↓
CodingHarness（唯一控制面）
       ├── WorkflowSpec / Task lifecycle
       ├── WorkerRegistry（Role → Worker）
       ├── MemoryManager（RoleMemoryView）
       └── Result merge / Recorder
                 ↓
        Implementer / Fixer
                 ↓
          Tester || Reviewer
                 ↓
       结构化事件 → DAG / 时间线 / 节点详情
```

## 执行步骤

1. 新增不可变 WorkflowSpec 和 NodeSpec，并校验重复、缺失依赖与环。
2. 新增 WorkerRegistry，解除 Role 与固定 Agent 实例绑定。
3. 新增 CancellationToken，在节点边界支持协作式取消。
4. 将 Coordinator 转为 CodingHarness 兼容入口并保持 CLI/Web 可运行。
5. 在 Web API 中维护安全节点快照，将 AgentMessage 关联到具体节点。
6. 在界面增加 DAG、事件时间线和可点击节点详情。
7. 为未使用的 Fixer 标记“未触发”及原因，避免把条件节点误判为失效。
8. 使用单元测试、JavaScript 语法检查和本地浏览器检查验证实现。

## 约束与风险

- 模型不得直接修改任务状态或绕过 Runtime 执行工具。
- 界面不得展示模型隐藏推理、密钥、完整敏感上下文或未经脱敏的环境变量。
- 当前 WorkflowSpec 主要完成描述和校验，`CodingHarness.run()` 尚未成为通用 DAG Executor。
- CancellationToken 当前只能在节点边界检查，无法保证立即中断正在进行的模型请求或子进程。
- 当前界面每 700ms 轮询一次，并非真正的服务端事件流。
- Fixer 仅在前序失败且仍有尝试预算时运行；最大尝试为 1 时不会进入返工。

## 待验证事项

- WorkflowSpec 驱动执行后，修改配置能否在不修改 Harness 核心的情况下改变顺序。
- 节点级取消、超时和恢复能否避免部分写入及外部副作用。
- SSE 是否能在保持本地安全边界的同时减少轮询开销和事件延迟。
- 节点详情中的权限、上下文摘要和结果证据是否足以解释任务过程。
- Token、耗时、首次通过率和并行加速比能否形成稳定的成熟度评价指标。

## 待办事项

- [ ] 实现通用 DAG Executor 和合法状态迁移表。
- [ ] 建立统一 Execution Gateway，集中执行 Capability、命令、文件和预算策略。
- [ ] 增加节点超时、执行中取消、Checkpoint 与恢复。
- [ ] 将前端轮询升级为 SSE。
- [ ] 增加模型调用、Token、关键路径和并行利用率指标。
- [ ] 形成对应代码、测试和设计记录的一周学习闭环。
