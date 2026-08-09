# Coding Harness 与 Blackboard 多 Agent 协作方案对比（Plan04）

## 日期

2026-08-09

## 讨论主题

将当前 Coding Agent Harness 与一种采用 Coordinator、Understanding、Safety、Context、Response 五类 Agent，并通过任务认领、共享黑板、Artifact 和安全审查协作的 MindBridge 类方案进行比较，判断哪些能力值得吸收。

## 目标与背景

当前项目采用 Harness 主动调度、角色化 Worker、受限 Workspace、真实测试和独立审查，强调确定性执行边界。对比方案强调动态任务认领、共享黑板、候选 Artifact、安全覆盖、RAG、工具队列、内置评测、本地模型和容器化交付。目标不是复制对方的角色集合，而是识别两种架构在动态协作、安全、正确性、评测和工程交付方面的差异。

## 候选方案对比

| 方案 | 核心思路 | 优点 | 缺点与成本 | 风险 | 适用条件 |
|---|---|---|---|---|---|
| 保持当前 Harness 主动调度 | Workflow 指定节点，Harness 分配 Role/Worker，结构化结果由 Harness 合并 | 控制明确，真实测试和权限边界容易证明 | 动态协作不足，流程仍有硬编码 | 规模扩大后扩展受限 | 单机 Coding Agent、正确性优先 |
| 完整共享黑板与任务认领 | Agent 从共享 Blackboard 认领任务并提交 Artifact | 解耦、易增加专业 Agent、适合动态任务 | 需要租约、幂等、版本、冲突和过期清理 | 重复执行、并发覆盖、敏感上下文扩散 | 任务类型动态、Worker 数量较多 |
| 直接复制五 Agent 角色 | 引入 Understanding、Safety、Context、Response 等对话角色 | 对话语义链清晰 | 与 Coding 场景不匹配，增加调用与延迟 | 形式上多 Agent、实质收益有限 | 对话安全与回复生成产品 |
| 混合受控协作 | Workflow 决定依赖，Harness 发布就绪任务，Worker 受控认领并提交版本化 Artifact | 保留确定性安全，同时提高动态扩展能力 | 需要新增 Task Board、Artifact Schema 和 Reducer | 边界设计不清会退化为共享可变状态 | 当前项目后续演进，推荐方向 |

安全方案也有两种：由 SafetyAgent 单独决定覆盖，或由确定性 Runtime Policy 提供不可绕过的硬边界、SafetyAgent 提供语义风险信号。后者安全性更强，但需要维护规则与语义判断两套机制。

工程完善度方面，对比方案已有评测集、Ollama、OpenAI-compatible、Mock、Docker、Compose、Modelfile 和 GGUF 脚本；当前项目已有 DeepSeek/OpenAI-compatible、Mock、CLI/Web 和 36 项单元测试，但缺少固定业务评测集、明确的 Ollama 适配和容器化交付。

## 最终选择

尚未决策是否实施 Task Board 与通用 Artifact。讨论形成的推荐方向是保留当前 Coding Role、真实执行和 Runtime 强制安全边界，后续优先评估受控 Task Board、版本化 Artifact、固定评测集和容器化交付，而不是直接复制 MindBridge 的五类 Agent 或开放共享可变黑板。

## 选择理由

- Understanding、Context、Response 等角色针对对话生成，不能直接映射 Coding 工作流。
- 当前 Workspace、CommandPolicy、PlanValidator 和真实测试提供了比纯模型审查更确定的执行安全与正确性证据。
- Task Board 和 Artifact 能改善动态扩展，但必须由 Harness 控制认领、版本、权限和状态变化。
- SafetyAgent 仍是概率模型，不能替代路径、命令、密钥和资源限制等确定性策略。
- 评测集和容器化能直接提高项目的可衡量性与可复现性，成本低于立即建设完整 Blackboard。

未选择直接复制五 Agent，是因为领域与收益不匹配；未选择完全开放的 Blackboard，是因为当前尚无租约、幂等、冲突合并和敏感信息隔离机制；是否实现混合方案仍需通过实际任务规模和调度需求验证。

## 架构或流程

```text
WorkflowSpec 决定依赖
        ↓
Harness 向受控 Task Board 发布就绪任务
        ↓
Worker 按 Role 与 Capability 认领
        ↓
Worker 提交版本化 Artifact
        ↓
Runtime Policy 做确定性安全检查
        ↓
Tester / Reviewer 提供功能与语义审查
        ↓
Harness Reducer 决定采纳、返工或拒绝
```

## 执行步骤

1. 先定义统一 Artifact 的最小字段：ID、任务版本、节点、生产者、类型、状态、来源和 Payload。
2. 建立固定 Coding 评测集，记录成功率、首次通过率、返工率、安全拒绝、Token 和耗时。
3. 使用单机内存实现最小受控 Task Board，验证发布、认领、超时和重复认领行为。
4. 仅允许 Harness 修改任务状态，Worker 通过命令提交 Artifact。
5. 在需求明确后补充 Ollama 适配、Dockerfile 和 Compose。
6. 根据评测结果决定是否继续建设持久化 Blackboard、租约和 WorkerPool。

## 约束与风险

- Blackboard 不得成为任意 Agent 可修改的全局 `TaskContext`。
- Artifact 必须版本化、可校验、可审计，并过滤敏感字段。
- SafetyAgent 只能增加限制或提供风险信号，不能绕过 Runtime 硬策略。
- 引入任务认领后必须处理租约、重复消费、迟到提交和取消后的结果。
- 增加 Agent 和审查阶段会提高延迟与 Token 成本，必须用评测证明收益。
- 对比方案的 17 项测试及六组验证仅来自用户提供的描述，本次没有独立核验其仓库实现。

## 待验证事项

- 当前 Coding 任务是否足够动态，能够证明任务认领优于 Harness 直接分配。
- 通用 Artifact 是否能覆盖规划、文件变更、测试和审查结果而不弱化类型安全。
- SafetyAgent 对代码风险的召回率、误报率和抗 Prompt Injection 能力。
- 固定评测集能否稳定区分架构改动带来的真实收益。
- Ollama 与容器化是否属于近期交付目标，还是应继续保持零依赖本地运行。

## 待办事项

- [ ] 明确是否采用混合受控 Task Board。
- [ ] 设计最小 Artifact Schema 和版本校验规则。
- [ ] 建立 Coding 任务、失败注入和安全攻击评测集。
- [ ] 记录首次通过率、返工率、Token、耗时和安全拒绝率。
- [ ] 评估 Ollama Provider、Dockerfile 与 Docker Compose。
- [ ] 在有数据后决定是否引入持久化 Blackboard 和 Tool Queue。
