# 批次 10D：Role 优先的多 Worker 路由

## 目标

在不把 Role 绑定到模型或供应商的前提下，让同一 Role 拥有多个 Worker 实现，并由 Runtime 根据任务声明做可复现、可审计的选择。任何硬条件不满足都必须阻塞，不能跨 Role 或偷偷降低要求。

## 已实现协议

- `WorkerDescriptor` 只保存 Worker ID、Role、能力、输入/输出协议、策略标签、principal、优先级和静态启用状态，不持有 Worker 实例。
- `WorkerRegistry.register_worker(descriptor, worker)` 保存描述与实例的组合；`register(role, worker)` 仍生成兼容描述。
- `TaskSpec` 新增 `required_capabilities`、`input_protocols`、`output_protocols`、`required_policy_tags` 和 `independent_from_tasks`，并随 SQLite 快照恢复。
- `WorkerSelectionDecision` 保存最终结果、选择 principal、所有候选及其首个淘汰阶段；Executor 的 Checkpoint 保存每个 Task 的决定。

## 确定性选择顺序

```text
Role
  → required capabilities
  → input protocols
  → output protocols
  → required policy tags
  → excluded principals
  → enabled + availability probe
  → priority 降序、worker_id 字典序
```

前七项均为硬条件。只有完整通过的候选才进入最后的稳定决胜，不使用模型评分，也不允许从其他 Role 借用 Worker。

## 缺失能力与职责隔离

- 无合格候选时抛出带 `WorkerSelectionCode` 和候选原因的 `WorkerSelectionError`；Executor 将节点设为 `blocked`，不调用 Worker。
- Executor 接纳输出时写入 `runtime_provenance`，记录 worker、principal、Role 和 Task；Worker 草稿不能伪造该字段。
- Reviewer 与 Tester 会从输入 Artifact 的 Runtime provenance 自动排除生产 principal；`independent_from_tasks` 还可声明通用的上游职责隔离。
- `ValidatorRegistry` 支持受信 Composition Root 注入 principal；Validator 与 subject producer principal 相同则结果强制为 `unknown`。

## 兼容与边界

- 旧场景继续使用 `register(role, worker)`，行为保持一 Role 一个默认实现。
- Role 仍负责职责、权限和记忆视图；WorkerDescriptor 只说明某个实现是否满足该 Role 下当前任务的技术条件。
- 本批没有迁移 VisionForge、改变模型客户端、调用真实模型/浏览器或注册具体业务 Validator。

## 运行证据

- 新增 `tests/test_worker_routing.py` 7 项测试。
- 默认回归：149 项通过，4 项真实浏览器类跳过。
- Python compileall 与 `git diff --check` 通过。
