# Multimodal Coding Multi-Agent Harness

> Scope：本目录是根项目多模态 Multi-Agent Harness 的当前 Coding/VisionForge 纵向切片与兼容入口，目标迁移为专业 Plugin；它不是整个产品，也不代表 `CodingPlugin` 已经实现。根项目以通用 Multi-Agent Runtime 为执行内核，权威定位见仓库根 `README.md`、`HANDOFF.md` 与 `Plan/Plan26.md`。

这是一个供应商无关、可审计的 Coding Multi-Agent Harness。系统接收文本及多模态
需求证据，由 Runtime 负责任务拆分、Worker 调度、文件变更、真实验证、局部修复和
最终收敛。模型只能提出结构化计划或 Patch，不能自行修改任务状态、扩大权限或宣告
任务完成。

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

要求 Python 3.10+。在本目录执行：

```bash
python3 -m unittest discover -s tests -q
```

真实浏览器测试默认跳过。安装 Chromium 或设置
`VISIONFORGE_BROWSER_EXECUTABLE` 后，可通过 `VISIONFORGE_E2E=1` 显式运行。

## 通用 CLI

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

## Web 界面

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
