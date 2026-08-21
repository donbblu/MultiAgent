# Plan24：统一多模态 Intake

## 目标

批次 13A 将此前独立的文本、图片、音频和视频证据链组合成一个通用入口。同一个 `CodingRequirement` 可以引用一种或多种模态；Runtime 确保每条原始 Evidence 最多处理一次，将结果汇总为 `core:evidence_bundle`，再交给普通文本 Planner。

这不是新业务场景，也没有把多模态逻辑放进所有 Agent。媒体只出现在各自受控感知节点，后续 Planner 和 Coding Agent 只读取统一 Bundle。

## 执行结构

```text
CodingRequirement + text/image/audio/video Evidence
                         ↓ Runtime preflight
        ┌────────────────┼────────────────┐
        ↓                ↓                ↓
 Image Worker      Audio Worker      Video Worker
        └────────────────┼────────────────┘
                         ↓
         Runtime 生成 core:evidence_bundle
                         ↓ 全部条目 ready 才继续
                普通文本 Planner
                         ↓
                  后续 Coding DAG
```

图片、音频和视频节点没有依赖关系，由现有 `TaskGraphExecutor` 并发调度。文本不需要模型转换，由 Runtime 在核对授权、MIME、大小和 SHA-256 后转换成“用户陈述” observation Claim。

## Bundle 协议

每个 `EvidenceBundleEntry` 保存：

- 原始 Evidence Artifact 引用和模态；
- `ready`、`blocked` 或 `failed` 状态；
- 目标结构化协议和派生 Artifact 引用；
- 引用原始 Evidence 的 Claim；
- 未就绪时的受控失败摘要。

`ready` 条目必须有派生 Artifact 和 Claim，不能携带失败原因；未就绪条目不能夹带伪造的派生产物或 Claim。Bundle 的整体 `ready` 只能由所有条目的状态计算，不能由 Worker 或模型指定。

图片条目使用 `core:image_observation`，音频使用 `core:audio_transcript`，视频使用 `core:video_bug_evidence`，文本保留 `core:requirement_text`。Bundle 不解释 UI Spec 或其他插件业务协议。

## 失败关闭

Runtime 先逐条检查 Evidence 是否属于当前任务、是否完整、是否与 CodingRequirement 一致，以及 Task/Role/Artifact 绑定的操作授权。只有通过预检的媒体才进入并行 DAG。

媒体 Worker 缺失、能力不足或执行失败时，其他独立媒体仍可完成，Runtime 随后生成包含各自状态的 Bundle，便于定位问题。但只要有一条必需 Evidence 不是 `ready`，文本 Planner 就不会被调用，不能静默丢弃失败输入后继续编码。

成功时，Runtime 将 Bundle 引用加入 CodingRequirement 的 `extension_refs`，生成只允许 Planner 读取该内部派生产物的临时授权。Planner 请求中只有 `evidence_bundle`，没有原始图片、音频或视频 Payload。

## 与 Role 和 Worker 的关系

所有感知与需求分析仍使用 `planner` Role。Registry 先按 Role 过滤，再根据以下硬条件选择不同 Worker：

- 图片：`vision_understanding` + 图片协议 + `multimodal`；
- 音频：`audio_transcription` + 音频协议 + `multimodal`；
- 视频：`video_temporal_understanding` + 视频协议 + `multimodal`；
- Bundle Planner：`task_planning` + Bundle 协议 + `text`。

同一原始 Artifact 在 Intake Plan 中不能重复绑定，防止多个节点重复上传或重复计费。

## 验证证据

- 新增 7 项测试，覆盖四模态混合输入、三媒体并行屏障、每个客户端单次调用、来源追踪、下游输入隔离、缺失 Worker、文本篡改、状态防伪、纯文本路径、重复/遗漏 Evidence 和相同隐藏 Validator。
- 13A 专项测试 7 项通过。
- 完整默认回归 213 项通过，4 项真实浏览器类跳过。
- Python 编译和差异检查通过。
- 没有读取 `.env`、访问网络、调用真实媒体服务或请求系统录屏权限。

## 下一步

当前已规划的 Core 多模态 MVP 批次全部完成。下一步不是自动增加新功能，而是进行里程碑验收：检查协议边界、失败证据、测试覆盖和工作区差异，然后由用户决定提交/推送，或另行规划真实供应商适配、产品入口和固定多模态评测。暂缓的真实 Core 消融仍需要新的明确授权。
