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
- 当前共有 213 个测试通过；其中 4 个真实浏览器类默认跳过，需要显式开启。
- 建立 Core `Claim`、三态 `VerificationOutcome` 和不可变 `VerificationRecord`；模型观察、推断和建议不会自动成为已验证事实，验证记录随 SQLite Runtime Snapshot 恢复。
- Worker Artifact metadata 会拒绝伪造的验证字段，正文中的同名业务数据不会改变外层状态；TaskGraph 节点全部成功不再自动验证产物、晋升长期记忆或宣布 completed，`GraphExecutionResult.acceptance_outcome` 默认是 unknown。
- VisionForge 质量门禁不再循环自证：build/browser/review 验证 quality gate，quality gate 再验证其他周期产物；场景流程和视觉规则未改变。
- 建立通用 `RequirementEvidence`、`CodingRequirement`、`RepositoryScope`、`AcceptanceCriterion`、`EvidenceGrant` 和冻结的 `ValidatorProfile`；UI Spec 继续只属于 VisionForge。
- 结构化需求启用后，Executor 会在调用 Worker 前核对 Evidence Artifact、仓库范围、Profile/Criterion 摘要和 Task/Role 授权；缺失授权不会回退为宽松模式。
- 建立 Runtime `ValidatorRegistry + ValidatorProfileRunner`，缺失能力或执行异常产生 unknown 和报告 Artifact；组合 profile gate 独占最终 Artifact 验证更新。
- VerificationRecord 绑定 Artifact 内容哈希和可选 Workspace 摘要；`required_verified_inputs` 会拒绝过期证明，并随 SQLite Runtime Snapshot 恢复。
- `WorkerRegistry` 已支持同一 Role 多个 `WorkerDescriptor`，按 Role、能力、协议、策略、职责隔离、可用性和稳定 tie-break 选择；结构化选择审计随 SQLite Checkpoint 恢复。
- 无合格 Worker 会确定性进入 blocked，不跨 Role 或降低要求；Runtime producer provenance 用于阻止 Reviewer/Tester/Validator 对同一 principal 的产物自审或自证。
- VisionForge 已通过 `VisionForgePlugin` 显式注册为 `visionforge:web_visual`；Web 从 PluginRegistry 解析场景，场景状态保存插件 ID/版本，未启用插件时安全拒绝。
- UI Spec、参考图、实际截图、Browser Run、Visual Review、视觉质量门禁和场景 Run 使用 `visionforge:*` Artifact kind；Core 不解释这些业务协议。
- Core 已实现受控 `core:build`、`core:test`、`core:cli` Validator：完整 argv 白名单、无 shell、清理环境、进程组超时清理和脱敏裁剪日志；超时或缺失工具保持 unknown。
- 建立首个版本化离线 Coding 任务 `python-tax-rounding`；Runtime 只在独立验证副本注入隐藏检查，starter 稳定失败、参考修复稳定通过，Profile gate 与验证 Workspace 摘要绑定。
- 固定 Core Coding 任务集已扩展为舍入 Bug、API payload 输入契约和跨文件 CLI 三类；build/test/CLI 分别提供确定性门禁，不依赖网页或模型评分。
- `FixedCodingEvaluationRunner` 每次复位独立 Workspace，并生成版本化离线 JSON 校准报告；3 个 starter 全部失败、3 个参考修复全部通过，坏题不会被误判为校准成功。
- 建立三方案 `AblationStrategyProfile`、统一预算和 Artifact 可见性策略；单 Agent、Planner + Developer 与完整 Tester/Fixer 使用同一固定任务和 Validator。
- 脚本化 dry-run 已覆盖 9 个 trial 的首次通过、修复、预算、越权和指标路径；报告明确区分脚本/模型用量，当前真实模型调用为 0，结果不得用于宣称多 Agent 更优。

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
- Core 通过显式 `PluginRegistry` 接纳可信场景插件；Core 不依赖具体插件，场景使用 `plugin_id:scenario` 命名空间并按 Core API 版本校验；场景快照同时保存插件 ID/版本并在恢复时拒绝漂移。
- 模型只有提交观察、推断、建议和候选产物的权力；只有 Runtime 根据 Validator 产生的执行证据，才能登记验证结果和决定最终完成。Worker 的正常返回不等于验收通过，证据不足必须保持 `unknown/unverified`，不能解释为通过。
- Core 只定义通用 Requirement、Evidence、Claim、Verification 和 Validator 协议。`UI Spec`、视觉评分和视觉问题分类属于 VisionForge 插件，Core 只按带命名空间的 Artifact 类型保存、授权和传递。
- Role 始终是 Worker 路由的第一键，承载职责、权限、记忆视图和职责隔离；同一 Role 后续允许注册多个 Worker，再由能力、输入/输出协议、运行策略和可用性进行确定性筛选，不能因为缺少 Worker 而降低要求或改派其他 Role。

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
- Core 已有 build/test/CLI Validator 实现，但通用 CLI/Web 产品入口尚未装配固定任务 Profile；API/browser Validator 仍由后续 Core 实现或场景插件提供，VisionForge 继续使用已有场景门禁。
- EvidenceGrant 当前由 Composition Root 注入且不作为可跨进程复用的授权凭据持久化；恢复时必须重新提供，否则结构化需求会安全拒绝。
- text/image/audio/video 已有统一 Evidence 描述协议；图片感知、音频转录和视频 Bug 时间线的 Core 协议与 Fake Client 链路已接入。真实媒体持久化及真实图片、语音和视频供应商适配尚未接入。
- 确定性 Coding 评测已有 3 个小型任务，能够覆盖函数、API 输入和跨文件 CLI，但样本仍不足以代表普遍 Coding 能力；三方案已接入通用 ModelClient Worker 并用 Fake Model 验证，尚未形成真实模型效果对照。
- VisionForge 仍位于 `coding_workflow/visionforge`，但已作为显式插件装配；本批未做包目录大迁移或删除 Legacy Runner。
- Core 图片、音频和视频输入已分别通过受控 Worker 转成 `core:image_observation`、`core:audio_transcript` 和 `core:video_bug_evidence`。三条链路都保留原 Evidence 引用，下游文本 Agent 不重复读取原媒体，最终仍使用原固定 Validator。
- Core 已建立统一 `MultimodalIntakeRunner + core:evidence_bundle`：同一需求的媒体感知可并行执行，每个原始 Artifact 最多处理一次；Bundle 保存每条来源和 ready/blocked/failed，任一必需证据未就绪时普通 Planner 不会被调用。

## 下一步

优化事项统一维护在 `OPTIMIZATION_BACKLOG.md`，开始和完成每个批次时同步更新状态、验收结果和下一批内容。

批次 10A 已完成 Core 插件边界，详细决策见 `Plan/Plan11.md`。Core 现在能够在零插件下运行，并以原子、版本化、命名空间化方式注册场景；本批没有把 VisionForge 接入 Registry。

后续严格按下面顺序一次推进一个批次。除非用户明确修改方向，不得跳过当前 Core 批次直接迁移 VisionForge、扩展媒体、调度或记忆。

批次 10B 已完成事实与验证权边界，见 `Plan/Plan12.md`；批次 10C 已完成通用需求与验收协议，见 `Plan/Plan13.md`；批次 10D 已完成 Role 优先的多 Worker 路由，见 `Plan/Plan14.md`；批次 10E 已完成 VisionForge 插件适配，见 `Plan/Plan15.md`；批次 11A 已完成受控 Validator 与首个固定任务，见 `Plan/Plan16.md`；批次 11B 已完成三类任务与离线校准报告，见 `Plan/Plan17.md`；批次 11C 已完成三方案协议与脚本化 dry-run，见 `Plan/Plan18.md`；批次 11D 已完成 ModelClient Worker 与调用前审计，见 `Plan/Plan19.md`。

批次 11E 的真实消融入口、全局预算和零网络 preflight 已完成，但真实调用没有获得明确授权，现标记暂缓；恢复时必须重新核对 `Plan/Plan20.md` 中的摘要，不能沿用模糊授权。

批次 12A 已完成 Core 图片需求证据链，见 `Plan/Plan21.md`。图片通过 RequirementEvidence 和 `vision:inspect` 授权进入 `planner` Role 下的视觉感知 Worker，输出通用 Claim Artifact；UI Spec 继续只属于 VisionForge，代码最终仍由同一固定 Validator 验收。

批次 12B 已完成 Core 音频需求证据链，见 `Plan/Plan22.md`。音频通过 RequirementEvidence 和 `audio:transcribe` 授权进入同一 `planner` Role 下的专用转录 Worker，输出带时间戳、不确定项和原音频引用的 Transcript/Claim Artifact；下游普通 Planner 只读取结构化转录。专项 7 项和完整 199 项测试通过，全程只使用 Fake Client 与本地字节。

批次 12C 已完成 Core 视频 Bug 证据链，见 `Plan/Plan23.md`。视频通过 RequirementEvidence 和 `video:inspect` 授权进入同一 `planner` Role 下的视频感知 Worker，输出事件时间线、候选复现步骤、预期/实际差异及 observation/inference/proposal Claim；下游普通 Planner 只读取结构化 Artifact。专项 7 项和完整 206 项测试通过，全程只使用 Fake Client 与本地 MP4 字节。

批次 13A 已完成统一多模态 Intake，见 `Plan/Plan24.md`。同一个 CodingRequirement 可以同时引用 text/image/audio/video Evidence；Runtime 先做授权和完整性预检，再并行执行媒体 Worker，生成带逐条状态和 Claim 来源的 `core:evidence_bundle`。只有 Bundle 全部 ready 时普通文本 Planner 才会被调用。专项 7 项和完整 213 项测试通过，全程只使用 Fake Client 与本地字节。

当前已规划的 Core 多模态 MVP 批次全部完成。下一步先做里程碑验收，不自动扩展功能；由用户决定是否提交/推送当前 12B、12C、13A 修改，或另行规划真实供应商适配、产品入口和固定多模态评测。`CORE-ABLATION-001` 仍暂缓，任何真实调用都需要新的明确授权。

## 关键文件

- `demo/coding_workflow/harness/task_graph.py`：TaskSpec、DAG 校验和 ready 任务选择。
- `demo/coding_workflow/harness/scheduler.py`：任务图运行状态和 Artifact 就绪管理。
- `demo/coding_workflow/harness/executor.py`：并发调度、局部重试和节点结果接纳。
- `demo/coding_workflow/planning.py`：结构化 Planner 和非法图修复。
- `demo/coding_workflow/dag_runner.py`：真实 DAG 端到端执行入口。
- `demo/coding_workflow/graph_workers.py`：DAG Worker 契约实现。
- `demo/coding_workflow/artifacts.py`：Artifact 与 ArtifactStore。
- `demo/coding_workflow/truth.py`：Claim、三态验证结果和不可变 VerificationRecord。
- `demo/coding_workflow/requirements.py`：通用 Evidence、Coding Requirement、仓库范围、验收、授权和冻结 Validator Profile。
- `demo/coding_workflow/validator_runtime.py`：Runtime Validator 注册、按 Profile 执行、报告 Artifact 和组合门禁。
- `demo/coding_workflow/command_validators.py`：受控 build/test/CLI 子进程、完整 argv 白名单、日志证据和三态判定。
- `demo/coding_workflow/coding_evaluation.py`：固定任务清单校验、Agent/私有验证 Workspace 隔离和 Profile 运行入口。
- `demo/coding_workflow/coding_evaluation_runtime.py`：多任务复位、starter/参考修复校准、trial 指标和 1.0 JSON 报告。
- `demo/coding_workflow/coding_ablation.py`：三方案 Profile、Artifact 可见性、统一预算、脚本 Worker、trial 和消融报告。
- `demo/coding_workflow/coding_model_workers.py`：四类 ModelClient Worker、Plan/Patch/Diagnosis Schema、严格解析、源码披露审计与请求哈希。
- `demo/coding_workflow/coding_ablation_execution.py`：真实实验配置、21 次调用估算、源码 preflight、真实 Runner 装配与报告 Bundle。
- `demo/coding_workflow/image_perception.py`：通用图片 Evidence、视觉 Claim 协议、感知 Worker、Role-first 注册与客观准确率。
- `demo/coding_workflow/audio_transcription.py`：供应商无关转录协议、时间戳 Transcript/Claim、音频授权与完整性检查、Role-first 注册和客观准确率。
- `demo/coding_workflow/video_perception.py`：供应商无关视频时间理解、事件/差异/候选复现协议、视频授权与完整性检查和 Role-first 注册。
- `demo/coding_workflow/multimodal_intake.py`：统一 Intake Plan、逐模态预检、并行媒体 DAG、状态化 Evidence Bundle、失败关闭和文本 Planner 交接。
- `demo/coding_workflow/model/budget.py`：Core 共享模型调用/Token 预留预算和受控客户端包装。
- `demo/coding_eval/v1/`：版本化 Core Coding starter、隐藏验收、参考答案和哈希清单。
- `demo/core_coding_eval_run.py`：默认零外部调用的离线题目校准 CLI。
- `demo/core_coding_ablation_run.py`：默认禁止真实模型的三方案脚本化 dry-run CLI。
- `demo/core_coding_model_ablation_run.py`：默认零网络的真实消融 preflight 与摘要绑定执行入口。
- `demo/coding_workflow/harness/registry.py`：WorkerDescriptor、Role-first 多实现筛选、结构化拒绝和选择审计。
- `demo/coding_workflow/roles.py`：Role 的职责、能力和权限边界；后续仍作为 Worker 第一路由键。
- `demo/coding_workflow/integration.py`：Patch 安全检查和集中合并。
- `demo/coding_workflow/memory.py`：分层记忆、Working Memory 和 RoleMemoryView。
- `demo/coding_workflow/memory_sqlite.py`：记忆和 Checkpoint 持久化。
- `demo/coding_workflow/runtime_sqlite.py`：TaskGraphRuntime、生命周期和 Artifact 快照持久化。
- `demo/coding_workflow/visionforge/contracts.py`：UI Spec 与 Visual Review 1.0 协议。
- `demo/coding_workflow/visionforge/artifact_types.py`：VisionForge 私有 `visionforge:*` Artifact kind。
- `demo/coding_workflow/visionforge/plugin.py`：VisionForge Manifest、`web_visual` 注册和产品 Registry 工厂。
- `demo/coding_workflow/visionforge/assets.py`：图片内容寻址存储与 Artifact 引用。
- `demo/coding_workflow/visionforge/agents.py`：四个 VisionForge 角色、结构化 Schema、模型能力和输入裁剪。
- `demo/coding_workflow/visionforge/browser.py`：受控进程、Vue 服务器生命周期、Playwright 调用与浏览器 Artifact。
- `demo/coding_workflow/visionforge/quality.py`：Runtime 组合质量门禁与可审计失败原因。
- `demo/coding_workflow/harness/scenario.py`：通用多轮 DAG、收敛决策、终态和恢复控制。
- `demo/coding_workflow/harness/plugins.py`：Core 插件 Manifest、受控注册上下文、命名空间场景和 Registry。
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
- `demo/tests/test_plugins.py`：Core 插件注册、兼容、原子性、缺失语义和反向依赖禁令测试。
- `demo/tests/test_truth.py`：事实分类、验证权、伪造字段、unknown、执行/验收分离和 SQLite 往返测试。
- `demo/tests/test_requirements.py`：需求协议、范围冻结、EvidenceGrant、Profile Runner、验证新鲜度和恢复测试。
- `demo/tests/test_worker_routing.py`：同 Role 多 Worker、硬过滤、blocked、选择恢复和自审/自证隔离测试。
- `demo/tests/test_command_validators.py`：命令策略、三态、脱敏裁剪、隐藏边界、任务哈希和 starter/solution 门禁测试。
- `demo/tests/test_coding_evaluation_runtime.py`：三任务校准、Workspace 复位、JSON 报告和坏题拒绝测试。
- `demo/tests/test_coding_ablation.py`：三策略、反馈隔离、同预算、越权、预算停止、模型误接入和报告测试。
- `demo/tests/test_coding_model_workers.py`：Fake Model 纵向修复、能力预检、秘密披露、越权 Patch、伪造通过和 Registry 隔离测试。
- `demo/tests/test_coding_ablation_execution.py`：调用估算、全局预算、重试/输出限制、源码摘要和授权前零凭据测试。
- `demo/tests/test_image_perception.py`：图片授权、能力/哈希边界、Claim 幻觉约束、同 Role 路由、感知 F1 和同隐藏验收测试。
- `demo/docs/task-graph-and-memory.md`：设计边界说明。
- `Plan/Plan06.md`：任务拆分和记忆机制的策略归档。
- `Plan/Plan09.md`：多模态 Coding Multi-Agent MVP、客观验收和实施顺序。
- `Plan/Plan11.md`：Core 插件边界、信任模型和 VisionForge 后续迁移顺序。
- `Plan/Plan12.md`：Core 事实与验证权边界、兼容策略和未完成事项。
- `Plan/Plan13.md`：Core 通用需求、Evidence 授权、Validator 执行和新鲜度边界。
- `Plan/Plan14.md`：Role-first 多 Worker 路由、审计、blocked 和 principal 职责隔离。
- `Plan/Plan15.md`：VisionForge 插件装配、Web 解析、场景身份和 Artifact 命名空间边界。
- `Plan/Plan16.md`：受控 Core Validator、私有隐藏验收和首个固定离线 Coding 任务。
- `Plan/Plan17.md`：多类型固定任务、离线复位、自校准规则和版本化报告。
- `Plan/Plan18.md`：三方案 Artifact/预算协议、脚本化 dry-run 和结果解释边界。
- `Plan/Plan19.md`：Core 模型 Worker、结构化输出、调用前披露、请求审计和真实运行前置条件。
- `Plan/Plan20.md`：真实消融冻结配置、源码外发范围、预算硬边界和授权摘要。
- `Plan/Plan21.md`：Core 图片需求证据链、视觉感知/文本规划拆分和确定性评测边界。
- `Plan/Plan22.md`：Core 音频需求证据链、独立转录客户端、时间线/不确定性和同一代码验收边界。
- `Plan/Plan23.md`：Core 视频 Bug 证据链、观察/推测/建议分离、时间线引用和同一回归验收边界。
- `Plan/Plan24.md`：统一多模态 Intake、并行/单次处理、状态化 Bundle、失败关闭和下游隔离。
- `OPTIMIZATION_BACKLOG.md`：优化批次、优先级、状态和验收标准。

## 验证命令

在 `/Users/donbblu/codex/multiAgent/demo` 执行：

```bash
python3 -m unittest discover -s tests -q
PYTHONPYCACHEPREFIX=/private/tmp/multiagent-pycache python3 -m compileall -q coding_workflow tests
```

真实浏览器测试需要先安装 Chromium，或通过 `VISIONFORGE_BROWSER_EXECUTABLE` 指定 Runtime 管理的 Chrome/Chromium，再设置 `VISIONFORGE_E2E=1` 执行 `test_visionforge_browser.py`。

在仓库根目录执行：

```bash
git diff --check
git status --short
```

## Git 基线

- 仓库：`/Users/donbblu/codex/multiAgent`
- 分支：`codex/multimodal-coding-mvp`
- 远端：`git@github.com:donbblu/MultiAgent.git`
- 当前基线提交：`a7c465d chore: archive daily progress 2026-08-20`
- `.env`、`.runtime/`、`.runs/`、运行输出和 `.DS_Store` 不得提交。

## 安全提醒

- 不读取、打印或提交 `.env` 和 API Key。
- 不让模型生成的路径绕过 ProjectWorkspace 与 PatchIntegrator。
- 不用模型记忆覆盖权限、安全策略、验收条件或状态机。
- 不展示或持久化 Agent 的原始思维过程，只记录摘要、事件、结果和证据。
