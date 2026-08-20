# Plan21：Core 图片需求证据链

## 目标

批次 12A 让图片可以作为通用 Coding Requirement 的输入证据，但不把网页、UI Spec 或 VisionForge 逻辑放进 Core。图片只在一个受控感知节点中出现一次，随后转换为结构化 Claim Artifact，普通 Planner、Implementer 和 Tester 不需要持续读取原图。

## DAG 边界

```text
core:requirement_image + RequirementEvidence + EvidenceGrant
                ↓ planner / vision_understanding
          ImagePerceptionWorker
                ↓ core:image_observation
        普通文本 Planner / Coding DAG
                ↓
       PatchIntegrator + 原固定 Validator
```

Role 仍是第一路由键。视觉感知和文本规划都属于 `planner` Role，但使用不同 WorkerDescriptor：前者要求 `vision_understanding`、图片输入协议和 `multimodal` 策略；后者要求 `task_planning`、Claim 输入协议和 `text` 策略。

## 权限与数据完整性

- 图片必须已有 `RequirementEvidence`，绑定 Artifact 引用、modality、MIME、字节数、SHA-256、来源和访问范围。
- Executor 先要求普通 `read` 授权；ImagePerceptionWorker 再要求同一 Task/Role/Artifact 的 `vision:inspect` 操作授权。
- Payload 由 Composition Root 注入的 resolver 获取，Core 不把 base64 或文件路径塞进 TaskGraph/SQLite。
- 调用模型前再次核对任务归属、Artifact kind、MIME、字节数、SHA-256 和 PNG/JPEG 签名；不一致时不调用模型。
- 本批不迁移或复用 VisionForge 私有图片存储，避免 Core 反向依赖插件。

## 防止推测变成事实

模型只允许输出：

- `observations`：图片中直接可见的陈述、区域和可见依据；
- `inferences`：基于可见内容的推测，必须同时说明依据和不确定性；
- `unreadable_regions`：无法可靠读取的区域。

Schema 不包含代码、文件路径、命令、权限、AcceptanceCriterion、`passed` 或 `verified`。Runtime 将输出转换为已有 `Claim`：observation 必须引用原始图片，inference 必须带 uncertainty。`core:image_observation` 默认保持 unverified，不能改变 Coding Requirement、Validator Profile 或最终完成状态。

## 评测口径

感知准确率使用固定可见陈述集合计算精确率、召回率和 F1；多报不存在内容会降低 precision，漏报会降低 recall，不使用抽象视觉审美评分。

代码交付仍使用对应文本任务原有的隐藏 Validator。自动测试让 text/image 两条输入路径应用同一候选修复，并确认得到完全相同的 Validator 集合和 passed 结果，证明输入模态没有改变验收标准。

## 验证证据

- 图片只进入视觉 Worker 一次，下游 Planner 只读取 `core:image_observation`。
- 同一 `planner` Role 的两个 Worker 能按能力、协议和策略正确选择。
- 缺少 `vision:inspect`、缺少 VISION 能力、Payload 大小/哈希错误均在模型调用前拒绝。
- 模型增加 `passed` 或 `acceptance_criteria` 字段会因严格 Schema 被拒绝。
- 精确率/召回率/F1 测试会同时惩罚遗漏和幻觉。
- text/image 路径复用 `python-tax-rounding` 的同一隐藏测试并得到相同结果。
- 完整默认回归 192 项通过，4 项真实浏览器类跳过；无 `.env`、网络、真实模型或媒体上传。

## 下一批

批次 12B 实现音频 Evidence 到受控 Transcript/Claim Artifact 的同类感知节点：保留原音频引用、转录片段时间范围和不确定项；下游 Coding DAG 只读取结构化转录，最终继续复用固定代码 Validator。默认只用 Fake 转录客户端和本地字节测试，不访问真实语音服务。
