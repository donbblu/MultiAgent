# Core Coding 固定离线任务

这里保存 Core Harness 的确定性 Coding 评测夹具，不属于 VisionForge 场景。

当前 `v1` 提供三种非视觉任务：

- `python-tax-rounding`：单文件十进制舍入 Bug。
- `python-user-payload`：API payload 的类型、字段和数值边界校验。
- `python-inventory-cli`：跨 `pricing.py`/`cli.py` 的折扣计算、退出码和 CLI 输出行为。

每个 starter 的公开测试都能通过，但 Runtime 私有检查或 CLI 验收会失败；对应参考修复必须通过全部 Profile 门禁。

边界如下：

- `starter/` 是 Agent 可见的初始仓库。
- `hidden/` 只由 Runtime 复制到独立验证副本的 `.harness-hidden-tests/`，不进入 Agent 工作区。
- `solution/` 只用于证明 Harness 和任务夹具本身可解，真实 Agent 运行不能读取或应用它。
- `suite.json` 固定每个文件的 SHA-256、允许修改的路径和完整命令 argv；未登记文件、哈希漂移和命令漂移都会被拒绝。
- 最终结论来自 `ValidatorProfileRunner` 的 build/test/CLI 证据，不来自模型评分。

运行离线题目校准：

```bash
cd /Users/donbblu/codex/multiAgent/demo
python3 core_coding_eval_run.py
```

命令会为每个任务分别创建全新 starter 和参考修复 Workspace，并将版本化 JSON 报告写入忽略提交的 `.runs/core-coding-eval/calibration.json`。合格任务必须满足“starter 明确失败、参考修复明确通过”；两者结果相同或出现 unknown 都不能通过校准。

当前报告只校验题目和 Runtime，自带参考答案不会交给 Agent。三种 Agent 协作方案和真实模型对照属于后续批次。

运行三方案脚本化 dry-run：

```bash
cd /Users/donbblu/codex/multiAgent/demo
python3 core_coding_ablation_run.py
```

dry-run 使用 Role-first `WorkerRegistry`、Artifact 可见性策略、`PatchIntegrator` 和同一套固定 Validator，但 Worker 响应是预先编排的：单 Agent 故意不修复，Planner + Developer 直接提交参考修复，完整方案先失败再由 Tester/Fixer 修复。它只验证实验编排、反馈隔离、修复轮次和指标计算，不是三种方案的效果结论。

报告会同时记录 `scripted_calls/scripted_tokens` 和 `model_calls/model_tokens`。当前离线入口的真实模型用量必须为零；误注册返回模型用量的 Worker 会被安全拒绝。
