# Core 通用需求与验收协议（Plan13）

## 日期

2026-08-20

## 目标

建立与 UI、供应商和输入模态无关的 Coding Requirement 协议。Runtime 冻结仓库权限、验收项和 Validator Profile；Role 只能读取明确授权的外部 Evidence；验证结果绑定 Artifact 内容和可选 Workspace 状态，旧证据不能被当成当前事实。

## 协议分层

### RequirementEvidence

只描述输入证据，不解释业务语义：

- `artifact_ref`
- `modality`：text/image/audio/video
- `mime_type`
- `size_bytes`
- `content_hash`
- `source`
- `derived_from`
- `access`

Runtime 会把描述符与实际 Artifact 的 MIME、大小和原始内容哈希核对。图片不会因为 modality 是 image 就自动成为 UI 设计稿，也不会自动触发 VLM。

### CodingRequirement

保存目标、交付物、约束、RepositoryScope、AcceptanceCriterion、Evidence 引用、假设、开放问题、ValidatorProfile 引用和插件扩展 Artifact 引用。协议版本固定为 1.0，并具有稳定 digest。

`TaskContext.coding_requirement` 可携带已接受的结构化需求；模型输入只能看到协议内容和 Artifact 引用，不能直接取得未授权原始证据。

### AcceptanceCriterion 与 ValidatorProfile

AcceptanceCriterion 使用命名空间 Validator kind，例如 `core:test`、`core:http` 或 `visionforge:visual`。ValidatorProfile 同时冻结：

- Validator ID、kind、配置和 required 状态；
- Criterion ID、描述、required、expected_result 和 Evidence 引用的摘要；
- 是否要求绑定 Workspace；
- `all_required` 完成策略。

只保留 Criterion ID 但降低阈值也会改变摘要并被拒绝。模型不能通过同步修改 CodingRequirement 和 Profile 绕过，因为 Profile 由 Runtime 作为独立输入注入。

### EvidenceGrant

授权与 task、Role、Artifact 引用、操作、目的和可选到期时间绑定。TaskGraphExecutor 在启用结构化 CodingRequirement 后强制要求外部 Evidence 具有匹配 Grant；缺失、跨 Role、跨任务、超范围或过期授权都会在调用 Worker 前被拒绝。

旧任务没有 `coding_requirement` 时继续兼容原执行方式；新协议一旦启用，就必须同时注入 ValidatorProfile、RequirementEvidence 和 EvidenceGrant。

## Runtime Validator

- `ValidatorRegistry`：Composition Root 显式注册可信 Validator，不允许模型注册或替换。
- `ValidatorProfileRunner`：严格按冻结 Profile 解析并执行 Validator。
- `ValidatorRunRequest/Result`：传递只读 subjects、配置和 Workspace 哈希；passed/failed 必须返回 Evidence ArtifactDraft。
- 缺失 Validator、缺少 Workspace hash、执行异常或错误返回类型都变成 `unknown`，不会回退到模型判断。
- 每次运行生成 `validator_profile_report` Artifact，保存所有 Validator 结果和原始证据引用。
- 只有组合门禁的 `core:profile_gate` VerificationRecord 改变 subject 的最终验证状态。

当前未预装具体 build/test/API/CLI/browser Validator；它们需要由 CLI、Web 或场景 Composition Root 使用现有安全命令、HTTP、浏览器工具逐步注册。通用 Runner 已通过 Fake Validator 验证选择和收敛语义。

## 新鲜度

- 每个 Artifact 提供稳定 `content_hash`。
- passed/failed/unknown VerificationRecord 都绑定 subject hash。
- ValidatorSpec 可要求绑定整个 Workspace hash 集合的稳定摘要。
- `ArtifactStore.is_verified()` 同时核对当前 subject 和 Workspace；任一变化都会使旧证明失效。
- `TaskSpec.required_verified_inputs` 可声明节点输入必须具有当前有效证明，Executor 在调用 Worker 前检查。
- 新一次 `unknown` 会把旧的 VERIFIED 外观降回 UNVERIFIED。
- Runtime Snapshot 持久化 VerificationRecord hash 和 `required_verified_inputs`，旧快照缺少新字段时按未绑定/空要求兼容恢复。

## 权限边界

RepositoryScope 采用保守子集判断：具体路径可以落在 Runtime glob 内；含 glob 的候选范围只有与 Runtime 声明完全相同或 Runtime 为 `**` 时才接受。不能删除 Runtime 的 prohibited actions。

这种策略可能拒绝一部分理论上安全但难以证明包含关系的 glob，优先保证模型不能扩大权限。更精确的 glob/符号级范围分析不在本批实现。

## 本批未做

- 没有改造同 Role 多 Worker 路由；它属于批次 10D。
- 没有迁移 VisionForge；UI Spec 和视觉协议仍属于插件边界。
- 没有实现音视频解析、OCR、VLM 自动选择或媒体上传。
- 没有调用真实模型、浏览器或网络。
- EvidenceGrant 当前由 Composition Root 注入，不持久化为跨进程授权凭据；恢复时缺少授权会安全拒绝而不是沿用旧权限。
