# 多模态 Coding Multi-Agent MVP 方向修正（Plan09）

## 日期

2026-08-16

## 结论

项目核心保持为供应商无关的 Coding Multi-Agent Harness。文本、图片、音频和视频是需求与问题证据的输入形式，不限定系统只能开发网页。现有 VisionForge Vue/Playwright/VLM 闭环完整保留，但降为 `web_visual` 场景能力，不再代表第一个通用 MVP。

第一个 MVP 要回答的问题是：在现有代码仓库中，引入 Planner、Tester 和 Fixer 后，是否比单 Agent 更稳定地完成可验证的 Coding 任务。

## 核心 MVP

```text
Requirement Evidence（text/image，协议预留 audio/video）
  → Requirement Analyst：生成 Coding Requirement
  → Planner：生成有依赖关系的 TaskGraph
  → Developer：生成 ImplementationPlan/Patch
  → PatchIntegrator + ProjectWorkspace：安全应用
  → Validator：构建、固定测试、隐藏测试、权限和回归
  → Tester/Fixer：只根据结构化失败证据修复
  → Runtime：所有门禁通过后 completed
```

### MVP 包含

- 在受控的现有仓库中修复 Bug 或增加小型功能。
- 文本和图片需求证据；协议能够扩展音频和视频。
- Planner、Developer、Tester/Fixer 的可选组合。
- Patch 路径、命令、权限、测试和最终状态由 Runtime 控制。
- 失败、重试、Artifact、Token、耗时和人工介入可追踪。

### MVP 不包含

- 用单一视觉分数判断通用 Coding 任务是否完成。
- 自由聊天、多租户、向量数据库或复杂长期记忆调优。
- 同时完整支持所有语言、框架、音频和视频供应商。
- 依赖人工审美才能判断正确性的开放式生成任务。

## 客观完成条件

通用 Coding Task 只有在以下适用条件全部满足时才能完成：

1. Patch 只修改任务授权路径，且通过现有安全校验。
2. 项目构建或静态检查通过。
3. 公开测试和 Runtime 隐藏测试通过。
4. 任务声明的 API、CLI、文件或行为断言通过。
5. 原有回归测试没有新增失败。
6. 没有未解决的 P0/P1 确定性错误证据。
7. Runtime 接纳最终 Artifact；模型的自述不参与最终裁决。

视觉评分只能出现在显式声明为 `web_visual` 的 Validator Profile 中，不能成为其他任务的隐式门禁。

## 第一版固定任务集

任务规模保持小而确定，每个任务拥有可复位仓库、公开测试、隐藏测试、允许修改路径和固定超时。

| 任务 | 输入形式 | 目标 | 客观验收 |
|---|---|---|---|
| `python-tax-rounding` | 文本 | 修复边界值舍入 Bug | pytest 公开测试 + 隐藏边界测试 |
| `python-cache-expiry` | 文本 | 修复跨文件缓存过期逻辑 | 时间冻结测试 + 回归测试 |
| `node-api-validation` | 文本 | 增加 API 参数校验和错误码 | HTTP 契约测试 + 原接口回归 |
| `cli-output-format` | 文本 | 增加输出格式选项 | stdout/stderr/退出码断言 |
| `diagram-interface` | 图片 | 根据小型接口图补齐实现 | 编译 + 接口签名 + 行为测试 |
| `error-screenshot-fix` | 图片 | 根据错误截图和仓库定位异常 | 失败复现 + 回归测试 |

图片任务不以“图片理解分数”作为最终结果；图片只是需求证据，最终代码仍由同一套确定性测试裁决。音频和视频在后续批次使用与文本任务等价的隐藏测试接入，避免同时引入任务难度差异。

## Multi-Agent 对照

固定模型、Prompt、仓库、输入、Token 上限和重试上限，比较：

1. `single_agent_once`：单 Agent 一次实现，不接收测试反馈。
2. `planner_developer`：Planner 拆分后由 Developer 实现，最终统一测试，不反馈修复。
3. `planner_developer_test_fixer`：完整 TaskGraph、测试证据和有限修复闭环。

三种方案运行相同任务、相同隐藏测试和相同权限，不允许某个方案修改验收标准。

## 指标

- 构建成功率
- 公开测试通过率
- 隐藏测试通过率
- 最终交付成功率
- 首次通过率
- 自动修复成功率和平均修复轮数
- 回归失败率
- 越权 Patch 拒绝次数
- 任务拆分/依赖协议失败次数
- Token、模型调用次数和耗时
- 人工介入次数

这些指标都是事件、命令结果或断言结果，不依赖模型给自己打分。

## 场景扩展

核心 Runtime 通过 Validator Profile 选择验证器：

- `language_tests`：单元测试、集成测试、静态检查。
- `api_contract`：HTTP 请求、响应和持久化断言。
- `cli_contract`：输入、输出和退出码。
- `browser_functional`：DOM、交互、控制台和网络。
- `web_visual`：在浏览器功能通过后附加视觉专项审查。

VisionForge 当前实现映射到 `browser_functional + web_visual`。它可以继续演进，但不得把专项视觉结果外推为通用 Multi-Agent Coding 能力。

## 后续批次

1. 批次 10：实现通用 Requirement Evidence、Coding Requirement 和 Validator Profile 协议及测试。
2. 批次 11：建立固定本地 Coding 任务、隐藏验收和三方案离线评测器。
3. 批次 12：先接图片任务，再接音频转录和录屏证据；所有模态使用确定性代码结果验收。
4. 之后再决定是否恢复 `web_visual` 的独立视觉缺陷集和人工校准。

## 历史证据处理

- `baseline-20260815-01` 和 `baseline-20260815-02` 保留为探索性 `web_visual` 证据。
- 不覆盖、不删除，也不将其 0 交付率解释为通用 Coding Harness 的失败率。
- 在新的客观 Coding 基准就绪前，不继续消耗模型预算重跑开放式网页任务。
