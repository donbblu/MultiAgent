# Plan18：三方案消融协议与脚本化 dry-run

## 目标

批次 11C 只验证“如何公平地跑三种方案”，不验证“哪种方案更好”。三种方案使用相同固定任务、Validator Profile、Workspace 边界和全局预算：

1. `single_agent`：Implementer 直接提交一次 Patch，看不到验证反馈。
2. `planner_developer`：Planner 生成 Plan，Developer 提交一次 Patch，看不到验证反馈。
3. `planner_developer_tester_fixer`：首次 Patch 后由 Runtime 验证；失败时 Tester 只读取结构化反馈，Fixer 最多修复一轮。

## 复用的 Core 边界

- Worker 仍由 `WorkerRegistry` 按 Role、能力、输入/输出协议、策略和 principal 隔离选择。
- 所有阶段只通过 Artifact 通信；Worker 请求不会包含隐藏测试或参考答案。
- Worker 只能返回 `ArtifactDraft`；`core:patch` 必须包含 `ImplementationPlan`。
- Patch 仍由 `PatchIntegrator` 在任务允许路径内应用，修改公开测试或越权路径会失败并计数。
- build/test/CLI 仍由同一个冻结 Validator Profile 执行，Tester 只分析 Runtime 反馈，不能自行宣布通过。

## Artifact 可见性

| 阶段 | 可见 Artifact |
|---|---|
| 单 Agent Implementer | Coding Requirement、当前 Source Snapshot |
| Planner | Coding Requirement、当前 Source Snapshot |
| Developer | Coding Requirement、Source Snapshot、Plan |
| Tester | Coding Requirement、当前 Source Snapshot、Plan、Validator Feedback |
| Fixer | Coding Requirement、当前 Source Snapshot、Plan、Validator Feedback、Test Diagnosis |

Validator Feedback 只包含三态结果和通用失败摘要，不包含隐藏测试源码、具体隐藏输入或 Runtime 私有目录。Tester principal 必须与 Implementer principal 不同。

## 预算与指标

三种 Profile 绑定同一个 `AblationBudget` 摘要。Runtime 在调用前检查剩余 Worker 调用和最大 Token 预留，在返回后核对实际登记用量；耗尽后保持 unknown，不额外调用。

每个 trial 记录最终/首次结果、首次通过、是否修复、修复成功、轮数、Worker 调用、accounted Token、脚本/模型用量、耗时、越权、人工介入、Validator 结果和阶段可见性审计。报告保存 suite、budget 和 profile 指纹。

## dry-run 的含义

脚本 Worker 为了覆盖分支而故意产生以下结果：单 Agent 0/3，Planner + Developer 3/3 首次通过，完整方案 3/3 经一次修复通过。这些数字由脚本行为决定，只证明：

- 三种方案走了不同但冻结的 Artifact 路径；
- 首次通过和修复指标计算正确；
- Fixer 修复后会重新运行完整 Profile；
- 越权、预算耗尽和真实模型误接入会被拒绝。

因此不得把 dry-run 数字写成多 Agent 优于单 Agent 的产品结论。

## 后续进展

批次 11D 已完成供应商无关 ModelClient Worker、结构化 Patch/Plan/Diagnosis 协议、Prompt 版本和调用前源码披露审计，见 `Plan/Plan19.md`。自动验证只使用 Fake Model；真实调用仍需在下一批单独预检和授权。
