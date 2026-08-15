# VisionForge 固定页面评测

`v1/suite.json` 是第一版固定评测集。它包含 3 个页面任务：SaaS 注册落地页、数据分析仪表盘和电商商品详情页。

每个任务都固定以下输入和验收事实：

- 本地 HTML 参考源；`render-reference.mjs` 使用锁定的 Playwright/Chromium、统一 viewport、UTC、中文 locale、浅色模式和关闭动画渲染 PNG。
- 自然语言页面需求。
- Runtime 拥有的 UI Spec，其中的 `data-testid`、DOM 和交互断言不能由模型改写。
- 视觉通过阈值、最多修复轮数、模型、Prompt 和协议版本由一次 `EvaluationConfig` 固定。

任务集的 `content_sha256` 同时覆盖 manifest、参考 HTML 和验收 UI Spec。修改任何参考画面或断言都会产生新的评测指纹，应同时提升任务集版本。

## 三种方案

评测执行器必须按 `EvaluationVariant` 实现以下边界：

1. `llm_once`：只生成一次。浏览器和 VLM 仍在生成后评分，但反馈不能返回给模型。
2. `llm_browser_feedback`：允许 Fixer 读取构建、DOM、交互、控制台和页面错误；VLM 结果只用于最终评分，不能作为修复输入。
3. `llm_browser_vlm`：允许 Fixer 同时读取 Browser Run 和结构化 Visual Review，最多修复两轮。

三种方案的最终结果必须统一使用固定 DOM/交互断言及相同 VLM/视觉阈值评分。不能把某个方案自己的停止条件直接当作评测通过条件。

`VisionForgeEvaluator` 会按“重复次数 → 固定任务 → 三种方案”顺序执行，以避免固定端口冲突。`RuntimeEvaluationTrialExecutor` 为每次试验创建隔离的 Vue 项目副本，强制使用 Runtime 固定验收 Spec，并把完整 Artifact Bundle、页面截图和最终项目保存在 `.runs/visionforge-eval/<run-id>/trials/`。

`visionforge_eval_run.py` 默认只做安全预检，不调用模型：

```bash
python3 visionforge_eval_run.py
```

只有显式增加 `--confirm-real-calls` 才会执行真实基线。脚本同时执行调用次数和总 Token 停止条件；默认上限是 51 次调用、600000 Token，文本输出单次最多 12000 Token，视觉输出单次最多 8000 Token。Token 停止条件在每次响应后更新，因此最后一个已经发出的请求可能让观察值略微超过总阈值；需要绝对货币上限时还应在供应商控制台配置余额或用量告警。

## 指标

报告为版本化 JSON，记录每次试验和每种方案的汇总：

- 构建成功率
- DOM/交互通过率
- 视觉验收通过率
- 最终交付成功率
- 首次通过率
- 自动修复成功率
- 平均修复轮数
- 平均视觉评分
- 平均 Token 消耗
- 平均耗时
- 人工介入次数

执行器异常不会丢失试验：Evaluator 会记录一个 `failed` 结果，并将人工介入次数加一。

构建失败也会转为结构化 Browser Run 证据，并附带 Runtime 生成的空白截图，使浏览器反馈方案可以把编译错误交给 Fixer，而不是直接丢失整个试验。

## 验证

默认契约与汇总测试：

```bash
cd demo
python3 -m unittest tests.test_visionforge_evaluation -q
```

真实参考图渲染需要设置 `VISIONFORGE_E2E=1`、`VISIONFORGE_NODE` 和 `VISIONFORGE_BROWSER_EXECUTABLE`。模型凭据不属于任务集或报告配置，不得写入评测产物。
