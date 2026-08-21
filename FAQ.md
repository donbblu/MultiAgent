# Multi-Agent / VisionForge 项目 FAQ

本文整理当前项目架构图中容易混淆的概念，并区分：

- **规划架构**：项目希望形成的长期职责边界。
- **当前实现**：仓库中已经存在并可运行的能力。
- **生产化能力**：面向真实多用户环境仍需补充的部分。

## 1. 入口层的 Evaluation 是什么？

`Evaluation` 是评测入口，不是普通用户使用产品的入口。它使用固定任务集重复运行 VisionForge，用来衡量系统效果、比较不同反馈方案，并检查后续修改是否破坏已有能力。

当前评测会比较三种方案：

1. `llm_once`：LLM 只生成一次，浏览器和 VLM 只负责最终评分。
2. `llm_browser_feedback`：允许 Fixer 使用浏览器构建、DOM、交互和运行错误反馈。
3. `llm_browser_vlm`：Fixer 同时使用浏览器反馈和 VLM 视觉反馈。

评测指标包括构建成功率、DOM/交互通过率、视觉验收通过率、最终交付成功率、自动修复成功率、Token 消耗和执行耗时等。

因此入口层更准确的划分是：

```text
产品入口
├── CLI
└── Web

评测与开发入口
└── Evaluation
```

## 2. `Spec` 后缀通常表示什么？

`Spec` 是 Specification 的缩写，表示结构化规格或执行契约。它负责描述“应该做什么”，而不是执行“如何完成”。

例如 `TaskSpec` 描述一个 DAG 节点的：

- 任务目标和执行角色。
- 前置依赖。
- 输入、输出 Artifact。
- 可读写范围。
- 验收条件。
- 超时和重试次数。

Harness 校验 `TaskSpec` 后，才会决定节点何时执行以及交给哪个 Worker。

规划图中的 `ScenarioSpec` 是概念性名称，当前代码中没有同名类。当前真实实现主要使用：

- `ScenarioProfile`：定义如何构建每轮 DAG，以及如何判断完成、返工、失败或需要输入。
- `ScenarioRoundPlan`：描述当前轮的 TaskGraph、Worker、角色、记忆和初始 Artifact。
- `TaskSpec`：描述 DAG 内的单个原子任务。

因此后续架构图应使用 `ScenarioProfile → ScenarioRoundPlan`，不再使用容易被误解为真实类名的 `ScenarioSpec`。

## 3. Artifact Schema 是什么？每次任务都会不同吗？

Artifact 是 Worker 之间唯一的结构化结果交接方式。例如：

```text
UI Analyst
    ↓ UI Spec Artifact
Web Developer
    ↓ Implementation Plan Artifact
Patch Integrator
```

Artifact Schema 规定某一种 Artifact 必须包含哪些字段、字段类型是什么以及哪些值有效。这样，下游 Worker 不需要从任意自然语言中猜测上游输出的含义。

当前设计分为两层：

```text
通用 Artifact 外壳
├── artifact_id
├── name
├── task_id
├── kind
├── content
├── metadata
└── created_at

按 kind 区分的场景载荷
├── ui_spec
├── implementation_plan
├── build_result
├── browser_run
├── visual_review
└── quality_gate
```

每次任务变化的是 Artifact 内容，而不是对应 Schema 的基本结构。例如，不同页面生成的 `UI Spec` 内容不同，但它们都遵循同一个版本化 UI Spec 协议。

通用性通过以下方式保持：

1. 所有 Artifact 使用相同的外层 ID、类型、任务归属、元数据和验证状态。
2. 不同 `kind` 使用各自稳定、版本化的 content Schema。
3. Harness 只处理引用、依赖、权限、接纳和验证状态，不需要理解所有业务字段。
4. 场景负责定义和校验自己的 Artifact 类型。
5. 新场景可以增加新的 Artifact Schema，不需要修改整个 Harness。

## 4. UI Spec 是什么？

UI Spec 是页面的结构化设计说明书。UI Analyst 根据文本需求和参考图生成 UI Spec，Web Developer、Browser Tester 和 Visual Reviewer 再使用它完成开发和验证。

当前 `UISpec` 主要包括：

```text
UISpec
├── schema_version
├── page_type
├── viewport
├── layout
│   └── 页面区域、顺序和父子关系
├── components
│   └── 组件类型、文字、区域、test_id 和属性
├── texts
├── interactions
│   └── click、fill、expect_visible、expect_text、expect_url
├── acceptance_criteria
└── style_tokens
```

UI Spec 不是 Vue 代码，也不是截图，而是参考图、需求和实现代码之间的中间协议。

项目中还需要区分：

- **Analyst 生成的 UI Spec**：描述模型对页面结构和视觉要求的理解。
- **Runtime 持有的 Acceptance Spec**：固定功能与交互验收要求，模型不能修改或降低。

评测时必须优先使用 Runtime 固定的验收条件，防止模型通过修改验收标准让自己通过。

## 5. 视觉质量门禁是什么？

当前门禁并不只是检查视觉，因此更准确的名称是 `VisionForge Quality Gate`。它由 Runtime 汇总真实执行证据后确定性判断，不能仅依赖 VLM 的一句“通过”。

当前门禁要求同时满足：

- Vue 项目构建成功。
- DOM 和交互断言全部通过。
- 浏览器没有严重控制台错误。
- 没有页面运行错误。
- 没有被阻止的外部网络请求。
- Browser Run 协议通过。
- Visual Reviewer 声明通过。
- 视觉评分达到 Runtime 阈值，当前默认值为 85。
- 没有 P1/P2 阻塞级视觉问题。

任意一项失败，整个质量门禁都会失败。VLM 的判断只是门禁证据之一，最终完成状态由 Runtime 决定。

## 6. 统一 DAG Harness 的四个部分分别做什么？数字表示顺序吗？

图中的 1、2、3、4 表示存在调用关系的职责模块，不是四个 Agent 顺序执行一次。

### 1. ScenarioProfile

定义场景如何工作，包括：

- 每轮使用什么 DAG。
- 使用哪些 Worker 和角色。
- 有哪些外部 Artifact。
- 如何根据证据判断完成、返工、失败或需要用户输入。

### 2. ScenarioRuntime

负责跨轮控制：

- 调用 ScenarioProfile 构建当前轮。
- 控制当前轮数和最大返工轮数。
- 保存、恢复场景状态。
- 记录活跃 Artifact。
- 独占最终状态和收敛判断。

### 3. TaskGraphExecutor

负责执行单轮 DAG：

- 校验 TaskGraph。
- 从 ready queue 调度节点。
- 根据依赖、Artifact 和资源冲突决定并行性。
- 执行节点重试和超时。
- 接纳 Worker 返回的 ArtifactDraft。
- 保存节点级 Runtime Snapshot。

### 4. WorkerRegistry

负责将 TaskSpec 中的角色名称映射到实际 Worker，例如：

```text
ui_analyst     → UIAnalystWorker
browser_tester → BrowserDagWorker
quality_gate   → QualityGateWorker
```

实际调用关系是：

```text
ScenarioRuntime
    ├── ScenarioProfile.build_round()
    │       └── ScenarioRoundPlan
    │               ├── TaskGraph
    │               └── WorkerRegistry
    ├── TaskGraphExecutor.run()
    │       └── 按 WorkerRegistry 执行节点
    └── ScenarioProfile.decide()
            ├── Complete
            ├── Rework
            ├── Fail
            └── Needs Input
```

## 7. VisionForge Worker 是一个单独的 Worker 吗？

不是。架构图中的 “VisionForge Worker” 是一个分组标题，表示 VisionForge 场景组合使用的一组专业 Worker。更准确的名称是 `VisionForge Worker Group` 或 `VisionForge Worker Lane`。

Generic Coding 和 VisionForge 使用不同 Worker，是因为它们解决不同业务问题：

```text
Generic Coding Worker Group
├── Planner
├── Implementer
├── Tester
└── Fixer

VisionForge Worker Group
├── UI Analyst
├── Web Developer
├── Patch Integrator
├── Browser Tester
├── Visual Reviewer
├── Quality Gate
└── Web Fixer
```

虽然业务能力不同，但所有 Worker 接入 Harness 的方式相同：

```text
读取 TaskContext 和输入 Artifact
              ↓
执行专业能力
              ↓
返回 ArtifactDraft
              ↓
由 TaskGraphExecutor 校验和接纳
```

Worker 的专业差异属于场景层；ArtifactDraft、权限、调度、接纳和状态管理属于通用 Harness。

## 8. VisionForge 的六个步骤是顺序执行还是按需调用？

当前 VisionForge 主 DAG 基本按照依赖顺序执行：

```text
UI Analyst
    ↓
Web Developer
    ↓
Patch Integrator
    ↓
Browser Tester
    ↓
Visual Reviewer
    ↓
Quality Gate
```

具体依赖为：

1. UI Analyst 读取参考图和需求，输出 UI Spec。
2. Web Developer 读取 UI Spec，输出 Implementation Plan。
3. Patch Integrator 校验并安全应用 Implementation Plan。
4. Browser Tester 在修改完成后构建页面、运行交互测试并截图。
5. Visual Reviewer 读取参考图、UI Spec、实际截图和 Browser Run，输出 Visual Review。
6. Quality Gate 同时读取 Build Result、Browser Run 和 Visual Review，作出最终判断。

如果门禁失败，系统进入 Fix DAG：

```text
Web Fixer
    ↓
Patch Integrator
    ↓
Browser Tester
    ↓
Visual Reviewer
    ↓
Quality Gate
```

它们不是自由聊天的 Agent，也不是运行时随机挑选。DAG 根据依赖和 Artifact 明确决定下一步。

Harness 本身支持并行，但节点只有同时满足以下条件才会并行：

- 所有前置依赖已经成功。
- 输入 Artifact 已经就绪。
- 与正在运行的节点没有读写资源冲突。
- 没有超过 Worker 并发额度。

## 9. 项目是否内置 Git 进行版本管理？

项目源码仓库本身使用 Git，并且已经关联 GitHub 远端。但 Multi-Agent Runtime 当前没有实现完整的任务级 Git 工作流。

当前 Runtime 不会自动：

- 为每个任务创建分支或 worktree。
- 自动生成 Git commit。
- 使用 Git commit 回滚。
- 创建 Pull Request。
- 将每次 Agent 修改映射为一个独立提交。

目前 `ProjectWorkspace` 直接在受控项目目录中进行原子文件写入，`PatchIntegrator` 校验允许修改的路径并禁止修改 `.git`、`.env`、`.runs` 和 `.verification` 等受保护路径。

因此准确表述是：

> 项目源码使用 Git 管理，但 Agent Runtime 尚未将 Git 作为任务隔离、版本恢复和交付机制。

进一步生产化时可以增加 `GitWorkspace`：

```text
GitWorkspace
├── 任务独立 branch/worktree
├── 修改前基线 commit
├── 自动 diff
├── 验证通过后 commit
├── 失败时恢复
└── 输出 PR 或 Patch Bundle
```

## 10. 当前没有数据库和后端 API，项目是否还无法落地？

项目目前已经具备 SQLite 和本地 HTTP API，并不是完全没有数据库或后端。

### 当前数据库能力

- `SQLiteMemoryStore`：保存长期记忆和 Working Memory。
- `SQLiteRuntimeStore`：保存 DAG、Artifact、生命周期和 Workspace 哈希快照。
- `SQLiteScenarioRunStore`：保存场景轮次、状态、活跃 Artifact 和关联 Runtime Snapshot。

### 当前 API 能力

- `POST /api/visionforge/assets`：上传参考图。
- `POST /api/visionforge/tasks`：提交 VisionForge 任务。
- `GET /api/visionforge/tasks/{id}`：查询任务状态和结果。
- 参考图、实际截图和 Artifact 查询接口。

因此，当前项目已经是一个可本地运行的垂直 MVP：可以提交需求和参考图，生成 Vue 页面，运行浏览器验证，执行 VLM 视觉审查，自动修复并查看 Artifact 调用链。

但是它还不是生产级 SaaS，主要缺少：

- 用户认证、授权和多租户隔离。
- 生产数据库、对象存储和数据迁移。
- 分布式任务队列和多 Worker 部署。
- 浏览器实例、端口和计算资源调度。
- 模型限流、成本配额、熔断和降级。
- 完整日志、Tracing、指标和告警。
- Secret 管理。
- Git branch/worktree 隔离。
- API 版本管理、幂等和请求限流。
- 部署、容灾、备份和恢复策略。
- 人工审核与失败接管流程。

当前更准确的定位是：

> 可运行、可评测、具备恢复能力的本地工程化 MVP，而不是生产级 SaaS。

“能够落地”和“达到生产级”不是同一标准。当前系统可以作为本地开发工具、课程项目、研究原型和求职演示使用，但还不适合直接服务大量真实用户。

## 11. 如何设置收敛标准？

收敛标准必须由场景声明、Runtime 固定并根据真实证据判断。Agent 可以尝试完成任务，但不能自行宣布已经完成。

### 第一层：节点收敛

```text
节点成功 =
输出 Artifact 符合 Schema
+ 未越过读写权限
+ 执行未超时
+ Artifact 被 Executor 接纳
```

### 第二层：单轮质量收敛

以 VisionForge 为例：

```text
单轮通过 =
构建成功
+ DOM/交互测试通过
+ 无严重控制台和页面错误
+ 无非法网络访问
+ 视觉评分达到固定阈值
+ 无 P1/P2 视觉问题
```

### 第三层：场景收敛

```text
if Quality Gate 通过:
    Completed
elif 还有修复额度且问题可自动修复:
    创建 Fix DAG
elif 需求、证据或权限不足:
    Needs Input
else:
    Failed
```

### 推荐的完整收敛约束

1. **硬性门禁**：构建、测试、交互和安全检查必须全部通过。
2. **场景专项门禁**：VisionForge 使用固定视觉阈值和 P1/P2 问题检查；通用 Coding 不使用视觉分数。
3. **修复轮数上限**：当前 VisionForge 最多自动修复两轮。
4. **资源预算**：限制总 Token、模型调用次数、耗时和费用。
5. **无进展停止**：连续两轮失败签名相同，或关键指标没有改善时停止自动修复。
6. **Patch 边界**：每轮只能修改授权路径，并限制文件数、代码量和风险操作。
7. **验收标准冻结**：Agent 不能删除、增加或降低 Runtime 持有的最终门禁。
8. **证据新鲜度**：最终判断只能使用当前代码对应的最新构建、测试和截图。
9. **完整回归**：局部修复后必须重新运行完整质量门禁，不能只运行失败的单项测试。
10. **人工接管**：需求含糊、证据缺失、权限不足或修复额度耗尽时进入 `Needs Input` 或 `Failed`，不能无限循环。

不同场景应配置不同的 Validator Profile：

```text
Generic Coding
→ build + unit test + regression test

API Coding
→ build + HTTP contract test + regression test

CLI Coding
→ exit code + stdout/stderr assertions

VisionForge
→ build + browser interaction + screenshot + visual review
```

项目收敛的核心原则是：

> 场景定义什么叫成功，Runtime 根据不可篡改的执行证据判断是否成功，Agent 只能产生候选结果，不能决定最终状态。

## 12. 如何让 Runtime 不给模型“把推测变成事实”的权力？

不能只靠 Prompt 要求模型诚实，必须从数据协议和状态权限上限制。系统需要区分三类模型输出：

- `observation`：对已有证据的描述，但仍需保留原始证据引用。
- `inference`：模型根据证据作出的推断，例如“可能是 CSS 优先级导致”。
- `proposal`：建议执行的操作、候选 Patch 或下一步验证。

这些输出统称为 `Claim`，默认都不是已验证事实。只有 Runtime 执行 Validator 并保存不可变 `VerificationRecord` 后，才能改变 Artifact 的验证状态。

验证结果使用三态，而不是简单布尔值：

```text
passed  → 有充分执行证据证明通过
failed  → 有充分执行证据证明失败
unknown → 缺少能力、证据不完整、执行超时或无法判断
```

`unknown` 不能被解释为通过，对应 Artifact 应继续保持 `UNVERIFIED`。

当前代码已经做到：

- Artifact 默认 `UNVERIFIED`，Worker 通过 `ArtifactDraft` 交付结果。
- 已有通用 `Claim`、`VerificationOutcome` 和不可变 `VerificationRecord`。
- Worker metadata 中伪造的验证字段会被拒绝；content 中的同名业务字段只是不可信数据，不会改变外层验证状态。
- `TaskRunResult.success` 和 `GraphExecutionResult.succeeded` 只表示执行正常，不表示验收通过。
- passed/failed 必须携带执行证据；unknown 保持未验证。
- TaskGraph 全部节点成功不会再自动验证产物、晋升长期记忆或宣布 completed。

批次 10C 已补充下游输入所需验证状态、Validator Profile、证据访问授权以及验证证据和 Workspace/Artifact 哈希的新鲜度绑定。具体 build/test/API/CLI/browser Validator 仍需由各入口或插件在 Composition Root 显式注册。

核心原则是：

> 模型可以说“我认为已经修好”，Runtime 必须实际运行验证，才能记录“已经修好”。

## 13. 通用需求协议应该包含什么？

Core 的需求协议只描述通用 Coding 目标、证据、权限和验收，不包含网页或 UI 专属字段。建议拆成以下对象：

这些对象现已在 Core 中实现为 1.0 协议，不再只是规划名称。

### `RequirementEvidence`

描述输入证据的引用和来源，不负责解释业务含义：

```text
artifact_ref
modality
mime_type
size
content_hash
source
derived_from
access_classification
```

一张图片在 Core 中只是图片证据，不能仅因为输入包含图片就自动路由给 VLM 或认定它是页面设计稿。

### `CodingRequirement`

描述需要交付的 Coding 结果：

```text
requirement_id
schema_version
objective
deliverables
constraints
repository_scope
acceptance_criteria
evidence_refs
assumptions
open_questions
validator_profile_ref
extension_refs
```

`extension_refs` 用于引用插件协议，避免把所有场景字段塞进 Core。

### `AcceptanceCriterion`

把验收条件变成可追踪对象，并指向具体 Validator：

```text
criterion_id
description
validator_kind
required
expected_result
evidence_refs
```

Validator 使用命名空间，例如 `core:test`、`core:http` 或 `visionforge:visual`。

### `EvidenceGrant`

由 Runtime 声明某个 Role 为完成某项任务可以读取哪些证据、执行哪些转换。Agent 只能看到获授权的 Artifact View，不能通过模型输出扩大仓库范围或访问边界。

## 14. 为什么有单独的 UI Spec？它是否属于 VisionForge？

是。UI Spec 是 VisionForge 的场景协议，不应成为 Harness Core 的永久组成部分。

合理边界是：

```text
Core RequirementEvidence
        ↓
VisionForge 视觉感知 Worker
        ↓
visionforge:ui_spec@1 Artifact
        ↓
VisionForge Developer / Browser Tester / Visual Reviewer
```

Core 只需要知道它是一个有类型、有版本、有来源和权限的 Artifact，负责保存、路由、授权和验证状态；字段中的页面布局、组件、样式和视觉评分由 VisionForge 插件解释。

批次 10E 已将这些场景类型实际迁移为 `visionforge:ui_spec`、`visionforge:visual_review`、`visionforge:quality_gate` 等命名空间 Artifact，并将场景注册为 `visionforge:web_visual`。Web 只通过 PluginRegistry 获取场景，Core 没有增加 UI 字段或视觉判定逻辑。

因此：

- `RequirementEvidence`、`CodingRequirement` 和通用 Artifact 外壳属于 Core。
- `UISpec`、`VisualReview` 和视觉门禁属于 VisionForge。
- 非网页任务不会创建或依赖 UI Spec。
- 插件类型使用 `visionforge:*` 命名空间，防止业务协议再次进入 Core。

## 15. Role 是第一路由键时，如何支持同一 Role 的多个 Worker？

Role 和 Worker 解决的是不同问题：

- Role 定义职责、权限、记忆视图、审计规则和职责隔离。
- Worker 是完成该职责的一种具体实现，可以使用不同模型、工具、成本和运行环境。

例如 `implementer` Role 可以同时有 Qwen、DeepSeek、本地规则引擎等多个 Worker。每个 Worker 通过已实现的 `WorkerDescriptor` 声明：

```text
worker_id
role
capabilities
accepted_input_protocols
produced_output_protocols
policy_tags
priority / enabled
availability probe
```

Runtime 的选择顺序必须固定：

```text
Role
  → 必需能力
  → 输入/输出协议兼容性
  → 权限、风险、成本和隔离策略
  → 当前可用性
  → 稳定评分和 tie-break
```

Role 是第一道硬过滤，不允许因为当前 Role 没有 Worker，就偷偷改派另一个 Role。没有合格 Worker 时应返回结构化 `blocked/missing_capability`，并列出可配置模型、启用插件或请求人工输入等候选动作，由 Runtime 或用户决定下一步。

选择结果还应保存 Worker ID、满足的条件、淘汰候选及原因，便于复现和排查。Reviewer/Validator 也不能由同一个执行实例审批自己生成的产物。

批次 10D 已实现上述边界：`TaskSpec` 声明硬要求，Registry 输出不可变选择决定和候选淘汰原因，无合格实现时 Executor 将节点记为 `blocked`。Runtime 还会给接纳的 Artifact 写入 producer principal；Reviewer/Tester 自动排除输入产物的生产 principal，Validator 使用同一 principal 时只能得到 `unknown`，不能自证通过。旧 `register(role, worker)` 继续可用。

## 16. 上述讨论对后续实施顺序有什么影响？

已经调整。刚完成的 Core 插件边界不需要返工，但不能再直接从媒体输入或 VisionForge 迁移开始。固定顺序为：

1. **批次 10B：事实与验证权边界（已完成）**——已建立 Claim、三态验证结果、VerificationRecord，并确保 Worker 正常返回不等于验收通过。
2. **批次 10C：通用需求与验收协议（已完成）**——已建立 RequirementEvidence、CodingRequirement、AcceptanceCriterion、EvidenceGrant、ValidatorProfile 和 Runtime Profile Runner。
3. **批次 10D：Role 优先的多 Worker 路由（已完成）**——同 Role 多实现，按能力、协议、策略、职责隔离和可用性确定性选择。
4. **批次 10E：VisionForge 插件适配（已完成）**——UI Spec 和视觉验收留在插件，通过 `visionforge:web_visual` 接入 Core。
5. **批次 11A—11D（已完成），11E（预检完成后暂缓）**——已实现受控 Core Validator、三类固定任务、统一校准报告、三方案脚本化 dry-run，以及供应商无关 ModelClient Worker、全局预算和调用前披露审计；真实对照因未获明确外部调用授权而未执行。

除非用户明确改变方向，否则不得跳过当前 Core 批次去扩展 VisionForge、媒体、复杂调度或记忆。这一顺序以 `HANDOFF.md` 和 `OPTIMIZATION_BACKLOG.md` 为准。

## 17. Core Validator 如何避免模型自己挑命令、伪造通过或偷看隐藏测试？

模型不拥有这些权力。Composition Root 预先冻结 Validator Profile，并为每个 build/test/CLI Validator 登记完整命令和参数。模型只能提交候选代码；它不能新增命令、删除隐藏测试、改变期望退出码，也不能把自己的文本结论写成 `passed`。

Runtime 会在独立 Workspace 中用 `shell=False` 执行白名单 argv。命令成功只是一个原始观察：只有退出码、固定输出断言和全部必需 Validator 都满足后，Profile gate 才会登记通过。工具不存在、命令超时或证据不完整是 `unknown`，不会被当作成功；明确测试失败才是 `failed`。

隐藏测试也不复制到 Agent 的可写工作区。Runtime 会先复制候选仓库到一个私有验证副本，再把隐藏检查注入该副本并执行。返回给流程的是裁剪、脱敏后的运行证据，不是隐藏测试源码。当前首个任务还让隐藏脚本只输出“通过/失败”的通用摘要，防止具体边界值通过 traceback 泄漏。

批次 11A 已实现上述最小链路，批次 11B 已扩展不同任务类型并建立统一离线报告，批次 11C 已用脚本化 Worker 验证三方案实验边界，批次 11D 已用 Fake Model 完成真实模型适配和调用前审计，但尚未运行真实对照。

## 18. 固定任务里保存参考答案，会不会让 Agent 偷看到答案？

参考答案是评测开发资产，用来证明题目本身“确实可解”，不是 Agent 输入。批次 11B 的离线校准会分别创建两个全新 Workspace：starter Workspace 只复制公开初始代码；参考修复 Workspace 才由 Runtime 的专用校准分支应用 solution。

真实 Agent 试验必须只获得 starter 的 Artifact、需求和被授权证据。solution 目录不进入 ProjectWorkspace，不写入 RequirementEvidence，也不出现在 Worker 的 Artifact 引用或日志中。隐藏检查同样只注入另一个 Runtime 私有验证副本。

校准标准不是“参考答案通过”这一条，而是同时要求：starter 明确失败、参考修复明确通过。这样能发现两类坏题：starter 本来就能通过的“空门禁”，以及参考实现也无法通过的错误或不稳定门禁。JSON 报告只保存结果、耗时、Validator 摘要和 Workspace 哈希，不保存参考答案或隐藏测试源码。

## 19. 脚本化 dry-run 里多 Agent 通过率更高，能证明多 Agent 更好吗？

不能。dry-run 的 Worker 行为是为了覆盖实验分支而预先安排的：单 Agent 故意提交 no-op，Planner + Developer 提交参考修复，完整方案先提交 no-op，再由 Tester/Fixer 修复。因此 0/3、3/3、3/3 是测试数据，不是模型能力数据。

这一步能证明的是实验装置没有串线：三种方案使用同一任务、Validator 和预算；单 Agent 与普通 Developer 看不到验证反馈；Tester 只能看到结构化失败摘要；Fixer 修复后会重新跑完整门禁；隐藏测试和参考答案没有进入 Worker 请求；调用、Token、修复轮数和越权指标能够正确计算。

只有接入同一组真实模型配置、冻结 Prompt 和随机性、重复运行保留任务，并报告失败和置信区间后，才能比较协作方案。真实实验也必须明确标记模型、调用预算、源码外发范围和报告版本，不能把脚本用量混入真实 Token。

## 20. 接入 ModelClient 后，模型能不能伪造通过或偷偷获得更多源码？

不能直接做到。批次 11D 的模型 Worker 把模型权力限制为三类候选输出：Plan、Patch 和 Diagnosis。它们都有严格的 1.0 JSON Schema，本地解析器会拒绝未知字段，所以 Tester 即使返回 `passed=true` 也无法形成合法 Artifact。Patch 只是一份候选 `ImplementationPlan`，仍要经过路径权限、合并和冻结 Validator；模型文本不会改变 Runtime 的三态结论。

每次调用前，Worker 会先在本地生成披露结果：只包含该 Role 当前获准读取的 Artifact；源码按稳定顺序和字符上限裁剪；`.env`、隐藏测试、参考答案、Git 和 Runtime 私有目录会被拒绝。审计记录文件名、SHA-256、字符数和截断状态，不复制源码正文。相同输入、Prompt 和 Schema 会得到相同请求哈希，便于确认后续失败究竟来自输入变化、Prompt 变化还是模型变化。

能力不足也不会自动换成错误的 Role。Planner/Tester 至少要求 text + structured output，Implementer/Fixer 还要求 tool calling；检查发生在模型客户端调用之前。缺少能力时保持明确失败，由调用方配置合适 Worker 或请求人工处理。

目前这些边界已用 Fake Model 和真实本地隐藏测试验证，但还没有运行新的真实 Core 消融。下一批需要先建立全局调用/Token 硬上限、冻结模型参数并展示源码外发摘要，获得用户明确授权后才访问供应商 API。

## 21. 为什么真实 Core 消融是 15—21 次调用，如何确保不会超限？

每个任务的单 Agent 需要 1 次调用，Planner + Developer 需要 2 次，完整方案首次实现通过时需要 2 次；如果失败，再调用 Tester 和 Fixer，共 4 次。因此每题最少 `1 + 2 + 2 = 5` 次，最多 `1 + 2 + 4 = 7` 次；3 题就是 15—21 次。

真实实验固定关闭 HTTP 自动重试，所以 21 次逻辑调用最多就是 21 次外部请求。四个 Role 共用一个 Core 预算器，网络或解析失败也会占用调用次数并扣完整 Token 预留，不能通过失败重试绕过限制。每次调用前必须预留 30,000 Token，全局最多 accounted 300,000 Token；供应商没有返回 usage 时也不会按 0 计算。

CLI 默认只做 preflight，不读取 `.env`。真实执行必须同时提供显式真实调用开关和当前 preflight SHA-256；模型、源码范围或预算有任何变化，摘要都会改变，旧授权就不能继续使用。

## 22. 通用 Coding Harness 接受图片后，会不会又变成网页 Agent？

不会。Core 图片节点不输出 UI Spec，也不判断页面好不好看。它处理的是任何 Coding 相关图片证据，例如错误截图、接口规格截图、架构图或流程图，并统一输出 `core:image_observation`。

图片只交给具备 VISION 能力且获得 `vision:inspect` 授权的感知 Worker。它把直接可见内容写成 observation，把需要推测的内容写成带 uncertainty 的 inference；之后普通 Planner 和 Coding Agent 只读取这些结构化 Claim。UI Spec 仍只存在于 VisionForge 插件。

图片感知准确率也不使用抽象审美评分，而是和固定的可见事实集合比较 precision、recall 和 F1。最终代码仍跑与文本需求相同的 build/test/CLI 或其他固定 Validator；图片和文本不会拥有两套验收标准。

## 23. 音频需求为什么不直接交给 LLM，转录错了怎么办？

音频包含两类不同工作：先听清楚说了什么，再理解需求并修改代码。前一步需要语音转录能力，后一步需要文本推理和 Coding 能力。把两步混在一个 LLM Agent 中，会让错误来源难以定位，也会让多个 Agent 重复上传和处理同一段音频。

Core 因此使用独立的供应商无关 `TranscriptionClient`。原音频只进入获授权的转录 Worker 一次，输出 `core:audio_transcript`；普通 Planner、Developer 和 Fixer 只读取结构化转录。以后可以替换本地 Whisper、云语音 API 或其他实现，不需要修改 TaskGraph、Role、Artifact 或 Validator。

转录不会直接升级为“事实”：

- 每个片段保存起止毫秒，并引用原音频 Artifact；
- 听不清的片段必须明确标记不确定原因，无法转录的区间单独保存；
- 转录 Artifact 默认是 unverified，不能自己写 `passed` 或改变验收标准；
- 固定转录样本用 precision、recall 和 F1 衡量漏报与幻觉；
- 代码是否完成仍由原来的 build/test/CLI/隐藏测试决定，而不是由转录客户端判断。

如果转录错了，调用链能区分是原音频质量、具体时间段、转录供应商还是后续需求分析的问题。Runtime 可以只重跑转录节点或让人工修正 Transcript，而不用重新执行整条 Coding 链路。本批只实现 Fake Client 和协议测试，尚未选择或调用真实语音服务。

## 24. 录屏里的“预期”和“复现步骤”为什么不能直接当事实？

录屏通常只能可靠证明“画面中发生了什么”。它不一定能证明产品本来应该怎样，也不能证明把画面中的动作重新执行一次就一定能复现问题。例如用户点击按钮后出现错误，这是可观察事件；“按钮本应成功提交”可能来自旁白、页面提示，也可能只是模型的常识推测。

因此视频协议把信息分成三类：

- `observation`：录像中看见或听见的用户操作、系统响应、错误和旁白，必须带时间范围并引用原视频；
- `inference`：预期与实际差异，必须说明预期来自画面、旁白还是模型推测；模型推测必须标注 uncertainty；
- `proposal`：从时间线整理出的候选复现步骤，仍需要 Tester 真正执行才能确认。

只有 Runtime 执行候选步骤并观察到相同失败，才能产生“已复现”的验证证据；修复后也必须重新运行固定回归。视频模型输出的 `passed` 或“问题已修复”不会改变 Artifact 验证状态。

本批没有控制或录制用户屏幕。测试只构造一小段本地 MP4 字节并交给 Fake Client，因此不需要 macOS 录屏权限。将来如果产品真的需要主动录屏，应作为单独工具能力显式申请权限，不能由视频理解 Worker 自行开启。

## 25. 为什么还需要 Evidence Bundle，直接把所有结果交给 Planner 不行吗？

直接把不同媒体结果拼进 Prompt，会出现三个问题：Planner 难以确认是否漏掉了某个输入；某个感知节点失败时容易被静默忽略；多个 Agent 可能再次读取原媒体，造成重复调用和费用。

`core:evidence_bundle` 相当于 Runtime 生成的“证据清单”。每条输入都必须有一条记录：

- `ready`：已生成结构化 Artifact 和引用原证据的 Claim；
- `blocked`：缺少授权、能力或合格 Worker；
- `failed`：证据损坏、协议错误或处理失败。

只有所有必需条目都是 `ready`，普通 Planner 才会收到 Bundle。任何条目未就绪时，其他独立媒体仍可完成，便于保留诊断证据，但 Planner 不会忽略失败项继续编写代码。

Bundle 也没有事实或验收权。它只能汇总 observation、inference 和 proposal，并保持 unverified；代码最终是否正确，仍由固定 Validator 判断。当前实现还禁止同一原始 Artifact 在 Intake Plan 中重复出现，从结构上避免重复上传或重复计费。

## 架构图命名修正建议

后续重新绘制架构图时建议同步修正以下名称：

- 将 `Evaluation` 从普通产品入口移到“评测与开发入口”。
- 将概念性的 `ScenarioSpec` 改为真实的 `ScenarioProfile → ScenarioRoundPlan`。
- 将 `VisionForge Worker` 改为 `VisionForge Worker Group`，明确它是一组 Worker。
- 将“视觉质量门禁”改为 `VisionForge Quality Gate`，因为它同时检查构建、功能、浏览器运行和视觉质量。
- 将 Harness 中的数字标注为“职责与调用关系”，避免被误认为四个 Agent 的线性步骤。
- 将 LLM/VLM 保持在公共模型能力层，由 Generic Coding 和 VisionForge Worker 按需调用。
