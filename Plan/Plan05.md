# Harness 确定性状态机与任务生命周期控制方案（Plan05）

## 日期

2026-08-10

## 讨论主题

优先解决 Harness 生命线问题，为 Coding Multi-Agent 任务建立确定性的状态机、任务投递与追踪、暂停恢复、取消和优雅退出能力。

## 目标与背景

原有 TaskContext 允许任意状态跳转，CancellationToken 只在迭代开始处检查，而且取消会被记录为 FAILED。任务主要通过同步 `CodingHarness.run()` 执行，没有统一投递句柄、独立运行控制状态、暂停恢复、生命周期历史或优雅关闭接口。目标是在保持现有 Coding Workflow、CLI 和 Web 兼容的前提下，先建立单机生命周期底座。

## 候选方案对比

| 方案 | 核心思路 | 优点 | 缺点与成本 | 风险 | 适用条件 |
|---|---|---|---|---|---|
| 继续只使用 TaskState | 将暂停、取消、排队等状态全部加入业务状态机 | 状态数量集中、实现表面简单 | 暂停会覆盖 implementing/verifying 等业务阶段，恢复语义复杂 | 控制状态与工作流状态互相污染 | 很短的线性任务 |
| 双层状态机 | TaskState 表示业务阶段，LifecycleState 表示 queued/running/paused 等运行控制 | 边界清晰，暂停不破坏业务阶段 | 需要维护两套状态及终态一致性 | 异常路径可能导致两套状态不同步 | 当前 Harness，已选择 |
| 直接引入外部工作流平台 | 使用 Temporal、Celery 等管理投递、暂停和恢复 | 生产能力较完整 | 引入依赖、部署和学习成本，现有模型不一定支持真正暂停 | 当前单机规模下过度设计 | 分布式、多进程生产任务 |

任务执行接口也比较了同步 `run()`、直接 Thread 和 TaskDispatcher。最终选择保留同步兼容入口，同时新增 TaskDispatcher 与 TaskHandle；这样 CLI 不必立刻重写，Web 和未来服务可以获得异步控制能力。

取消方案比较了强制杀死线程和协作式 checkpoint。Python 线程无法安全强杀，直接终止可能留下部分文件写入，因此当前选择协作式取消，并明确正在执行的模型请求和子进程尚不能立即中断。

## 最终选择

采用双层确定性状态机：TaskState 管理 received、planning、implementing、verifying、rework 与业务终态；LifecycleState 管理 created、queued、running、paused、cancelling 与运行终态。新增线程安全 LifecycleController、生命周期迁移历史、TaskDispatcher 和 TaskHandle。暂停、恢复与取消通过显式 checkpoint 生效，Web 提供控制接口和按钮，旧 CancellationToken 作为兼容层保留。

## 选择理由

- 暂停是运行控制，不应丢失当前业务阶段。
- 固定迁移白名单能在错误发生处拒绝非法跳转，而不是依赖调用约定。
- TaskHandle 让调用方可以投递后立即获得状态、暂停、恢复、取消和等待能力。
- 协作式 checkpoint 不会在文件写入或结果合并中间强行终止线程。
- 保留同步入口和 CancellationToken 可以减少对现有 CLI、Demo 和测试的破坏。
- 当前项目仍是单机运行，内置线程池足够验证生命周期契约，无需立即引入分布式平台。

放弃单状态机是因为它会混淆业务和控制语义；暂缓外部工作流平台是因为成本超过当前需求；不采用线程强杀是因为无法保证资源清理和 Workspace 一致性。

## 架构或流程

```text
TaskDispatcher.submit(TaskContext)
        ↓ 返回 TaskHandle
Lifecycle: created → queued → running
                              ├→ paused → running
                              ├→ cancelling → cancelled
                              ├→ completed
                              └→ failed
        ↓
CodingHarness checkpoints
        ↓
TaskState: received → planning → implementing → verifying
                                  ↑                ↓
                                  └──── rework ────┘
                                           ↓
                            completed / failed / cancelled
```

## 执行步骤

1. 为 TaskState 定义完整合法迁移表并拒绝非法跳转。
2. 新增独立 LifecycleState、LifecycleController、Snapshot 和 Event 历史。
3. 保留 CancellationToken 兼容层并将 CodingHarness 接入统一 Controller。
4. 在 Worker 执行前后和质量阶段前后增加安全 checkpoint。
5. 新增 TaskDispatcher、TaskHandle、状态查询、等待和优雅 shutdown。
6. 让通用执行服务接受外部 LifecycleController。
7. 为 Web 增加 pause、resume、cancel API 和控制按钮。
8. 增加非法迁移、暂停阻塞、异步投递、运行取消和优雅关闭测试。

## 约束与风险

- 当前暂停和取消只在 checkpoint 生效，不能立即打断正在进行的 HTTP 请求或子进程。
- TaskHandle 查询 TaskContext 时尚未建立统一不可变快照，后续并发增强需要收紧读取一致性。
- 生命周期历史当前保存在进程内；RunRecorder 仍主要记录业务事件，重启恢复尚未实现。
- `shutdown(cancel_running=True)` 是协作式取消，不保证第三方调用立即响应。
- Web 控制接口当前没有多用户认证，只适用于监听 127.0.0.1 的本地模式。

## 待验证事项

- 模型请求进行中收到取消后，如何安全中止 HTTP 连接并丢弃迟到响应。
- CommandExecutor 如何将取消传递给子进程组并确保子进程全部清理。
- 两层状态在异常、进程退出和恢复场景下如何原子持久化。
- 暂停期间是否需要释放模型、文件锁或 Worker 配额。
- Web 多次重复 pause/resume/cancel 请求的幂等行为是否需要专门协议。

## 待办事项

- [ ] 将 timeout/cancel 信号传递到 ModelClient。
- [ ] 抽离 CommandExecutor，并支持终止子进程组。
- [ ] 将生命周期事件写入统一 EventStore。
- [ ] 增加 Checkpoint 持久化和进程重启恢复。
- [ ] 为 TaskHandle 提供一致的不可变任务快照。
- [ ] 定义重复控制请求的幂等结果和审计事件。
