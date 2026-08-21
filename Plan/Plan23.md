# Plan23：Core 视频 Bug 证据链

## 目标

批次 12C 让视频或录屏作为通用 Coding 问题证据进入 DAG。原视频只交给专用视频感知 Worker 一次，输出 `core:video_bug_evidence`；普通 Planner、Developer 和 Fixer 只读取结构化时间线，不重复读取视频。

视频证据不限定网页场景：它可以记录 CLI、桌面应用、移动端、接口调试工具或网页中的操作和错误。最终修复仍由对应项目的固定测试和回归门禁判断。

## DAG 边界

```text
core:requirement_video + RequirementEvidence + EvidenceGrant
                 ↓ planner / video_temporal_understanding
             VideoPerceptionWorker
                 ↓ core:video_bug_evidence
          普通文本 Planner / Coding DAG
                 ↓
        PatchIntegrator + 原固定 Validator
```

视频感知与文本规划都属于 `planner` Role，但通过能力、输入/输出协议和策略标签选择不同 Worker。没有合格视频 Worker 时保持 blocked，不跨 Role，也不把视频静默丢弃后继续执行。

## 供应商无关视频协议

`VideoPerceptionClient` 与供应商 SDK 解耦，声明 `video_understanding`、`timestamps` 和可选 `audio_track` 能力。响应包含：

- 按时间排序的可见或可听事件；
- 事件类型：用户操作、系统响应、可观察状态、错误信号或旁白；
- 候选复现步骤及其支持事件；
- 预期/实际差异及预期来源；
- 未审查时间区间；
- provider、model、时长和延迟。

当前 Core 只支持 MP4 和 WebM，默认最大 100 MiB。真实视频解码、抽帧、OCR 或供应商上传策略留给后续适配器，不进入 TaskGraph 和通用 Artifact 协议。

## 观察、推测与建议的分离

- 录像中实际看到或听到的事件转换为 observation Claim，并保留起止毫秒、事件类型、区域、原视频引用和不确定性。
- 预期与实际差异转换为 inference Claim。预期必须标明来自可见画面、旁白或模型推测；模型推测不得伪造事件来源且必须说明 uncertainty。
- 复现步骤转换为 proposal Claim，并引用支持它的视频事件。它只是待 Runtime/Tester 执行的候选步骤，不是已复现事实。
- 视频响应不能包含 `passed`、验收修改或完成状态；输出 Artifact 默认保持 `UNVERIFIED`。

这样即使视频模型误读操作，错误也停留在可追踪 Claim 中，不会直接变成测试通过或代码已修复的结论。

## 权限与完整性

- 原视频必须有 `RequirementEvidence`，并与 Artifact 的 modality、MIME、大小和 SHA-256 一致。
- Executor 要求 `read`；视频 Worker 额外要求同一 Task/Role/Artifact 的 `video:inspect` 授权。
- 调用客户端前检查任务归属、Artifact kind、最大大小、哈希和 MP4/WebM 文件签名。
- Payload 由 Composition Root 的 resolver 提供；本批不读取屏幕、不启动录屏，也不把视频字节写入 SQLite。

## 评测口径

- 固定事件描述集合用于计算 precision、recall 和 F1，漏报与多报分别降低 recall 和 precision。
- text/video 两条输入路径应用同一候选修复，并运行同一个 `python-tax-rounding` 隐藏 Validator。
- 候选复现步骤只有在后续确定性 Tester 真正执行并得到相同失败时，才能形成 Runtime 验证证据。本批不让模型自行声明“已复现”。

## 验证证据

- 新增 7 项测试，覆盖一次视频读取、下游输入隔离、Claim 类型、授权、能力、Payload 完整性、格式签名、时间线引用、推测边界、额外字段拒绝和同一隐藏回归。
- 视频专项测试 7 项通过。
- 完整默认回归 206 项通过，4 项真实浏览器类跳过。
- Python 编译和差异检查通过。
- 没有读取 `.env`、访问网络、调用真实视频服务、上传视频或请求 macOS 录屏权限。

## 下一批

批次 13A 建立统一多模态 Intake：同一个 CodingRequirement 可以同时引用 text/image/audio/video Evidence；Runtime 按模态并行选择受控感知 Worker，将结果汇总为来源可追踪的通用 Evidence Bundle，再交给普通文本 Planner。它不会引入新的业务场景，也不会改变各模态最终共用的 Validator。
