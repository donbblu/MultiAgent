# 多模态 Coding Multi-Agent 项目交接

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

项目核心是供应商无关的单机 **Coding Multi-Agent Harness**。系统接受文本、图片、音频和视频形式的需求或问题证据，由多个可组合 Agent 完成需求理解、任务拆分、代码修改、测试、审查和修复；最终结果由 Runtime 的编译、测试、权限和回归证据裁决。

VisionForge 的“参考图 → Vue 页面 → 浏览器功能验收 → VLM 视觉审查 → 自动修复”完整保留为 `web_visual` 场景，不再代表整个产品。多模态描述输入方式，不限定输出只能是网页。

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
  → 失败：记录证据并创建局部 FixTask
  → Fixer 生成修复 Artifact，安全合并并运行受影响测试
  → 最终完整质量门禁通过后整合长期记忆并 completed
```

CLI 和 Web 只使用 DAG Runtime，不再提供旧式顺序执行或引擎回退选项。

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
- CLI/Web 已接入真实 DAG 执行路径，并移除旧式顺序执行分支。
- Web 展示任务图、状态、Artifact 和验证事件，不展示模型原始推理。
- 动态创建局部 FixTask，修复 Artifact 通过安全合并、受影响验证和最终完整质量门禁。
- 持久化并恢复完整 TaskGraphRuntime、尝试次数、生命周期、Artifact 和 Workspace 哈希。
- 建立文件、符号、测试和 Artifact 实体索引，并支持中文文本与实体精确检索。
- 长期记忆支持幂等去重、失效、过期和 `supersedes` 版本替代，持久化前扫描并脱敏常见秘密。
- 建立 VisionForge 1.0 UI Spec 与 Visual Review 协议，包含受控交互、P1/P2/P3 问题和 Runtime 视觉通过判定。
- ModelClient 支持 text、vision、tool_calling、structured_output 能力声明，以及多模态结构化请求和 Token/耗时响应元数据。
- 图片按 SHA-256 内容寻址存储；Artifact 与 SQLite 只保存 PNG/JPEG 引用、尺寸、MIME 和哈希。
- 建立固定 Vue 3 + Vite 模板、保护路径、锁文件和稳定 DOM Hook，并通过真实生产构建。
- 建立受控命令运行器和 Vue 开发服务器生命周期，支持白名单、readiness、超时、取消与进程组清理。
- Playwright Browser Tester 使用固定 viewport 和受控交互，阻止外部网络请求并记录 DOM 断言、控制台、页面错误和实际截图 Artifact。
- Requirement Analyst、Developer 和 Visual Reviewer 已通过供应商无关 ModelClient 接入，并在调用前检查所需模型能力。
- `WebVisualScenario` 已将 UI Analyst、Web Developer、Patch Integrator、Browser Tester、Visual Reviewer 和 Quality Gate 接入通用 `ScenarioRuntime + TaskGraphExecutor`；参考图作为外部 Artifact，失败后按 `ConvergenceDecision` 执行最多两轮 Fix DAG。
- 新增 `ArtifactDraft` 和 Worker staging store；Agent、Browser Tester 与 Quality Gate 不再直接写共享 `ArtifactStore`，产物只由 Executor 接纳。
- `SQLiteScenarioRunStore` 保存场景状态、当前轮次、每轮 Runtime Snapshot、活跃 Artifact 和终态结果；支持 Graph 完成后恢复、Gate 与 Fix 轮次之间恢复、completed 幂等恢复及 Workspace 漂移拒绝。
- Runtime 质量门禁组合构建、DOM/交互、控制台、页面/网络错误、视觉分数和 P1/P2，模型不能自行宣告 completed。
- Fixer 根据结构化反馈生成局部 Patch，最多修复两轮；旧 Patch、失败证据和最终验证状态通过 ArtifactStore 追踪。
- 旧 Runner 的 SQLite 返工 Checkpoint 仍保留兼容测试；产品路径改由 `SQLiteScenarioRunStore + SQLiteRuntimeStore` 统一恢复。
- Web 提供受控 PNG/JPEG 内容寻址上传和 VisionForge 任务 API；任务只接收 asset ID 与需求，固定使用 Runtime 创建的 Vue 项目目录。
- Web 可查看参考图、实际截图、评分、修复轮次和结构化 Artifact 调用链，不展示模型原始推理或内部项目路径。
- 建立 3 个版本化固定页面任务、Runtime 拥有的 DOM/交互断言和受控 HTML→PNG 参考图渲染器。
- 建立三方案统一评测协议与 JSON 报告，记录构建、功能、视觉、首次通过、自动修复、轮数、Token、耗时和人工介入。
- 建立 DeepSeek 文本模型与 DashScope Qwen 视觉模型的独立配置和按角色路由；客户端适配供应商级结构化输出模式与请求选项。
- 已用 `deepseek-v4-pro` 和 `qwen3.7-plus` 各完成一次经授权的最小真实能力烟测，图片输入、JSON 解析及 Token/耗时元数据均验证通过。
- 当前共有 115 个测试通过；其中 4 个真实浏览器类默认跳过，需要显式开启。

## 关键设计决策

- Workflow/Task DAG 决定何时执行，Role 决定执行能力，Agent 和模型是可替换 Worker。
- Harness 独占任务状态、权限、安全策略、Artifact 接纳和最终收敛判断。
- Agent 只能读取裁剪后的 `RoleMemoryView`，不能访问密钥或扩大权限。
- Agent 不能直接修改共享目录或直接改变 TaskGraphRuntime 状态。
- 节点之间通过 Artifact 引用交接，不通过共享可变对象隐式通信。
- 未经验证的推测不能晋升长期记忆。
- 当前阶段使用线程池和 SQLite，暂不引入外部工作流平台、向量数据库或图数据库。
- 输入模态与验证场景解耦：图片、音频或视频可以描述后端、CLI、库或前端任务；Visual Reviewer 只在显式 `web_visual` 场景启用。
- 通用 Coding 任务的 completed 只依赖构建、固定/隐藏测试、行为断言、权限和回归；不使用抽象视觉评分。

## 当前限制

- `timeout_seconds` 主要是策略元数据，不能强制终止运行中的模型线程。
- 暂停和取消只在 Worker 边界生效，不能立即中断 HTTP 请求或验证子进程。
- 资源冲突目前主要依赖精确 scope 字符串，尚无可靠的 glob 交集和符号级分析。
- Reviewer 和 Safety 尚未成为 DAG 最终收敛门禁。
- 记忆检索已有确定性单元测试，但尚未建立独立测评集、质量指标、真实任务对照实验和调优闭环。
- 当前使用实体精确命中和文本排序；是否需要向量或混合检索应由测评结果决定。
- 当前 Browser Tester 只支持固定 Vue 模板、单一本地 HTTP origin 和 Chromium；浏览器二进制由 Playwright 安装或由 Runtime 显式指定。
- 当前已完成一次保留失败记录和一次校准后真实基线，但 9 个试验的交付通过率仍为 0；结构化输出可靠性、构建错误证据保真和视觉修复稳定性尚未达到可用于产品结论的水平。
- 场景恢复不会重复已完成 DAG 节点；如果 Workspace 在快照后被外部修改，会拒绝自动恢复并要求人工处理。
- Web 和评测入口已经切换到 `VisionForgeScenarioRunner`；旧 `VisionForgeRunner` 与文件内 Legacy DAG Runner 只作兼容对照，不再由产品入口调用。
- Web 上传资产目录会跨进程保存，但 Web 任务列表目前只保存在内存中，服务重启后不能继续查询旧任务。
- 固定 Vue 模板当前共享单一浏览器端口，因此 Web Runtime 串行执行页面任务；尚未支持取消正在运行的任务。
- 固定评测框架的第二次真实运行只有 SaaS 任务形成完整三方案结果；其余任务受模型空内容、非法/截断 JSON 和不存在的图片引用影响。该报告可以作为可靠性诊断基线，但尚不能证明业务效果提升。
- 第一版固定任务集只有 3 个页面，适合 MVP 烟测，不足以产生统计上稳定的普遍结论。
- 当前还没有通用 `RequirementEvidence`、与 UI 无关的 `CodingRequirement` 或任务级 Validator Profile；图片协议已有场景实现，音频和视频尚未接入。
- 还没有用于比较单 Agent、Planner + Developer 和完整 Tester/Fixer 闭环的确定性 Coding 任务集。

## 下一步

优化事项统一维护在 `OPTIMIZATION_BACKLOG.md`，开始和完成每个批次时同步更新状态、验收结果和下一批内容。

批次 9 已完成产品与评测方向修正，详细决策见 `Plan/Plan09.md`。两次 VisionForge 真实报告继续保留为 `web_visual` 探索性证据；视觉人工校准暂缓，当前不再重跑开放式网页基线。

下一批只实现通用协议，不运行真实模型：

1. 建立 `RequirementEvidence`，统一引用 text/image/audio/video Artifact，并校验 MIME、大小、来源、哈希和授权范围。
2. 建立与 UI 无关的 `CodingRequirement`，保存目标、约束、验收、仓库范围和证据引用。
3. 建立 Runtime 拥有的 Validator Profile，从 build/test/API/CLI/browser 中选择确定性门禁；模型不能增加、删除或降低门禁。
4. 保持现有 UI Spec 与 Visual Review 兼容；`web_visual` 已作为首个多轮 DAG 场景，可供后续场景复用。
5. 每个协议都增加非法输入、JSON 往返、权限边界和兼容测试；现有测试尽可能全部保持通过。

本批不接供应商、不上传媒体、不调用模型，也不实现音视频转录。完成后等待用户确认，再建立固定 Coding 任务集和三方案评测器。

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
- `demo/coding_workflow/runtime_sqlite.py`：TaskGraphRuntime、生命周期和 Artifact 快照持久化。
- `demo/coding_workflow/visionforge/contracts.py`：UI Spec 与 Visual Review 1.0 协议。
- `demo/coding_workflow/visionforge/assets.py`：图片内容寻址存储与 Artifact 引用。
- `demo/coding_workflow/visionforge/agents.py`：四个 VisionForge 角色、结构化 Schema、模型能力和输入裁剪。
- `demo/coding_workflow/visionforge/browser.py`：受控进程、Vue 服务器生命周期、Playwright 调用与浏览器 Artifact。
- `demo/coding_workflow/visionforge/quality.py`：Runtime 组合质量门禁与可审计失败原因。
- `demo/coding_workflow/harness/scenario.py`：通用多轮 DAG、收敛决策、终态和恢复控制。
- `demo/coding_workflow/harness/scenario_sqlite.py`：场景清单、轮次与活跃 Artifact 持久化。
- `demo/coding_workflow/visionforge/dag.py`：VisionForge GraphWorker、外部参考图、主 DAG 与 Fix DAG Factory。
- `demo/coding_workflow/visionforge/scenario.py`：`WebVisualScenario` 和 `VisionForgeScenarioRunner` 产品入口。
- `demo/coding_workflow/visionforge/recovery.py`：SQLite 返工 Checkpoint、Artifact 快照和 Workspace 漂移检查。
- `demo/coding_workflow/visionforge/runner.py`：旧纵向执行兼容实现；Web 与评测已不再使用。
- `demo/coding_workflow/visionforge/web_runtime.py`：图片上传目录、固定项目准备、任务执行与安全公开快照。
- `demo/coding_workflow/visionforge/evaluation.py`：固定任务加载、参考图渲染、三方案试验记录、指标汇总和 JSON 报告。
- `demo/coding_workflow/visionforge/evaluation_runtime.py`：三方案隔离执行器、模型预算、固定验收注入和 Artifact Bundle 持久化。
- `demo/visionforge_eval_run.py`：真实基线预算预检与显式授权执行入口；默认不调用外部模型。
- `demo/coding_workflow/model/factory.py`：DeepSeek/Qwen 配置预设、环境变量读取和文本/视觉客户端独立路由。
- `demo/coding_workflow/model/openai_compatible.py`：OpenAI 兼容请求、多模态输入、供应商结构化输出模式和本地 Schema 约束。
- `demo/visionforge_model_smoke.py`：两次最小真实能力烟测入口；只报告安全的验证元数据。
- `demo/visionforge_vue_template/`：受保护的固定 Vue 3 + Vite 页面模板。
- `demo/visionforge_vue_template/.visionforge/browser-runner.mjs`：固定 viewport、受控交互、截图和浏览器证据采集。
- `demo/visionforge_vue_template/visionforge.ui-spec.json`：固定页面的可执行 UI Spec 测试用例。
- `demo/tests/test_visionforge.py`：VisionForge 协议、模型能力、图片和模板测试。
- `demo/tests/test_visionforge_browser.py`：命令生命周期和真实浏览器闭环测试。
- `demo/tests/test_visionforge_runner.py`：Fake Model 契约、安全 Patch、Artifact 链和真实纵向链路测试。
- `demo/tests/test_visionforge_rework.py`：质量门禁、修复上限、Artifact 替代、恢复和真实浏览器修复闭环测试。
- `demo/tests/test_visionforge_evaluation.py`：固定任务、实验配置、三方案汇总、Run 适配和真实参考图渲染测试。
- `demo/visionforge_eval/v1/`：版本化任务 manifest、3 个参考页面和 Runtime 固定验收 UI Spec。
- `demo/visionforge_eval/render-reference.mjs`：无外部网络、固定环境的 HTML→PNG 渲染器。
- `demo/visionforge_eval/README.md`：三方案反馈边界、统一评分口径和指标说明。
- `demo/coding_agent_cli.py`：通用 Coding DAG 的 CLI 入口。
- `demo/web_server.py`：通用 Harness 兼容入口与 VisionForge 上传、任务、图片读取 API。
- `demo/web/index.html`、`demo/web/app.js`、`demo/web/styles.css`：VisionForge 上传、进度、截图、轮次和 Artifact 调用链界面。
- `demo/tests/test_web_server.py`：Web Runtime、上传目录、受控请求字段和前端入口测试。
- `demo/tests/test_workflow.py`：Harness、DAG、记忆和端到端测试。
- `demo/docs/task-graph-and-memory.md`：设计边界说明。
- `Plan/Plan06.md`：任务拆分和记忆机制的策略归档。
- `Plan/Plan09.md`：多模态 Coding Multi-Agent MVP、客观验收和实施顺序。
- `OPTIMIZATION_BACKLOG.md`：优化批次、优先级、状态和验收标准。

## 验证命令

在 `/Users/donbblu/codex/multiAgent/demo` 执行：

```bash
python3 -m unittest discover -s tests -q
```

真实浏览器测试需要先安装 Chromium，或通过 `VISIONFORGE_BROWSER_EXECUTABLE` 指定 Runtime 管理的 Chrome/Chromium，再设置 `VISIONFORGE_E2E=1` 执行 `test_visionforge_browser.py`。

在仓库根目录执行：

```bash
git diff --check
git status --short
```

## Git 基线

- 仓库：`/Users/donbblu/codex/multiAgent`
- 分支：`main`
- 远端：`git@github.com:donbblu/MultiAgent.git`
- 当前基线提交：`7c7c525 chore: archive daily progress 2026-08-15`
- `.env`、`.runtime/`、`.runs/`、运行输出和 `.DS_Store` 不得提交。

## 安全提醒

- 不读取、打印或提交 `.env` 和 API Key。
- 不让模型生成的路径绕过 ProjectWorkspace 与 PatchIntegrator。
- 不用模型记忆覆盖权限、安全策略、验收条件或状态机。
- 不展示或持久化 Agent 的原始思维过程，只记录摘要、事件、结果和证据。
