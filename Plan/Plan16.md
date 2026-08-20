# Plan16：受控 Core Validator 与首个固定离线任务

## 目标

批次 11A 只建立最小确定性纵向链路：Runtime 用冻结的 Validator Profile 选择 build/test/CLI 实现，在白名单 Workspace 内执行完整 argv，并用首个固定 Coding 任务证明失败和通过都由执行证据决定。

本批不调用模型、不进行 Agent 方案对照，也不把 VisionForge 的视觉门禁搬进 Core。

## 受控命令边界

- Composition Root 必须为每个 Validator kind 注册完整 argv 白名单；模型不能临时增加命令、参数或 Validator。
- 子进程使用 `shell=False`、固定 Workspace、关闭 stdin、清理后的最小环境和独立进程组。
- timeout 到达后终止整个进程组；超时、工具不存在或 Runtime 无法执行都返回 `unknown`，不能伪装成通过或确定失败。
- 非预期退出码、固定输出断言不满足或测试数为零返回 `failed`。
- Artifact 只保存命令、退出码、耗时、脱敏和头尾裁剪日志、原日志长度及摘要；确定性断言在进程原始输出上执行，避免裁剪造成误判。
- Profile gate 绑定验证副本的 Workspace 内容摘要；`passed` 证明不能脱离对应 Workspace 复用。

## 固定任务边界

首个任务是 Python 税额十进制舍入 Bug。它不是网页任务，也不依赖审美评分：

1. Agent 工作区只包含 `starter/` 和公开测试。
2. Runtime 创建只读语义上的私有验证副本，再注入 `hidden/` 检查。
3. Agent 工作区出现 `.harness-hidden-tests`、符号链接或清单文件哈希漂移时安全拒绝。
4. starter 的公开测试通过但隐藏边界失败，因此最终 Profile 为 `failed`。
5. 参考答案仅用于离线自测，应用后 build、公开测试和隐藏检查全部通过，Profile 才为 `passed`。
6. 隐藏检查只输出通用成功/失败摘要，不把具体边界值写入证据 Artifact。

## 本批没有完成的内容

- 只有一个函数 Bug 夹具，尚未覆盖 API 校验、跨文件修改、CLI 行为或回归保护。
- 尚未建立整套任务复位、批量运行和指标 JSON 报告。
- 尚未接入单 Agent、Planner + Developer、Tester/Fixer 三种对照。
- `core:api`、浏览器等 Validator 仍应由后续 Core 实现或插件提供。

这些内容按批次 11B、11C 顺序继续，未经用户确认不提前执行。
