# Plan22：Core 音频需求证据链

## 目标

批次 12B 让音频需求以受控方式进入通用 Coding DAG。原音频只交给专用转录 Worker 一次，输出带时间戳、来源和不确定性的 `core:audio_transcript`；之后普通 Planner 和 Coding Agent 只读取结构化转录，不重复读取音频。

本批不接真实语音供应商，不读取 `.env`，也不把转录结果当作最终事实或代码验收结果。

## DAG 边界

```text
core:requirement_audio + RequirementEvidence + EvidenceGrant
                 ↓ planner / audio_transcription
           AudioTranscriptionWorker
                 ↓ core:audio_transcript
          普通文本 Planner / Coding DAG
                 ↓
        PatchIntegrator + 原固定 Validator
```

转录和文本规划继续使用同一个 `planner` Role。Role 确定职责和权限；Worker 再按能力、输入/输出协议和策略标签区分。转录 Worker 要求 `audio_transcription + multimodal`，文本 Planner 要求 `task_planning + text`。

## 供应商无关转录协议

音频不复用面向 LLM/VLM 的 `ModelClient`，而是使用独立 `TranscriptionClient`：

- 客户端声明 `transcription`、`timestamps` 和可选的 `language_detection` 能力；
- `TranscriptionRequest` 只包含 Artifact 引用、MIME、获授权音频字节和可选语言提示；
- `TranscriptionResponse` 返回供应商、模型、语言、音频时长、按时间排序的片段、无法转录区间和延迟；
- 每个片段必须有唯一 ID、起止毫秒、文本和清晰度；不确定片段必须明确原因；
- 时间段越界、倒序、重叠、字段扩展或未知返回类型都会被拒绝。

这条边界允许后续替换本地 Whisper、云语音 API 或其他实现，而不修改 TaskGraph、Role、Artifact 或 Validator。

## 权限与完整性

- 原音频必须已有 `RequirementEvidence`，并与 Artifact 的 MIME、大小和 SHA-256 一致。
- Executor 要求普通 `read`；转录 Worker 额外要求同一 Task/Role/Artifact 的 `audio:transcribe` 授权。
- Payload 由 Composition Root 注入 resolver；TaskGraph 和 SQLite 不保存原始音频字节。
- 调用客户端前核对任务归属、Artifact kind、modality、字节数、SHA-256，以及 WAV/MP3 文件签名。
- 当前最大音频大小默认为 25 MiB，仅支持 WAV 和 MP3；格式扩展必须显式修改策略和测试。

## 防止转录变成事实或验收

Runtime 将每个转录片段转换为 observation Claim，陈述中保留毫秒范围，并直接引用原音频 Artifact。片段的不确定说明会原样进入 Claim。`AudioTranscript` 会再次核对片段、Claim、时间范围、原音频引用和 uncertainty 的一一对应关系。

转录响应没有代码、命令、权限、AcceptanceCriterion、`passed` 或 `verified` 字段；严格映射会拒绝额外字段。输出 Artifact 默认保持 `UNVERIFIED`，只能作为后续需求分析的证据，不能改变 Validator Profile 或最终完成状态。

## 评测口径

- 转录准确性使用固定片段文本计算 precision、recall 和 F1；漏报降低 recall，多报不存在内容降低 precision。
- 音频输入与文本输入应用同一候选代码，并运行同一个 `python-tax-rounding` 隐藏 Validator。
- 最终通过条件仍是 Runtime 的代码测试结果，与输入模态、转录客户端和转录文字中的自我声明无关。

## 验证证据

- 新增 7 项测试，覆盖一次转录、下游只接收 Transcript、Artifact 追踪、授权、能力、Payload 完整性、文件签名、时间线、不确定性、额外字段拒绝和相同隐藏验收。
- 音频专项测试 7 项通过。
- 完整默认回归 199 项通过，4 项真实浏览器类跳过。
- Python 编译和差异检查通过。
- 没有读取 `.env`、访问网络、调用真实转录服务或上传音频。

## 下一批

批次 12C 实现视频/录屏 Evidence → 受控 Timeline/Bug Reproduction Artifact。保留原视频引用、关键时间点、观察到的操作、预期/实际差异及不确定项；下游 Coding DAG 只读取结构化时间线，并继续复用固定回归测试。默认使用 Fake 视频感知客户端和本地字节测试，不请求录屏权限、不访问网络。
