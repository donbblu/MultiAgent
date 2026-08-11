# Harness 架构边界问题清单

## 目的

本文记录当前 Coding Agent Harness 已确认的架构边界问题，作为后续学习、设计、实现和验收的任务清单。

处理原则：

- 每次只解决一个边界问题。
- 先描述失败场景，再编写测试，最后实现最小修改。
- 保持 CLI、Web 和现有测试兼容。
- 测试通过不代表问题全部解决，必须同时记录当前限制。

## 优先级说明

- **P0**：影响Harness核心正确性，应优先解决。
- **P1**：影响安全性、并发可靠性或扩展性。
- **P2**：当前可以运行，但规模扩大后会造成维护问题。

## 问题总览

| 编号 | 优先级 | 问题 | 状态 |
|---|---|---|---|
| H-01 | P0 | WorkflowSpec没有真正驱动执行 | 待解决 |
| H-02 | P0 | Planner不是实际执行节点 | 待决策 |
| H-03 | P0 | Worker接收完整且可变的TaskContext | 待解决 |
| H-04 | P0 | 并行Worker共享可变任务对象 | 待解决 |
| H-05 | P1 | active_role形成隐藏依赖 | 待解决 |
| H-06 | P1 | 权限检查分散 | 待解决 |
| H-07 | P1 | MemoryPolicy没有被完整执行 | 待解决 |
| H-08 | P1 | WorkerRegistry缺少Worker接口约束 | 待解决 |
| H-09 | P1 | 取消不能终止运行中的外部操作 | 部分解决 |
| H-10 | P2 | ProjectWorkspace职责过多 | 待评估 |
| H-11 | P2 | RunRecorder混合持久化与事件通知 | 待评估 |

## P0：核心正确性

### H-01：WorkflowSpec没有真正驱动执行

**现状**

`WorkflowSpec`描述了节点和依赖，但真实执行顺序、返工和并行逻辑仍硬编码在`CodingHarness.run()`中。

**风险**

工作流配置和真实执行可能不一致，修改流程时需要同时修改多处代码。

**目标**

由通用Executor读取`WorkflowSpec`并决定节点就绪、执行顺序和并行关系，使Workflow成为唯一事实来源。

**完成标准**

- [ ] 修改Workflow配置可以改变执行顺序，无需修改Harness核心。
- [ ] 环和缺失依赖在执行前被拒绝。
- [ ] 节点执行顺序有自动化测试证明。
- [ ] 返工路径不再由固定Role名称硬编码。

### H-02：Planner不是实际执行节点

**现状**

Harness会分配Planner Role，但没有调用Planner Worker，也没有生成结构化规划结果。

**待决策**

- 方案A：Planner成为真实节点，输出结构化PlanningResult。
- 方案B：Planner不是独立节点，只保留为Harness内部准备阶段。

**完成标准**

- [ ] 明确选择方案并记录理由。
- [ ] Workflow定义与Runtime行为保持一致。
- [ ] 如果保留Planner节点，必须有输入、输出和失败测试。

### H-03：Worker接收完整且可变的TaskContext

**现状**

Worker通过`run(task: TaskContext)`获得完整任务对象，能够看到并修改不属于当前节点的状态。

**风险**

Worker可能越过上下文边界，意外修改任务状态、反馈或版本。

**目标**

Harness保留权威`TaskContext`，Worker只接收不可变、最小化的`NodeInput`，并只返回`NodeResult`。

**完成标准**

- [ ] 定义供应商无关的`NodeInput`和`NodeResult`。
- [ ] Worker接口不再接收完整TaskContext。
- [ ] Worker无法直接修改TaskState和版本。
- [ ] 不同Role只能获得各自需要的字段。

### H-04：并行Worker共享可变任务对象

**现状**

Tester和Reviewer在线程池中共享同一个`TaskContext`实例。

**风险**

未来任一Worker修改TaskContext时可能出现Race Condition和不可复现结果。

**目标**

为每个并行节点创建独立的不可变输入快照，通过Reducer合并结构化结果。

**完成标准**

- [ ] 并行Worker不共享可变输入。
- [ ] 过期结果不能覆盖新版本状态。
- [ ] 合并顺序不影响最终结果。
- [ ] 有并发和迟到结果测试。

## P1：安全与扩展性

### H-05：active_role形成隐藏依赖

**现状**

Worker通过`task.active_role`确定当前Role，并行阶段还需要手动清空该字段。

**风险**

Worker行为依赖Harness先前的隐式修改，容易使用错误Role或权限。

**目标**

Role作为`NodeInput`的显式字段传入，不再通过共享TaskContext传递。

**完成标准**

- [ ] Worker调用显式携带Role。
- [ ] 删除Worker对`task.active_role`的读取。
- [ ] 并行执行不需要手动清空active_role。

### H-06：权限检查分散

**现状**

Capability、命令、路径、输出和资源检查分布在Role、Worker、Policy、Validator和Workspace中。

**风险**

新增Worker或Tool时可能遗漏某项检查，导致越权或安全策略不一致。

**目标**

建立统一Execution Gateway，集中执行Role Capability、任务授权、工具策略和资源预算检查。

**完成标准**

- [ ] 所有文件和命令操作经过统一Gateway。
- [ ] 未授权操作默认拒绝。
- [ ] Worker不能绕过Gateway直接执行副作用。
- [ ] 权限拒绝有结构化结果和审计事件。

### H-07：MemoryPolicy没有被完整执行

**现状**

文件、反馈和字符预算会被执行，但`readable_scopes`和`writable_scopes`主要停留在描述层。

**风险**

配置看起来存在安全限制，Runtime实际上没有完整执行，容易产生错误安全感。

**目标**

实现Scope检查，或删除暂未生效的字段并明确当前能力。

**完成标准**

- [ ] 每个Policy字段都有对应Runtime行为和测试。
- [ ] 未实现字段不再被描述为已生效能力。
- [ ] 越权Scope访问被明确拒绝。

### H-08：WorkerRegistry缺少Worker接口约束

**现状**

Registry使用`dict[str, object]`，可以注册没有`run()`契约的任意对象。

**风险**

配置错误只能在任务运行到该节点时暴露。

**目标**

定义统一Worker Protocol，并在注册或启动阶段验证Worker契约。

**完成标准**

- [ ] WorkerRegistry使用明确的Worker类型。
- [ ] 非法Worker在任务执行前被拒绝。
- [ ] 一个Worker可以安全承担多个Role。

### H-09：取消不能终止运行中的外部操作

**现状**

已建立独立 LifecycleController、确定性生命周期迁移、TaskHandle 和 Web 控制接口；暂停、恢复与取消会在节点边界及 Worker 返回后生效。当前仍不能保证立即终止运行中的模型请求或子进程。

**风险**

用户取消后，模型请求、测试命令或文件操作仍可能继续运行。

**目标**

将取消和超时信号传递到Worker、ModelClient和CommandExecutor，并正确清理资源。

**完成标准**

- [x] 节点执行前取消不会启动Worker。
- [ ] 模型调用和子进程都有超时。
- [ ] 运行中取消能够终止或明确标记不可中断操作。
- [ ] 取消后不会继续合并迟到结果。

## P2：职责拆分

### H-10：ProjectWorkspace职责过多

**现状**

ProjectWorkspace同时负责文件解析、读写、回滚、子进程执行和命令超时。

**风险**

文件与命令安全策略逐渐混合，模块测试和权限控制会越来越复杂。

**目标**

根据实际复杂度评估是否拆分为ProjectFileSystem、CommandExecutor和Execution Gateway。

**完成标准**

- [ ] 文件操作与命令操作的策略边界清晰。
- [ ] 拆分前后安全测试保持通过。
- [ ] 如果暂不拆分，记录继续合并的理由和限制。

### H-11：RunRecorder混合持久化与事件通知

**现状**

RunRecorder既写JSONL和任务快照，又通过Listener向Web层发送事件。

**风险**

事件存储和事件分发耦合，未来增加多个订阅者或存储后端时不易扩展。

**目标**

根据需要拆分EventBus、EventStore和Subscriber。

**完成标准**

- [ ] 事件生产者不依赖具体存储和UI。
- [ ] 存储失败与订阅者失败有独立处理策略。
- [ ] 如果暂不拆分，记录当前规模下保留设计的理由。

## 推荐解决顺序

```text
H-03 NodeInput / NodeResult
        ↓
H-05 移除active_role隐藏依赖
        ↓
H-04 并行不可变输入与Reducer
        ↓
H-01 Workflow驱动Executor
        ↓
H-02 明确Planner语义
        ↓
H-08 Worker契约
        ↓
H-06 Execution Gateway
        ↓
H-07 MemoryPolicy落实
        ↓
H-09 运行中取消
        ↓
H-10 / H-11 按复杂度评估拆分
```

说明：先建立节点输入输出契约，可以减少后续Executor、并发和Gateway改造时的重复工作。

## 单项问题处理模板

```markdown
### 问题编号与名称

**失败场景**
- 当前输入：
- 当前错误行为：
- 预期行为：

**设计选择**
- 候选方案：
- 最终选择：
- 选择理由：

**测试**
- 失败测试：
- 回归测试：
- 测试未覆盖：

**实现**
- 修改模块：
- 保持兼容的接口：

**结果**
- 测试证据：
- 当前限制：
- 后续任务：
```
