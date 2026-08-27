# CLI 本地执行可见性演示

本报告对应 `TRACE-20260827-125`。它只证明 CLI Composition Root 的默认拒绝、exact-bool 传递和用户可见数据投影；批准场景使用 pure mock，没有启动模型或真实进程。

## 用户现在能看到什么

| 场景 | 状态 | Spawn | 命令结果 | 可见结果 |
|---|---|---:|---:|---|
| 默认不带 `--trusted-local-execution` | `rejected_before_task` | `0`，来源为 CLI 前置拒绝 | 0 | 退出码 2；模型、Workspace 和本地进程均未启动 |
| 显式批准（pure mock） | `terminal` | 未直接计数，不冒充真实 spawn | 1 | Profile、exit、duration、cleanup 状态以同一 payload 输出 |

显式批准的 mock 投影为：

- Profile：`legacy_workspace_verify`
- Exit：`0`
- Duration：`12 ms`
- Cleanup：`terminal / verified=true`
- Fresh approver instances：`2`，均由 exact `True` 创建
- Opaque token exposed：`false`

## 自动验证

- 聚焦测试：`7/7` 通过，0 failure/error/skip，`0.003s`
- Python 3.9 `py_compile`：exit 0
- `git diff --check`：exit 0
- 跨 argv 的 `--token value`、`Bearer value`、inline secret 与 private-key command 均有脱敏反例
- 整个 JSON CLI stdout 可直接 `json.loads`；Markdown 与 JSON 来自同一 normalized payload

## 边界

本批没有运行模型、真实 target、signal、network 或 Browser。`terminal_execution_count=1` 只表示 mock DTO 中存在一份 Profile+cleanup 终态证据；它不被描述成实际 spawn 计数，也不构成 Runtime Acceptance 或 `KEEP`。
