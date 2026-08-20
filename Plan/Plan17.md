# Plan17：多类型固定任务与离线校准报告

## 目标

批次 11B 将首个函数 Bug 夹具扩展为一组仍然容易客观判断的 Core Coding 任务，并建立统一的离线复位、自校准和 JSON 报告。该报告验证的是题目与 Runtime，不评价模型能力。

本批不调用真实模型、不运行浏览器，也不开始 Agent 消融实验。

## 任务组成

### `python-tax-rounding`

单文件十进制舍入 Bug，继续验证 build、公开测试和 Runtime 私有边界。

### `python-user-payload`

模拟 API 输入层的纯函数契约。需求明确 payload 类型、允许字段、email 与 age 规则；公开测试覆盖基本成功和必填字段，隐藏检查覆盖非对象、空白字符串、bool、上界和未知字段。

### `python-inventory-cli`

跨 `inventory/pricing.py` 与 `inventory/cli.py` 的行为任务。除 build 和测试外，`core:cli` 还直接验证折扣后的 stdout、非法输入的退出码和 stderr，因此不能只让函数测试通过。

## 自校准规则

每次离线校准都从冻结 starter 重新复制 Workspace，不复用上一次执行结果。每题运行两次：

1. starter 必须得到 `failed`；如果它通过，说明题目没有真正捕获缺陷。
2. Runtime 专用参考修复必须得到 `passed`；如果失败或 unknown，说明题目、环境或 Validator 有问题。
3. 只有两条同时成立，该题的 `calibration_by_task` 才为 true。

参考修复位于评测资产中，只能由 `FixedCodingEvaluationRunner` 的校准分支应用；后续真实 Agent 试验不得把 solution 路径、源码或 Artifact 引用授予 Worker。

## 报告边界

报告协议版本为 1.0，记录：

- suite ID 与 manifest SHA-256；
- 每次 trial 的 task、revision、三态 outcome、delivery、耗时和越权次数；
- 每个 build/test/CLI Validator 的结果、摘要和证据数量；
- 绑定验证副本的 Workspace 摘要；
- starter/参考修复交付率和逐题校准结论。

报告不保存隐藏测试源码、具体隐藏输入、临时 Workspace 路径或失效 Artifact 引用。写入使用同目录临时文件和原子替换。

## 下一批

批次 11C 建立三种 Agent 协作方案的统一 trial 协议、反馈可见性和预算边界，先用脚本化 Worker 做离线 dry-run。真实供应商调用需要另行明确授权，不与框架正确性测试混在一起。
