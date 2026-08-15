# VisionForge 多模态开发与视觉验收方案（Plan08）

## 日期

2026-08-15

## 讨论主题

在现有 Coding Multi-Agent Harness 基础上，将产品方向聚焦为 VisionForge：使用 LLM/VLM、受控浏览器和确定性 Runtime 完成参考图驱动的 Vue 页面生成、功能验收、视觉审查、自动修复和可重复评测。

## 目标与背景

通用 Coding Harness 已具备 DAG、Artifact、权限、生命周期、Checkpoint 和记忆基础，但缺少一个边界清晰、能够展示 Harness 价值的垂直业务闭环。仅生成代码或展示聊天页面无法回答页面是否可运行、交互是否正确、视觉是否接近参考图、失败如何修复以及效果如何评测。目标是复用现有控制面，建立一个模型负责理解和生成、Runtime 负责执行和裁决的多模态 MVP。

## 候选方案对比

| 方案 | 核心思路 | 优点 | 缺点与成本 | 风险 | 适用条件 |
|---|---|---|---|---|---|
| 继续扩展通用 Coding Harness | 优先完善通用记忆、调度、工具和后端生成 | 基础设施覆盖面广 | 产品价值不直观，难以建立统一成功指标 | 长期停留在框架开发 | 通用平台研究 |
| 一次性 LLM 页面生成 | 需求直接生成页面代码，不做浏览器或视觉反馈 | 成本低、实现快 | 只能证明生成，不能证明交付质量 | 功能和视觉假成功 | 演示性原型 |
| LLM + Browser Tester | 生成后运行构建、DOM 和交互检查并反馈修复 | 功能可验证，反馈确定 | 无法评价视觉接近度 | 页面能用但视觉偏差大 | 功能优先页面任务 |
| LLM + Browser + VLM | 参考图生成 UI Spec，浏览器验收，VLM 审查并驱动 Fixer | 形成完整多模态闭环，效果可量化 | 模型调用和浏览器成本更高，协议与评测复杂 | VLM 判断偏差、外部调用费用 | VisionForge MVP，已选择 |

## 最终选择

选择 VisionForge 纵向闭环，并保留三种方案作为统一评测对照：`llm_once`、`llm_browser_feedback` 和 `llm_browser_vlm`。固定 Vue 3 模板、viewport、任务集、断言、修复上限和质量门禁；DeepSeek 文本模型负责 Developer/Fixer，Qwen 视觉模型负责 Requirement Analyst/Visual Reviewer。模型输出始终是不可信输入，Runtime 独占路径、命令、浏览器、Artifact 状态和最终 completed 判定。

## 选择理由

- 页面任务能够同时验证代码生成、工具调用、视觉理解、反馈修复和收敛控制。
- 现有 Artifact、PatchIntegrator、Workspace、SQLite Runtime 和生命周期可以直接复用。
- 三方案对照可以区分浏览器反馈与 VLM 视觉反馈的实际增益，而不是凭主观截图判断。
- 固定任务、模板和 Runtime 验收可以防止模型改写评测标准。
- 文本与视觉模型按角色路由保持供应商和能力解耦，也便于分别核算 Token、耗时和失败。

放弃只做一次性页面生成，是因为无法验证交付质量；暂缓继续扩展通用平台，是因为在垂直闭环跑通前增加向量记忆、自由聊天或复杂后端会分散验证目标。

## 架构或流程

```text
参考图 + 页面需求
  → Requirement Analyst（VLM）生成版本化 UI Spec
  → Developer（LLM）生成受限 ImplementationPlan
  → PatchIntegrator + ProjectWorkspace 安全应用
  → Vue build + Playwright DOM/交互/控制台/网络检查
  → Visual Reviewer（VLM）对比参考图和实际截图
  → Runtime 组合质量门禁
      ├─ 通过：Artifact verified，Run completed
      └─ 失败：Fixer 使用允许的结构化反馈生成局部 Patch
                → 最多两轮 → 完整质量门禁

评测：固定 3 个页面 × 3 种方案 × 固定模型/Prompt/viewport/修复上限
```

## 执行步骤

1. 建立 UI Spec、Visual Review 和模型能力协议。
2. 建立 PNG/JPEG 内容寻址资产存储和固定 Vue 模板。
3. 实现受控构建、开发服务器、Playwright 交互与截图 Artifact。
4. 接入 Analyst、Developer、Reviewer 和一次性 VisionForgeRunner。
5. 增加 Runtime 组合质量门禁、视觉 Fixer、两轮上限与 Artifact 替代状态。
6. 使用 SQLite 保存返工阶段、Artifact、轮次和 Workspace 哈希并支持安全恢复。
7. 增加 Web 图片上传、任务 API、双图和 Artifact 调用链展示。
8. 建立固定页面任务集、参考图渲染器和三方案评测报告。
9. 配置 DeepSeek/Qwen 独立路由，先做经授权的最小真实能力烟测。
10. 在预算门禁下执行小规模真实基线，保留全部失败证据并进行人工校准。

## 约束与风险

- 外部模型调用产生费用，真实运行必须显式确认预算和停止条件。
- 参考图属于外发数据，未经明确授权不得发送到视觉供应商。
- 模型的 `passed=true` 不能绕过构建、功能、网络、控制台和视觉阈值门禁。
- 固定 Vue 模板目前使用单一浏览器端口，Web Runtime 串行执行。
- 浏览器二进制需要 Playwright Chromium 或 Runtime 显式配置的 Chrome/Chromium。
- Web 任务列表仍在内存中，服务重启后不能查询旧任务。
- 3 个页面只适合 MVP 校准，不能产生普遍统计结论。
- 首次真实基线因协议缺陷和供应商连接拒绝未形成可比较业务指标，不能宣称效果提升。
- 失败报告必须保留，重新运行使用新 Run ID，不得覆盖或手工修饰结果。

## 待验证事项

- 三种方案在构建、功能、视觉、首次通过率、修复率、Token 和耗时上的真实差异。
- VLM 评分、P1/P2 判断与人工盲审的一致性。
- 校准后的嵌套布局协议能否减少 Analyst 输出验证失败。
- 供应商连接稳定性和重试策略是否影响实验可比性。
- 两轮 Fixer 上限是否足够，以及视觉反馈是否会破坏已通过功能。
- 固定三任务是否需要扩展后再用于产品判断。

## 待办事项

- [ ] 用户重新确认额外模型调用预算后，用新 Run ID 重跑真实基线。
- [ ] 保留并引用首次失败报告，不覆盖历史证据。
- [ ] 对少量输出执行人工盲审并校准视觉阈值和问题级别。
- [ ] 汇总三方案的成功率、修复率、Token、耗时和人工介入。
- [ ] 增加 Web 任务持久化、并发端口隔离和运行中取消。
- [ ] 在 MVP 结果明确前继续暂缓无关的通用平台扩展。
