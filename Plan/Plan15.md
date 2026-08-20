# 批次 10E：VisionForge 插件适配

## 目标

让 VisionForge 成为 Harness 上显式启用的业务场景插件，而不是 Core 的永久组成部分。产品入口通过通用 PluginRegistry 获取场景，Core 不 import、不解释 UI 或视觉协议。

## 插件装配

- `VisionForgePlugin` 声明插件 ID `visionforge`、版本 `1.0.0`、Core API `1.0` 和场景 `web_visual`。
- 插件注册 `WebVisualScenario` 工厂，稳定引用为 `visionforge:web_visual`。
- `create_visionforge_plugin_registry()` 只供产品 Composition Root 显式调用；创建空 Core Registry 不会自动加载 VisionForge。
- `VisionForgeScenarioRunner` 接受可信 `ScenarioRegistration`，通过其 `create()` 构造带插件身份的 Profile；旧的直接构造路径暂时保留兼容。

## Web 边界

- `web_server.py` 显式创建 Registry、注册 VisionForge，并注入 `VisionForgeWebRuntime`。
- Web Runtime 在创建模型客户端或运行浏览器前先解析场景；Registry 或场景缺失时安全失败，不回退为直接调用。
- ScenarioRunState 与最终 Runtime Snapshot 保存 plugin ID、plugin version 和 scenario；恢复时由通用 ScenarioRuntime 拒绝身份或版本漂移。

## Artifact 与 Validator 命名空间

VisionForge 自己解释的类型集中在 `artifact_types.py`：

```text
visionforge:reference_image
visionforge:ui_spec
visionforge:actual_screenshot
visionforge:browser_run
visionforge:visual_review
visionforge:quality_gate
visionforge:run
```

通用 ImplementationPlan、Integration Result 和 Build Result 没有强行改成 VisionForge 私有类型。视觉验证记录继续使用 `visionforge:quality_gate` Validator kind。Core 只把上述值当作带命名空间的字符串保存、授权和路由。

## 兼容与未做事项

- VisionForge 包仍位于 `coding_workflow/visionforge`；插件边界依赖方向已经成立，因此没有为目录外观做高风险大迁移。
- 旧 `VisionForgeRunner` 和直接 `VisionForgeScenarioRunner` 入口暂时保留，Web 产品路径已经切换到插件注册。
- 没有改变 UI Spec/Visual Review Schema、视觉阈值、修复策略、模型配置或浏览器行为。
- 没有调用真实模型、真实浏览器、网络或媒体上传。

## 运行证据

- 新增/扩展插件清单、零插件、缺失插件、Web Composition Root、Artifact 命名空间和场景插件身份持久化测试。
- 默认回归：152 项通过，4 项真实浏览器类跳过。
- Python compileall 与 `git diff --check` 通过。
