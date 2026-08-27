# Multimodal Coding Multi-Agent Harness

> Scope：本目录是根项目多模态 Multi-Agent Harness 的当前 Coding/VisionForge 纵向切片与兼容入口，目标迁移为专业 Plugin；它不是整个产品，也不代表 `CodingPlugin` 已经实现。根项目以通用 Multi-Agent Runtime 为执行内核，权威定位见仓库根 `README.md`、`HANDOFF.md` 与 `Plan/Plan26.md`。

这是一个供应商无关、可审计的 Coding Multi-Agent Harness。系统接收文本及多模态
需求证据；Harness 负责任务拆分、角色/模型策略、工具与局部修复编排，Runtime Kernel
负责 Worker 调度、状态/权限/预算、Artifact 接纳、真实 Validator 执行和最终门禁。
模型只能提出结构化计划或 Patch，不能自行修改任务状态、扩大权限或宣告任务完成。

## 默认作品集入口

> 当前入口运行真实的 **Agent Runtime MVP**：它会创建持久 Thread、AgentInstance 与
> AgentSession，经 SQLite Mailbox 投递结构化 Message，并在共享线程池的 Agent 泳道中
> 执行 scripted Worker。本地候选 `cbb35e3` 已通过干净检出 release check 与最终独立
> Review，初审唯一文档新鲜度发现已关闭。它仍不是生产级 Runtime。

要求 Python 3.10+。从仓库根目录运行：

```bash
python3 demo/portfolio_demo.py --trusted-local-execution
```

这是 README 唯一推荐的默认 Quickstart。它固定使用 `core-coding-eval-v1` 的三个任务和
三种 scripted 策略，不读取 `.env`、不访问网络、不启动 Web/Browser、不调用真实模型。
`--trusted-local-execution` 只批准固定 Suite 已登记的本地 Python Validator；缺少批准
时会在 Suite、Workspace、Validator 和报告副作用前退出 `2`。

成功输出包含 Runtime 汇总和完整角色时间线。以下 ID 使用占位符；真实运行每次生成
不同 ID，但冻结的计数和结果不变：

```text
mode=scripted/offline network=false real_provider=false external_model_calls=0
runtime scope=portfolio-demo threads=9 agents=21 sessions_closed=21 mailbox_sent=42 mailbox_received=42 stage_messages=21 handoffs=12 fifo=true max_parallel_agents=3
role=Planner stage=plan Artifact=core:plan ArtifactRef=artifact://<artifact-id> Validator=none result=completed thread_id=portfolio-<run-id>-<task>-<strategy> agent_id=agent-<run-id>-<trial>-planner session_id=session-<run-id>-<trial>-planner session_state=closed lifecycle=created>paused>resumed>closed message_id=message-<run-id>-<n> handoff=false
role=Developer stage=implement Artifact=core:patch ArtifactRef=artifact://<artifact-id> Validator=none result=completed thread_id=<same-thread> agent_id=agent-<run-id>-<trial>-implementer session_id=session-<run-id>-<trial>-implementer session_state=closed lifecycle=created>paused>resumed>closed message_id=message-<run-id>-<n> handoff=true
role=Validator stage=initial_validation Artifact=core:validator_feedback ArtifactRef=none Validator=runtime-owned fixed suite result=failed thread_id=<same-thread> agent_id=none session_id=none session_state=runtime-owned lifecycle=runtime-owned message_id=none handoff=false
role=Tester stage=diagnose Artifact=core:test_diagnosis ArtifactRef=artifact://<artifact-id> Validator=none result=completed thread_id=<same-thread> agent_id=agent-<run-id>-<trial>-tester session_id=session-<run-id>-<trial>-tester session_state=closed lifecycle=created>paused>resumed>closed message_id=message-<run-id>-<n> handoff=true
role=Fixer stage=fix Artifact=core:patch ArtifactRef=artifact://<artifact-id> Validator=none result=completed thread_id=<same-thread> agent_id=agent-<run-id>-<trial>-fixer session_id=session-<run-id>-<trial>-fixer session_state=closed lifecycle=created>paused>resumed>closed message_id=message-<run-id>-<n> handoff=true
role=Validator stage=final_validation Artifact=core:validator_feedback ArtifactRef=none Validator=runtime-owned fixed suite result=passed thread_id=<same-thread> agent_id=none session_id=none session_state=runtime-owned lifecycle=runtime-owned message_id=none handoff=false
status=passed tasks=3 trials=9 delivered=6 expected_failures=3 repaired=3 external_model_calls=0 report=demo/.runs/portfolio-demo/report.json
```

三个 Single-Agent 失败是预期对照；完整矩阵匹配才会退出 `0`。结构化
`portfolio-demo-report/v2` 报告写入 `demo/.runs/portfolio-demo/report.json`，包含全部
Trial、Agent Runtime、StageAudit、Validator、失败原因、期望/实际矩阵和限制声明。
报告使用临时文件加原子替换；同目录的 `runtime.sqlite3` 按唯一运行 ID 追加持久 Runtime
证据。`.runs/` 被忽略，Trial Workspace 在结束后清理。`portfolio-demo-report/v1` 只
代表历史 preview 契约。

公开闭环是：

```text
固定输入 → 持久 Thread / Agent / Session → SQLite Mailbox
        → AgentLaneRuntime（同 Agent FIFO、跨 Agent 并行）
        → WorkerRegistry/Role 路由 → Artifact → PatchIntegrator
        → Runtime-owned Validator
        → 失败时 Tester 诊断 / Fixer 修复 / 再验证
        → 结构化 Handoff + 公开时间线 + portfolio-demo-report/v2
```

该 scripted/offline Demo 证明冻结场景下的 Agent 生命周期、Mailbox、Handoff、泳道、
Harness 编排、权限、Artifact、Validator 和 Fix 控制流；不证明 LLM 效果、多 Agent
普遍优越性或生产认证。它没有 ACK/重试/崩溃重投、跨进程泳道协调、in-flight 恢复、
exactly-once 或 durable Turn Store。CLI 与 JSON 报告是可复用 MVP 产品面；本地候选已
完成 release check 和最终独立Review。下文真实模型 CLI、Web 和 VisionForge
是保留的进阶/纵向入口，不是默认路径。

## 当前执行路线

通用 CLI 和 Web 任务只使用 DAG Runtime：

```text
用户需求与验收条件
        ↓
StructuredTaskPlanner 生成 TaskSpec
        ↓
TaskGraph 校验依赖、Artifact 和资源冲突
        ↓
TaskGraphExecutor 并发调度 ready Worker
        ↓
Worker 提交 ImplementationPlan Artifact
        ↓
PatchIntegrator 检查权限、路径和文件冲突
        ↓
ProjectWorkspace 原子合并
        ↓
真实验证命令
  ├─ 通过 → completed，并晋升有证据的长期记忆
  └─ 失败 → 创建局部 FixTask → 合并修复 → 完整质量门禁
```

CLI/Web 不再提供旧式顺序执行或引擎回退选项。`web_visual` 由通用
`ScenarioRuntime` 驱动多轮 `TaskGraphExecutor`：参考图作为外部 Artifact 进入
DAG，UI Analyst、Web Developer、Patch Integrator、Browser Tester、Visual
Reviewer 和 Quality Gate 通过强类型 Artifact 交接；门禁失败时按场景策略创建
最多两轮 Fix DAG。

## 核心边界

- `TaskContext` 保存目标、验收标准、验证命令和权限边界。
- `TaskSpec / TaskGraph / TaskGraphRuntime` 决定任务依赖、ready 状态和局部重试。
- `WorkerRegistry` 按 Role 分配 Worker；Role 决定能力与上下文权限，不绑定模型供应商。
- Worker 只读取裁剪后的 `RoleMemoryView`，并通过 Artifact 交接结果。
- Worker 只返回 `ArtifactDraft`；共享 `ArtifactStore` 仅由 Executor 接纳和写入。
- `PatchIntegrator` 是共享项目的唯一 Patch 接纳入口，Workspace 负责原子写入。
- 测试、构建和行为断言由 Runtime 执行；模型不能降低或删除质量门禁。
- `SQLiteMemoryStore` 和 `SQLiteRuntimeStore` 分别保存记忆、Working Memory 与可恢复运行快照。

Task 使用两层状态：`TaskState` 表示业务阶段，`LifecycleState` 表示 queued、running、
paused、cancelling 等运行控制。暂停和取消在 Worker 边界生效，当前不能强制中断已经
发出的模型 HTTP 请求。

## Agent 分工

- Planner：把目标拆成有依赖、产物和资源范围的可验收子任务。
- Implementer：针对领取的节点生成结构化文件变更 Artifact。
- Tester：运行 Runtime 预先授权的真实验证命令并保存证据。
- Fixer：只读取相关失败证据和文件，生成局部修复 Artifact。
- Reviewer：提供只读审查能力；通用 DAG 的最终 Reviewer 门禁仍在后续接入计划中。
- Vision Reviewer：仅在 `web_visual` 场景读取参考图与实际截图，输出结构化视觉问题。

Agent 之间不依赖自由聊天来决定工作。调度器根据任务依赖、Role 能力、资源范围和
Artifact 是否就绪分配工作；所有关键交接与结果均为结构化 Artifact 和事件。

## 运行测试

要求 Python 3.10+。在本目录执行定向 Agent Runtime + Demo 回归：

```bash
python3 -m unittest \
  tests.test_agent_runtime \
  tests.test_agent_mailbox \
  tests.test_coding_ablation \
  tests.test_portfolio_agent_runtime \
  tests.test_portfolio_demo
```

运行 Runtime 测试集：

```bash
python3 -m unittest discover -s tests -p 'test_runtime*.py'
```

运行完整非预期红测集合：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest $(rg --files tests -g 'test_*.py' -g '!test_*expected_red.py' | sed 's#/#.#g; s#\.py$##')
```

`test_local_trusted_execution_expected_red.py` 与
`test_local_trusted_execution_behavior_expected_red.py` 是必须在独立新解释器中执行的历史
EXPECTED_RED 证据；不要把它们混入普通 discover 进程。

真实浏览器测试默认跳过。安装 Chromium 或设置
`VISIONFORGE_BROWSER_EXECUTABLE` 后，可通过 `VISIONFORGE_E2E=1` 显式运行。

## 进阶：真实模型通用 CLI（非默认）

以下入口会根据配置进入真实 Provider 路径，可能读取供应商配置并访问网络；它不属于
离线作品集 Quickstart，也不能用 scripted Demo 的结果替代真实模型效果评测。

```bash
python3 coding_agent_cli.py \
  "写一个冒泡排序函数，返回新列表且不修改输入" \
  --name bubble-sort
```

可选择已注册的模型供应商并补充验收标准：

```bash
python3 coding_agent_cli.py "需求" \
  --name my-task \
  --provider deepseek \
  --model deepseek-v4-pro \
  --criterion "正常输入与边界输入均有自动化测试"
```

输出固定在 `agent-output/<name>/`。已有非空目录默认拒绝覆盖，只有显式传入
`--continue-existing` 才会继续修改。默认验证命令为：

```bash
python3 -m unittest discover -s tests -v
```

## 进阶：Web 界面（非默认）

现有 Web 是 Coding Harness 兼容工作台，不是完整持久 Thread/Agent 控制面；任务索引和
运行句柄仍主要保存在进程内。它不属于作品集完成门禁，也不替代默认 CLI 报告。

```bash
python3 web_server.py
```

访问 `http://127.0.0.1:8765`。界面展示任务图、角色接手、Artifact、验证证据、
局部修复和最终状态，不展示模型原始推理。Web 同时提供受控的 VisionForge 图片上传、
任务和结果查询接口。

## 模型接入

核心流程只依赖 `ModelClient`、`ModelConfig`、`ModelClientFactory` 和结构化请求协议。
供应商差异封装在 `coding_workflow/model/` 中；角色选择模型时会先检查 text、vision、
tool calling 和 structured output 等能力声明。

## 关键目录

- `coding_workflow/dag_runner.py`：通用 DAG 端到端入口与局部 FixTask 闭环。
- `coding_workflow/agent_runtime.py`：Agent 实体、Session、Message/Handoff 与 Manager 契约。
- `coding_workflow/runtime_persistence/agent.py`：Agent、Session 和私有状态 SQLite Store。
- `coding_workflow/runtime_persistence/mailbox.py`：持久 Mailbox 与消费游标。
- `coding_workflow/portfolio_agent_runtime.py`：作品集 Demo 的 Agent 泳道、Mailbox 与 Worker 适配层。
- `portfolio_demo.py`：默认离线入口、Runtime 汇总和 `portfolio-demo-report/v2` 组装。
- `coding_workflow/harness/`：TaskGraph、调度、生命周期、注册表和执行器。
- `coding_workflow/planning.py`：结构化任务规划与非法图修复。
- `coding_workflow/graph_workers.py`：DAG Worker 契约实现。
- `coding_workflow/artifacts.py`：不可变 Artifact 与验证状态。
- `coding_workflow/integration.py`：Patch 权限、冲突检查与集中合并。
- `coding_workflow/memory.py`、`memory_sqlite.py`：分层记忆与 Working Memory。
- `coding_workflow/runtime_sqlite.py`：DAG 快照、恢复和 Workspace 漂移检查。
- `coding_workflow/visionforge/`：多模态网页生成、浏览器验证、视觉审查和修复场景。
- `coding_workflow/visionforge/dag.py`：VisionForge Worker、主 DAG 与 Fix 子图入口。
- `coding_workflow/visionforge/scenario.py`：`WebVisualScenario`、收敛策略和产品执行入口。
- `coding_workflow/harness/scenario.py`：通用多轮场景 Runtime。
- `coding_workflow/harness/scenario_sqlite.py`：场景轮次清单和恢复状态。
- `tests/`：确定性单元、集成及可选真实浏览器测试。

更详细的任务图和记忆边界见
[docs/task-graph-and-memory.md](docs/task-graph-and-memory.md)。

## 安全边界

- 所有文件路径必须位于授权工作区，拒绝绝对路径和 `..` 穿越。
- 验证命令使用参数数组与 `shell=False`，并受命令白名单约束。
- 模型不得读取密钥、访问网络、安装依赖或直接写共享目录。
- 记忆持久化前会扫描并脱敏常见密钥格式。
- 高风险生产使用仍需增加进程、网络和文件系统沙箱，以及人工审批。
