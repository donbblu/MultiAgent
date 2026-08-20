# Plan19：Core Coding 模型 Worker 与调用前审计

## 目标

批次 11D 把供应商无关 `ModelClient` 接到批次 11C 冻结的四个 Role，但不运行任何真实供应商模型。自动测试全部使用内存 Fake Model，验证协议、安全边界和 Runtime 裁决链路。

## Worker 与模型能力

| Role | Stage | 模型能力 | 输入 Artifact | 输出 Artifact |
|---|---|---|---|---|
| Planner | `plan` | text、structured_output | Requirement、Source Snapshot | `core:plan` |
| Implementer | `implement` | text、structured_output、tool_calling | Requirement、Source Snapshot、可选 Plan | `core:patch` |
| Tester | `diagnose` | text、structured_output | Requirement、Source Snapshot、Plan、Validator Feedback | `core:test_diagnosis` |
| Fixer | `fix` | text、structured_output、tool_calling | Requirement、Source Snapshot、Plan、Validator Feedback、Diagnosis | `core:patch` |

Role 仍是第一路由键。四个 Model Worker 使用 `model-eval` 策略标签和不同 principal，不能因为某个模型缺少能力而跨 Role 降级。

## 结构化协议

Plan、Patch 和 Diagnosis 使用 `1.0` JSON Schema，均禁止未知字段。本地解析器在模型返回后再次严格校验，不依赖供应商保证：

- Plan 的每个目标文件必须位于任务允许范围。
- Patch 只接受完整文件候选，拒绝绝对路径、父目录穿越、保留目录、`.env`、重复文件和未授权文件。
- Diagnosis 只能记录 evidence、hypothesis、recommended changes 和 uncertainty，没有 `passed`、`verified` 等验收字段。
- Patch 被解析成 `ImplementationPlan` 后仍必须经过 `PatchIntegrator`；最终 build/test/CLI 结果只由冻结 Validator Profile 裁决。

因此模型可以提出计划、补丁和诊断假设，但没有把推测变成事实或把任务标为通过的协议入口。

## 调用前披露与审计

`ModelAblationWorker.prepare()` 是零网络预检：

1. 检查 Stage、Role、必需 Artifact 和可见协议。
2. 只投影当前 Stage 获准读取的 Artifact。
3. 按稳定路径顺序裁剪源码，拒绝 `.env`、`.git`、隐藏测试、参考答案和 Runtime 私有目录。
4. 为 Artifact 和每个源码文件记录 SHA-256、原始字符数、披露字符数和是否截断；审计清单不保存源码正文。
5. 固定协议版本、Prompt 版本、JSON Schema 和请求 SHA-256。
6. 真正调用前再次检查客户端能力；缺少能力时不会调用 Fake/真实客户端。

响应 Artifact metadata 记录 provider、model、Token、延迟、Prompt/协议版本、请求哈希和源码披露清单。Runner 仍在每次调用前预留 Worker/Token 预算，真实模型用量必须显式开启，默认 dry-run 会拒绝登记真实模型调用。

## 验证证据

- Fake Model 纵向链路：Planner 计划 → Developer 首次候选失败 → Tester 诊断 → Fixer 修复 → Runtime 隐藏测试通过。
- 负向测试：能力缺失在调用前拒绝；Patch 越权拒绝；`.env`/隐藏路径拒绝披露；Diagnosis 伪造 `passed=true` 因未知字段拒绝；Registry 必须有四个精确 Role 和不同 principal。
- 完整默认回归：178 项通过，4 项真实浏览器类按默认配置跳过。
- 本批没有读取 `.env`、访问网络、调用真实模型、上传源码/图片或运行浏览器。

## 下一批

批次 11E 完成真实消融运行入口和全局预检：冻结供应商/模型/Prompt/温度等配置，为三任务三方案计算最坏 21 次模型调用，设置全局调用与 Token 硬上限，生成用户可审阅的源码披露摘要，并要求显式授权后才能访问网络。获得授权后才实际运行并保留所有失败记录；结果只作为这组固定小任务的实验数据，不泛化为产品结论。
