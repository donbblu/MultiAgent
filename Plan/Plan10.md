# Scenario DAG Runtime 统一执行方案（Plan10）

## 日期

2026-08-19

## 讨论主题

将通用 Coding Harness 与 VisionForge 的多轮执行、Artifact 接纳、收敛和恢复统一到 ScenarioRuntime，消除固定 Coordinator、旧 WorkflowSpec 和场景专用 Runner 之间的重复控制路径。

## 目标与背景

项目已经具备 TaskGraphExecutor、生命周期、ArtifactStore、SQLite Runtime 和 VisionForge 自动修复闭环，但通用 Coding、旧 Coordinator 与 VisionForge Runner 分别维护工作流、返工和恢复语义。多套入口可能对同一状态给出不同结论，也让 Worker、场景和 Runtime 的权限边界不一致。目标是建立唯一的场景级控制面，由 ScenarioProfile 声明每一轮 DAG，由 ScenarioRuntime 调度、接纳 Artifact、执行收敛判断并保存恢复状态。

## 候选方案对比

| 方案 | 核心思路 | 优点 | 缺点与成本 | 风险 | 适用条件 |
|---|---|---|---|---|---|
| 保留多套 Runner | 通用 DAG、旧 Coordinator 和 VisionForge 各自维护执行循环 | 单个场景改动局部 | 状态、恢复、收敛和 Artifact 语义重复 | 场景行为逐渐分叉 | 独立短期原型 |
| 在旧 Coordinator 上继续加分支 | 将场景差异作为固定流程条件 | 兼容旧接口 | 控制流再次硬编码，难以扩展 Validator Profile | Coordinator 重新成为上帝对象 | 固定单一工作流 |
| 统一 ScenarioRuntime | 场景提供轮次 DAG 和收敛决策，Runtime 统一执行、持久化和终态 | 单一控制面、可恢复、场景可组合 | 需要迁移入口并删除重复代码 | 兼容行为遗漏 | 当前 Harness，已选择 |
| 引入外部工作流平台 | 用 Temporal 等实现多轮工作流 | 分布式能力成熟 | 部署、依赖和迁移成本高 | 当前单机 MVP 过度设计 | 多机生产环境 |

## 最终选择

采用 `ScenarioRuntime + ScenarioProfile + ConvergenceDecision`。ScenarioProfile 负责构造每一轮 TaskGraph、准备外部 Artifact 和根据证据决定 completed、failed 或下一轮；TaskGraphExecutor 负责节点并发；ScenarioRuntime 独占场景状态、Artifact 接纳和终态；SQLiteScenarioRunStore 保存场景清单、轮次和关联 RuntimeSnapshot。VisionForge 映射为 WebVisualScenario，Web 与评测入口改用统一 Runner；旧实现只保留必要兼容测试，重复的 Coordinator 路径和相关模块删除。

## 选择理由

- TaskGraphExecutor 适合执行单轮 DAG，ScenarioRuntime 负责跨轮修复和最终收敛，两层职责清晰。
- 不同业务场景只需要替换 ScenarioProfile 和 Validator，不必复制调度、恢复和生命周期代码。
- Runtime 统一接纳 ArtifactDraft，Worker 无法自行把结果写入共享 ArtifactStore。
- 场景级 SQLite 清单复用节点级 RuntimeSnapshot，可在 Gate、Fix 轮次和 completed 后幂等恢复。
- 删除旧 Coordinator 和重复通信、记录、审查代码，减少多个事实源。

放弃保留多套 Runner和继续扩展 Coordinator，是因为会延续状态分叉和硬编码；暂不引入外部平台，是因为当前仍是单机可验证 MVP。

## 架构或流程

```text
ScenarioProfile.prepare
  → 外部 Artifact + 第 1 轮 TaskGraph
  → ScenarioRuntime
      → TaskGraphExecutor 并发执行节点
      → Runtime 接纳 ArtifactDraft
      → ScenarioProfile.converge(evidence)
          ├─ completed → 终态与 Artifact 固化
          ├─ failed → 记录确定性原因
          └─ continue → 构造下一轮 Fix DAG
  → SQLiteScenarioRunStore
      → 场景状态、轮次、活跃 Artifact、RuntimeSnapshot、Workspace 哈希
```

## 执行步骤

1. 定义 ScenarioProfile、ScenarioRuntime、ConvergenceDecision 和场景结果协议。
2. 增加 SQLiteScenarioRunStore，保存场景清单和各轮 Runtime Snapshot。
3. 将 ArtifactDraft 作为 Worker 输出，收回共享 Artifact 接纳权。
4. 让 TaskGraphRuntime 和 Executor 支持场景恢复与外部 Artifact。
5. 将 VisionForge Analyst、Developer、Browser、Reviewer、Gate 和 Fix DAG 映射到 WebVisualScenario。
6. 将 Web Runtime 与三方案评测入口切换到统一场景 Runner。
7. 保留必要的旧 Runner 兼容对照，删除不再使用的 Coordinator、WorkflowSpec 和重复模块。
8. 更新 HANDOFF、README、待办和测试。

## 约束与风险

- 场景 Runtime 统一后，旧 API 的隐式事件和角色历史可能不再存在，需要调用方使用新的场景快照。
- SQLite 场景清单和节点 Runtime Snapshot 必须保持版本与 Workspace 哈希一致。
- 已完成场景恢复应幂等，不能重复模型调用、Patch 应用或验证副作用。
- Workspace 漂移时必须拒绝自动恢复，不得猜测外部修改归属。
- 旧 Runner 仍存在兼容测试时，要明确其非产品入口，避免形成新的双路径。
- Validator Profile 尚未完成，当前统一的是执行控制面，不代表通用 Coding MVP 已闭环。

## 待验证事项

- ScenarioRuntime 能否覆盖非视觉的语言、API、CLI 和图片证据 Coding 场景。
- 多轮恢复在模型返回后、Patch 应用后、Gate 后和 completed 后是否都保持幂等。
- ArtifactDraft 接纳失败时，节点、场景和生命周期状态是否一致。
- 移除旧 Coordinator 后 CLI/Web 是否仍提供足够的可观察事件。
- 通用 Validator Profile 如何与 ScenarioProfile 分工，避免场景降低最终门禁。

## 待办事项

- [ ] 建立 text/image/audio/video 的 RequirementEvidence 协议。
- [ ] 建立与 UI 无关的 CodingRequirement。
- [ ] 实现 Runtime 拥有且模型不可降低的 Validator Profile。
- [ ] 建立固定本地 Coding 任务和隐藏测试。
- [ ] 比较单 Agent、Planner+Developer 和完整测试修复闭环。
- [ ] 在新场景稳定后移除剩余 Legacy Runner 兼容代码。
