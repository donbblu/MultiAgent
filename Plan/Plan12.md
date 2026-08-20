# Core 事实与验证权边界（Plan12）

## 日期

2026-08-20

## 目标

模型和 Worker 只能提交候选陈述与产物，不能把自身判断写成 Runtime 事实。TaskGraph 节点全部执行成功只表示 Worker 正常返回；Artifact 验证状态、长期记忆晋升和场景 completed 必须来自 Runtime 接纳的验证证据。

## Core 协议

- `ClaimKind`：`observation`、`inference`、`proposal`。
- `Claim`：记录陈述、来源、证据引用、不确定性和时间；它本身永远不是验证证明。
- `VerificationOutcome`：只允许 `passed`、`failed`、`unknown`。
- `VerificationRecord`：记录 Validator 命名空间、验证对象、执行证据、结果、摘要和时间，并以 `verification://` 引用。

约束：

- observation 必须引用原始证据。
- inference 必须显式记录不确定性。
- passed/failed 必须有执行证据。
- 被验证 Artifact 不能同时作为自身的 passed/failed 证据。
- unknown 可以没有证据，但不能改变 Artifact 的 `UNVERIFIED` 状态。
- VerificationRecord 不可变并随 SQLite Runtime Snapshot 往返恢复。

## 验证权

Worker 只获得裁剪后的 `TaskRunRequest`，不获得共享 `ArtifactStore`。Worker 返回的 `ArtifactDraft` 永远由 Executor 以 `UNVERIFIED` 状态接纳，并拒绝 metadata 中的 `validation_state`、`artifact_validation` 和 `verification_refs` 等保留字段。正文仍可包含同名业务数据，但 Runtime 不读取它来改变外层验证状态，从而避免限制插件自己的 Schema。

`ArtifactStore.record_verification()` 在一次锁内完成以下检查后原子更新：

1. VerificationRecord 类型有效。
2. 全部 subject Artifact 存在且未被 supersede。
3. Artifact 类型的执行证据引用存在。
4. VerificationRecord ID 未重复。
5. passed 映射为 VERIFIED，failed 映射为 FAILED，unknown 映射为 UNVERIFIED；后续重新验证无法判断时不会保留旧的 VERIFIED 外观。

现有 Runtime/场景使用的 `mark_verified()` 和 `mark_failed()` 暂时保留兼容，但现在会创建并保存结构化 VerificationRecord；空证据的 passed/failed 会被协议拒绝。插件属于可信进程内代码，真正的不可信第三方插件隔离不在本批范围内。

完整回归发现 VisionForge 原先将 quality gate 同时列为 subject 和唯一 evidence，属于循环自证。为适配 Core 事实协议，现有三个兼容执行入口都改为：build/browser/review 作为 quality gate 的证据，quality gate 再作为其他周期产物的证据。该修正不迁移插件目录，也不改变视觉门禁规则。

## 执行成功与验收通过

- `TaskRunResult.success`：Worker 调用是否正常完成。
- `GraphExecutionResult.succeeded`：全部图节点是否正常完成。
- `GraphExecutionResult.acceptance_outcome`：默认 `unknown`。
- `GraphExecutionResult.accepted`：只有 outcome 为 passed 才为真。

`TaskGraphExecutor` 不再因为所有节点 succeeded 就自动：

- 把全部输出 Artifact 标成 VERIFIED；
- 将节点结果晋升长期记忆；
- 将 Lifecycle 标成 completed。

通用 `DagRunner` 和 `ScenarioRuntime` 原本就使用 `finalize_lifecycle=False`，并在外层执行质量门禁或场景收敛判断，因此这项收权与当前产品路径一致。

## 本批未做

- RequirementEvidence、CodingRequirement、AcceptanceCriterion、EvidenceGrant 和 ValidatorProfile 已在后续批次 10C 完成，见 `Plan/Plan13.md`。
- 没有改造一 Role 多 Worker；它属于批次 10D。
- 没有迁移 VisionForge；UI Spec 和视觉验证仍留在现有场景代码，插件适配属于批次 10E。
- 没有接入真实模型、媒体上传或新供应商。
- 验证证据新鲜度和 Workspace/Artifact 哈希绑定已在后续批次 10C 完成，见 `Plan/Plan13.md`。
