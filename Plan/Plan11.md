# Core 场景插件边界（Plan11）

## 日期

2026-08-20

## 决策

Harness Core 不永久包含 VisionForge 或其他具体业务场景。Core 只提供显式、可信、进程内的场景插件 SPI；插件依赖 Core 并注册 `ScenarioProfile` 工厂，Core 永远不导入具体插件。

第一版不实现动态下载、Python entry points 或不可信代码沙箱。应用 Composition Root 根据配置显式创建插件并调用 Registry；未注册任何插件时，通用 Coding Harness 必须正常导入、运行和测试。

## Core 接口

- `PluginManifest`：插件 ID、插件版本、Core API 版本、场景清单和依赖声明。
- `ScenarioPlugin`：只暴露 Manifest 和 `register(context)`。
- `PluginRegistrationContext`：只允许注册 Manifest 已声明的场景，完成后关闭。
- `ScenarioRegistration`：保存插件身份、版本、场景名和工厂。
- `RegisteredScenarioProfile`：不修改插件实现，为 ScenarioRuntime 附加插件身份。
- `PluginRegistry`：完成兼容检查、原子注册、命名空间解析和缺失错误语义。

场景引用固定为：

```text
plugin_id:scenario
```

例如后续 VisionForge 注册为：

```text
visionforge:web_visual
```

## 边界

- Core 不能 import VisionForge。
- Core 的通用协议、模型客户端和运行状态不能使用 VisionForge 业务命名。
- 插件不能覆盖其他插件或 Core 注册项。
- Manifest 与实际注册必须完全一致。
- Core API 版本不匹配时，在执行插件代码前拒绝。
- 插件注册失败时，Registry 不保存部分状态。
- 场景工厂返回值必须满足 `ScenarioProfile`，且 name 与声明一致。
- `ScenarioRunState` 和 SQLite 保存插件 ID/版本；恢复时必须与当前 Profile 完全一致。
- 插件是可信进程内代码；第三方不可信插件需要未来增加进程隔离，不能依赖此 SPI 获得安全性。

## 本批未做

- 没有迁移 `coding_workflow/visionforge`。
- 没有修改 Web Server 或 VisionForge API。
- 没有注册 Worker、Validator、Artifact Schema 或 UI 扩展点。
- 没有动态发现、安装、卸载或热更新插件。
- 没有接入模型或媒体处理。

这些扩展只有在真实场景迁移需要时才逐步增加，避免 Core Plugin API 过早膨胀。

## 后续顺序

1. 已完成事实与验证权边界：Claim、三态 VerificationOutcome、VerificationRecord，以及 Worker 执行成功和 Runtime 验收通过的语义分离，见 `Plan/Plan12.md`。
2. 已完成 RequirementEvidence、CodingRequirement、AcceptanceCriterion、EvidenceGrant 和 ValidatorProfile 等 Core 通用协议，见 `Plan/Plan13.md`。
3. 下一批将 WorkerRegistry 改为 Role 优先、同 Role 多实现，并按能力、协议、策略和可用性确定性选择。
4. 增加 VisionForge Plugin 适配器，通过 Registry 注册现有 `WebVisualScenario`；UI Spec、Visual Review 和视觉门禁继续属于插件。
5. Web Server 改为从 Registry 解析已启用场景，不再直接 import VisionForge。
6. 评估将通用媒体存储移入 Core，再把 VisionForge 代码、模板、评测和 Web 扩展迁出 Core 包。
7. 完成迁移后删除 Legacy Runner；通用模型代码中的 VisionForge 响应命名已在本批提前清除。

最新批次 ID、验收条件和禁止跳步规则以 `HANDOFF.md` 与 `OPTIMIZATION_BACKLOG.md` 为准。
