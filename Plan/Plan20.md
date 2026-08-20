# Plan20：Core Coding 真实消融预检与执行边界

## 当前状态

批次 11E 的零网络实现和自动验证已完成，真实供应商调用没有获得明确授权。用户随后要求进入下一批，因此 `CORE-ABLATION-001` 已暂缓；预检、预算器和执行入口全部保留，未来恢复时必须重新确认摘要是否仍匹配。

## 冻结实验

- Suite：`core-coding-eval-v1`，manifest SHA-256 `cea75c0ee1f8fafc4d4eebfabbe2ff8f18ee1f2624d3831e198cce984827ee91`。
- 三个任务：十进制舍入、API payload 校验、跨文件库存 CLI。
- 三种方案：单 Implementer、Planner + Developer、Planner + Developer + Tester + Fixer。
- 模型：DashScope `qwen3.7-plus`，temperature 0，structured output `json_object`。
- Prompt：`core-coding-ablation-1.0`；协议：Plan/Patch/Diagnosis 1.0。
- HTTP 自动重试：0。每个逻辑模型调用最多对应一个外部请求。
- 最少 15 次、最坏 21 次外部请求；单次输出上限 4,000 Token。
- 单次调用预留上限 30,000 Token；全局 accounted Token 上限 300,000。
- Preflight SHA-256：`a645b66f56a000f642b9447372d2fb4248792260f19f53675a97c0079cc87524`。

## 外发范围

会发送：

- 三个任务的自然语言需求；
- starter 中 8 个 Python/公开测试文件，共 2,548 个字符；
- 后续模型生成的 Plan、候选代码、Diagnosis；
- Runtime 裁剪后的通用 Validator 失败摘要。

不会发送：

- `.env`、API Key 或其他凭据；
- `.git`、`.runs`、`.runtime`、`.verification`；
- 三个隐藏验收脚本及具体隐藏输入；
- `solution` 中的参考答案。

预检报告只列文件路径、SHA-256、字符数和裁剪状态，不复制源码正文。每次实际请求还会产生独立请求哈希和实际披露清单。

## 预算边界

新增 Core `ModelCallBudget`，四个 Worker 共享同一预算：

1. 调用前检查外部请求次数。
2. 根据消息、Schema、协议开销和最大输出计算保守上界。
3. 调用前预留固定 30,000 Token；剩余预算不足时不会调用供应商。
4. HTTP/解析失败既占用一次请求次数，也按整笔预留计入 accounted Token，防止未知供应商用量绕过上限。
5. 供应商未返回 usage 时按整笔 30,000 Token 计入 accounted budget。
6. 配置必须实际发送 `max_tokens=4000`，否则真实实验拒绝启动。

## 授权绑定

CLI 默认只打印 preflight，不读取 `.env`。真实执行同时要求：

- `--confirm-real-calls`；
- `--confirm-preflight-sha256 a645b66f...`；
- 用户在当前任务明确同意上述供应商、源码范围、21 次请求和 300,000 Token 上限。

摘要或配置发生变化时旧授权自动失效，必须重新预检。

## 自动验证

- 新增 7 项预检、预算、无 usage、失败调用、重试关闭、输出上限和授权摘要测试。
- 完整默认回归 185 项通过，4 项真实浏览器类跳过。
- Python 编译和 `git diff --check` 通过。
- 本阶段没有读取 `.env`、访问网络或调用真实模型。
