# MultiAgent 项目交接

## 使用方式

新任务先阅读本文，再检查 `git status`、最近提交和本文件列出的关键代码。代码、测试和 Git 是事实来源；本文用于恢复当前方向，不替代代码检查。

推荐的新任务开场指令：

```text
请读取 /Users/donbblu/codex/multiAgent/HANDOFF.md，
再检查 git status、最近一次提交和其中列出的关键文件。
以代码和测试为事实来源，不要重新读取旧聊天。
从 HANDOFF.md 的“下一步”继续推进。
```

## 项目目标

构建一个供应商无关的单机 Coding Multi-Agent Harness。Harness 负责需求拆分、任务调度、生命周期、权限、Artifact、记忆、验证和收敛；Role、Agent 与模型供应商保持解耦。

## 当前默认 Workflow

```text
用户需求
  → TaskContext 标准化目标、验收条件和权限
  → StructuredTaskPlanner 生成 TaskSpec
  → TaskGraph 校验依赖、Artifact 和资源冲突
  → TaskGraphExecutor 从 ready queue 并发调度 Worker
  → Worker 生成 ImplementationPlan Artifact
  → PatchIntegrator 检查路径、权限和跨 Artifact 冲突
  → ProjectWorkspace 原子合并
  → Tester 运行白名单验证命令
  → 通过：整合已验证长期记忆并 completed
  → 失败：记录反馈和 Checkpoint 并 failed
```

CLI 和 Web 默认使用 `dag` 引擎；`--engine legacy` 可以回退到旧 Coordinator 流程。

## 已完成

- 建立确定性的 TaskState 与 LifecycleState 双层状态机。
- 建立 `TaskSpec`、`TaskGraph`、循环依赖和 Artifact 关系校验。
- 根据依赖、输入 Artifact 和资源冲突选择可并发子任务。
- `TaskGraphExecutor` 通过 `WorkerRegistry` 并发执行任务。
- 子任务失败只重试自身，依赖失败任务的节点进入 blocked。
- Worker 不直接写共享项目，只提交 Artifact。
- `PatchIntegrator` 是共享项目的唯一写入入口，支持权限、路径和文件冲突检查。
- Workspace 使用临时文件和原子替换应用整批文件变更。
- 建立感知、Working、长期和实体四类统一 MemoryRecord。
- 支持 Harness 主动触发和 Agent 被动检索，并按 Task、Role 和上下文预算过滤。
- 使用 SQLite 保存记忆与 TaskWorkingMemory Checkpoint。
- 只有真实测试通过且拥有 Artifact 证据的节点结果才会晋升长期记忆。
- CLI/Web 已接入真实 DAG 执行路径，并保留 legacy 回退。
- Web 展示任务图、状态、Artifact 和验证事件，不展示模型原始推理。
- 当前共有 58 个单元测试通过。

## 关键设计决策

- Workflow/Task DAG 决定何时执行，Role 决定执行能力，Agent 和模型是可替换 Worker。
- Harness 独占任务状态、权限、安全策略、Artifact 接纳和最终收敛判断。
- Agent 只能读取裁剪后的 `RoleMemoryView`，不能访问密钥或扩大权限。
- Agent 不能直接修改共享目录或直接改变 TaskGraphRuntime 状态。
- 节点之间通过 Artifact 引用交接，不通过共享可变对象隐式通信。
- 未经验证的推测不能晋升长期记忆。
- 当前阶段使用线程池和 SQLite，暂不引入外部工作流平台、向量数据库或图数据库。

## 当前限制

- 合并后测试失败会直接结束任务，还不会动态生成局部 FixTask。
- `timeout_seconds` 主要是策略元数据，不能强制终止运行中的模型线程。
- 暂停和取消只在 Worker 边界生效，不能立即中断 HTTP 请求或验证子进程。
- 资源冲突目前主要依赖精确 scope 字符串，尚无可靠的 glob 交集和符号级分析。
- SQLite 已恢复 Working Memory，但尚未持久化和恢复完整 TaskGraphRuntime、尝试次数与生命周期。
- Reviewer 和 Safety 尚未成为 DAG 最终收敛门禁。
- 实体记忆已有类型边界，但尚未建立文件、符号、测试和 Artifact 的实体索引。
- 长期记忆尚缺少完整的去重、失效和 `supersedes` 管理。

## 下一步

最高优先级是闭合动态返工环路：

```text
测试失败
  → 分析失败报告和受影响 Artifact
  → 动态创建局部 FixTask
  → Fixer 只读取失败证据和相关文件
  → 生成修复 Artifact
  → 安全合并
  → 运行受影响测试
  → 最终完整质量门禁
```

之后依次推进：

1. 将 timeout/cancel 传递到 ModelClient 和验证子进程组。
2. 持久化完整 TaskGraphRuntime、尝试次数和生命周期快照。
3. 增加 glob 或符号级读写冲突分析。
4. 将 Reviewer 与 Safety 接入 DAG 收敛门禁。
5. 建立实体索引以及记忆去重、失效和版本替代。
6. 建立任务拆分质量、并发收益、返工率和记忆命中率评测。

## 关键文件

- `demo/coding_workflow/harness/task_graph.py`：TaskSpec、DAG 校验和 ready 任务选择。
- `demo/coding_workflow/harness/scheduler.py`：任务图运行状态和 Artifact 就绪管理。
- `demo/coding_workflow/harness/executor.py`：并发调度、局部重试和节点结果接纳。
- `demo/coding_workflow/planning.py`：结构化 Planner 和非法图修复。
- `demo/coding_workflow/dag_runner.py`：真实 DAG 端到端执行入口。
- `demo/coding_workflow/graph_workers.py`：DAG Worker 契约实现。
- `demo/coding_workflow/artifacts.py`：Artifact 与 ArtifactStore。
- `demo/coding_workflow/integration.py`：Patch 安全检查和集中合并。
- `demo/coding_workflow/memory.py`：分层记忆、Working Memory 和 RoleMemoryView。
- `demo/coding_workflow/memory_sqlite.py`：记忆和 Checkpoint 持久化。
- `demo/coding_agent_cli.py`：CLI 的 dag/legacy 入口。
- `demo/web_server.py`：Web 执行入口、状态控制和安全事件展示。
- `demo/tests/test_workflow.py`：Harness、DAG、记忆和端到端测试。
- `demo/docs/task-graph-and-memory.md`：设计边界说明。
- `Plan/Plan06.md`：任务拆分和记忆机制的策略归档。

## 验证命令

在 `/Users/donbblu/codex/multiAgent/demo` 执行：

```bash
python3 -m unittest discover -s tests -q
```

在仓库根目录执行：

```bash
git diff --check
git status --short
```

## Git 基线

- 仓库：`/Users/donbblu/codex/multiAgent`
- 分支：`main`
- 远端：`git@github.com:donbblu/MultiAgent.git`
- 本文创建时的最近提交：`1c70347 chore: archive daily progress 2026-08-13`
- `.env`、`.runtime/`、`.runs/`、运行输出和 `.DS_Store` 不得提交。

## 安全提醒

- 不读取、打印或提交 `.env` 和 API Key。
- 不让模型生成的路径绕过 ProjectWorkspace 与 PatchIntegrator。
- 不用模型记忆覆盖权限、安全策略、验收条件或状态机。
- 不展示或持久化 Agent 的原始思维过程，只记录摘要、事件、结果和证据。
