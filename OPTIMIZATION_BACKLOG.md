# 多模态 Coding Multi-Agent 优化待办

本文是项目方向和优化工作的单一待办清单。`HANDOFF.md` 负责恢复上下文；具体批次、状态和验收条件以本文为准。

- 最后核对：2026-08-21
- 当前批次：批次 12B 进行中
- 当前项：`CORE-AUDIO-001`

## 维护规则

- 状态只使用：`待开始`、`进行中`、`已完成`、`暂缓`。
- 一次只推进一个批次；用户确认“下一批”后才开始后一批。
- 开始时改为 `进行中`；实现、自动化测试和运行证据齐全后才能改为 `已完成`。
- 每批结束必须记录修改文件、自动化测试、无法自动完成的手动检验和下一批内容。
- 模型输出始终是不可信输入。文件写入、命令、浏览器、状态和最终质量门禁由 Runtime 控制。
- 现有 Coding Harness 测试必须尽可能保持通过。

## 产品方向

项目核心是一个支持文本、图片、音频和视频需求证据的 **Coding Multi-Agent Harness**。多模态负责表达需求和问题证据，不限定生成网页；系统可以修改前端、后端、CLI、库或其他现有代码仓库。

核心 MVP 聚焦一条可客观验收的 Coding 链路：

```text
多模态输入 Artifact
  → Requirement Analyst 生成结构化 Coding Requirement
  → Planner 生成 TaskGraph
  → Developer 生成受限 Patch
  → Runtime 安全应用 Patch
  → Validator 运行编译、固定测试和隐藏测试
  → 失败证据触发 Fixer
  → 完整回归与权限门禁决定 completed
```

TaskGraph、WorkerRegistry、ArtifactStore、PatchIntegrator、ProjectWorkspace、SQLite Runtime、生命周期、权限、Checkpoint 和 Web/API 是通用确定性基础设施。浏览器、Visual Reviewer、API 测试器和语言测试器是按任务选择的验证场景；模型不能自行声明通过。

VisionForge 的 Vue/Playwright/VLM 闭环保留为 `web_visual` 场景，不再代表整个产品，也不使用抽象视觉分数评价通用 Coding MVP。

## 已完成的 VisionForge 场景批次

### 批次 1：协议、模型能力、图片引用和 Vue 模板

| ID | 优先级 | 状态 | 内容 | 验收条件 |
|---|---|---|---|---|
| VF-CONTRACT-001 | P0 | 已完成 | 建立版本化 UI Spec 与 Visual Review 协议 | 字段完整、枚举受控、非法数据确定性拒绝、支持 JSON 往返 |
| VF-MODEL-001 | P0 | 已完成 | 为 ModelClient 增加 text、vision、tool_calling、structured_output 能力声明和结构化请求/响应 | 能在调用前拒绝能力不匹配；现有文本调用保持兼容；记录 provider/model/token/耗时字段 |
| VF-ASSET-001 | P0 | 已完成 | 建立 PNG/JPEG 图片资产存储和 Artifact 引用 | 校验格式、大小和尺寸；按 SHA-256 存储；Artifact/SQLite 只保存引用，不保存 Base64 图片 |
| VF-TEMPLATE-001 | P0 | 已完成 | 增加固定 Vue 3 + Vite 页面模板 | 固定依赖版本；Agent 可修改范围与 Runtime 配置分离；模板结构有自动化测试 |

### 批次 2：确定性浏览器闭环

| ID | 优先级 | 状态 | 内容 | 验收条件 |
|---|---|---|---|---|
| VF-BROWSER-001 | P0 | 已完成 | 实现受控 Vue 构建和长生命周期开发服务器 | 固定白名单命令；readiness、超时、取消和进程清理有测试 |
| VF-BROWSER-002 | P0 | 已完成 | 实现 Playwright Browser Tester | 固定 viewport；执行受控 DOM/交互断言；捕获严重控制台错误并生成截图 Artifact |

批次 2 验收记录：

- 新增 `visionforge/browser.py`、固定 Browser Runner、可执行 UI Spec 和浏览器测试。
- 默认测试 90 项通过，真实浏览器用例默认跳过。
- 显式开启的 5 项浏览器测试通过，覆盖 Vue 构建、服务器 readiness、4 个交互/DOM 断言、截图与 Browser Run Artifact，以及 4173 端口清理。
- 没有必须由用户完成的手动检验；运行环境需安装 Playwright Chromium，或由 Runtime 显式提供 Chrome/Chromium 路径。

### 批次 3：单次多模态纵向链路

| ID | 优先级 | 状态 | 内容 | 验收条件 |
|---|---|---|---|---|
| VF-ANALYST-001 | P0 | 已完成 | Requirement Analyst 使用参考图与需求生成 UI Spec Artifact | Fake VLM 契约测试稳定；真实 VLM 可通过配置替换 |
| VF-DEVELOPER-001 | P0 | 已完成 | Developer 使用 UI Spec 和 Vue 项目生成 ImplementationPlan | Patch 只能修改允许路径并由现有 Validator/Integrator 接纳 |
| VF-REVIEW-001 | P0 | 已完成 | Visual Reviewer 对比参考图和实际截图 | 输出版本化 Visual Review；P1/P2、分数和证据字段完整 |
| VF-RUNNER-001 | P0 | 已完成 | 新增 VisionForgeRunner 串联一次完整执行 | UI Spec、Patch、Browser Run、Screenshot、Visual Review 均可追踪为 Artifact |

批次 3 验收记录：

- 新增 Requirement Analyst、Developer、Visual Reviewer 和一次性 `VisionForgeRunner`，角色只接收裁剪输入并产出结构化 Artifact。
- OpenAI 兼容客户端在请求提供 Schema 时使用严格 `json_schema`；三个角色在调用前检查 text、vision、tool_calling 和 structured_output 能力。
- Developer Patch 仍由现有 `PatchIntegrator` 应用；越权修改 `package.json` 的测试被确定性拒绝。
- `visionforge_run` Artifact 串联参考图、UI Spec、ImplementationPlan、Integration Result、Build Result、实际截图、Browser Run 和 Visual Review，并记录模型、Token 和耗时元数据。
- 默认测试 96 项通过，2 个真实浏览器用例默认跳过；显式真实纵向链路的 6 项测试全部通过。
- P1/P2 或低分只生成 `needs_fix` 证据，本批没有自动调用 Fixer。
- 没有必须由用户完成的手动检验；真实供应商模型调用留待提供模型配置和凭据后验证。

### 批次 4：视觉反馈驱动自动修复

| ID | 优先级 | 状态 | 内容 | 验收条件 |
|---|---|---|---|---|
| VF-GATE-001 | P0 | 已完成 | Runtime 实现组合质量门禁 | 构建、DOM、交互、控制台、视觉分数及 P1/P2 全部满足才允许 completed |
| VF-FIXER-001 | P0 | 已完成 | Fixer 使用结构化视觉反馈生成局部 Patch | 首次 P2、修复后通过的端到端测试；旧 Patch 标为 superseded |
| VF-RECOVERY-001 | P1 | 已完成 | 持久化并恢复视觉返工阶段 | 中断恢复不重复已验证副作用；最多两轮修复后确定性失败 |

批次 4 验收记录：

- 新增 Runtime 质量门禁，组合构建、DOM/交互断言、严重控制台错误、页面错误、外部网络、视觉模型结论、视觉分数和 P1/P2；模型的 `passed=true` 不能绕过门禁。
- 新增 Fixer 角色，只读取 UI Spec、Browser Run、Visual Review 和受控源码快照，产出局部 ImplementationPlan 并继续由 `PatchIntegrator` 应用。
- 首次失败的 Patch 标为 failed；修复 Patch 应用后旧 Patch 标为 superseded；只有最终门禁通过的 Artifact 和 Run 才标为 verified/completed。
- 最多允许两轮修复，第三次门禁仍失败时确定性进入 failed，不再调用模型。
- 新增 SQLite 返工 Checkpoint，保存阶段、Artifact 快照、修复轮数和 Workspace 哈希；修复 Patch 已应用后中断可从验证阶段恢复，不重复 Analyst、Developer 或 Fixer。
- Workspace 在 Checkpoint 后被人工修改时拒绝自动恢复。
- 默认测试 102 项通过，3 个真实浏览器用例默认跳过；显式真实修复闭环的 6 项测试全部通过。
- 没有必须由用户完成的手动检验；真实模型输出质量仍需后续使用供应商配置和固定任务集评测。

### 批次 5：Web 上传与 Artifact 调用链

| ID | 优先级 | 状态 | 内容 | 验收条件 |
|---|---|---|---|---|
| VF-WEB-001 | P1 | 已完成 | 增加受控图片上传和 VisionForge 任务 API | 图片类型、大小、路径全部校验；任务只引用 asset ID |
| VF-WEB-002 | P1 | 已完成 | 在 Web 展示参考图、实际截图、UI Spec、视觉问题和修复轮次 | 不展示原始思维过程；每个 Artifact 可沿任务调用链查看 |

批次 5 验收记录：

- 新增内容寻址的 PNG/JPEG 上传 API；校验请求类型、真实图片格式、文件大小、像素尺寸与 asset ID，SQLite 只保存图片引用元数据。
- 新增固定 Vue 模板的 VisionForge 任务 API；客户端不能提交项目路径或 Base64，Runtime 串行占用固定浏览器端口并通过原有 Runner 执行闭环。
- Web 已改为 VisionForge 产品入口，可上传参考图、输入需求，并查看参考图、实际截图、视觉评分、修复轮次与完整 Artifact 调用链；结构化内容使用安全文本渲染，不展示模型原始推理。
- 新增 5 项 Web Runtime/API 契约测试；默认测试共 107 项通过（3 项真实浏览器用例默认跳过）。
- 显式真实浏览器测试分别通过 5 项浏览器闭环、6 项纵向链路和 6 项自动修复闭环；固定 Vue 项目生产构建通过。
- 使用本地隔离假模型执行器完成 Web 手动烟测：真实上传 1672×941 PNG、创建任务、轮询完成、渲染双图、展开 UI Spec Artifact 均正常。
- 无必须由用户完成的手动检验；真实供应商 LLM/VLM 输出质量仍需后续固定任务评测。

### 批次 6：固定页面任务评测

| ID | 优先级 | 状态 | 内容 | 验收条件 |
|---|---|---|---|---|
| VF-EVAL-001 | P1 | 已完成 | 建立固定页面任务集和可重复运行器 | 固定模型、Prompt、Schema、浏览器、viewport 和最大修复轮数 |
| VF-EVAL-002 | P1 | 已完成 | 比较三种交付方案 | 报告构建、DOM/交互、视觉、首次通过、修复成功、轮数、Token、耗时和人工介入 |

批次 6 验收记录：

- 建立版本化 `visionforge-mvp-pages` v1.0.0 固定任务集，包含 SaaS 注册落地页、数据分析仪表盘和电商商品详情页。
- 每个任务包含本地参考 HTML、固定 1440×900 viewport、自然语言需求，以及由 Runtime 拥有的 DOM/交互断言；模型不能改写评测标准。
- 新增受控参考图渲染器，使用预置模板锁定的 Playwright、无外部网络、UTC/中文 locale、固定色彩和关闭动画生成 PNG。
- 新增三方案评测协议：LLM 一次生成、LLM + 浏览器反馈、LLM + Browser Tester + VLM Reviewer；最终统一按固定功能断言和视觉阈值评分。
- 报告记录构建、DOM/交互、视觉、交付成功、首次通过、自动修复、平均轮数、视觉分数、Token、耗时和人工介入，并保存任务集内容指纹与实验配置。
- 默认测试共 113 项通过（4 个真实浏览器类默认跳过）；显式真实测试 18 项通过，其中新增 3 个固定参考页面 PNG 渲染证据。
- 本批未调用真实供应商模型，因此目前只有评测框架和确定性对照测试，没有可用于产品结论的真实模型胜率。

### 批次 7：双模型路由与真实能力烟测

| ID | 优先级 | 状态 | 内容 | 验收条件 |
|---|---|---|---|---|
| VF-MODEL-ROUTING-001 | P0 | 已完成 | DeepSeek 文本模型与 Qwen 视觉模型按角色独立路由 | Developer/Fixer 只用文本客户端；Analyst/Reviewer 只用视觉客户端；能力声明分别校验 |
| VF-MODEL-PROTOCOL-001 | P0 | 已完成 | 适配 DeepSeek/Qwen 的 `json_object` 与 Qwen 非思考模式 | Schema 注入提示并由 Runtime 本地校验；Qwen 请求关闭思考且不设置可能截断 JSON 的 max_tokens |
| VF-MODEL-SMOKE-001 | P0 | 已完成 | 各发送一次最小文本/视觉请求 | 不输出密钥；验证真实模型名、图片输入、JSON 解析、Token 和耗时；未授权外发图片时不得执行 |

批次 7 验收记录：

- 新增文本与视觉模型独立配置及角色路由：Developer/Fixer 使用 DeepSeek 文本客户端，Requirement Analyst/Visual Reviewer 使用 DashScope Qwen 视觉客户端。
- OpenAI 兼容客户端支持供应商级结构化输出模式、固定请求选项和可选 `max_tokens`；`json_object` 响应继续由 Runtime 按本地 Schema 严格解析与校验。
- 新增最小真实模型烟测入口，只输出供应商、模型、Token、耗时和校验状态，不输出响应正文、密钥、`.env` 或源码。
- 经用户明确授权，DeepSeek `deepseek-v4-pro` 文本烟测成功：JSON 校验通过，245 Token，2132 ms。
- 经用户明确授权，仅将 `demo/docs/multi-agent-architecture.png` 作为一次视觉输入发送给 DashScope；Qwen `qwen3.7-plus` 烟测成功：JSON 校验通过，1676 Token，2023 ms。未发送源码、`.env` 或密钥。
- 默认测试共 115 项通过（4 个真实浏览器类默认跳过）；本批没有执行三方案真实基线，也没有据此宣称端到端页面交付质量已通过。

### 批次 8：真实模型基线试跑与校准

| ID | 优先级 | 状态 | 内容 | 验收条件 |
|---|---|---|---|---|
| VF-PILOT-001 | P1 | 已完成 | 选择一组固定 LLM/VLM 配置执行三方案小规模基线 | 记录模型与 Prompt 版本、成本上限、失败证据和可复现 JSON 报告 |
| VF-CALIBRATE-001 | P1 | 暂缓 | 人工盲审少量结果并校准视觉阈值与问题严重级别 | 人工结论与 VLM 判定差异可追踪；不使用保留任务反复调参 |

批次 8 当前进度：

- 已实现三种方案的 Runtime 强制边界：`llm_once` 不允许反馈修复；`llm_browser_feedback` 只允许 Browser/构建证据触发 Fixer，且 Fixer 请求中不包含 Visual Review；`llm_browser_vlm` 才允许两类反馈共同驱动修复。
- 固定 DOM/交互验收 Spec 由评测 Runtime 注入，Requirement Analyst 生成的 UI Spec 不能改写评测标准。
- 构建失败会转为结构化 Browser Run 和截图证据，可进入浏览器反馈修复，而不是直接丢失试验。
- 新增隔离真实执行器、Artifact Bundle 落盘、调用次数/Token 停止条件和默认不调用外部模型的预检入口。
- 3 个任务 × 3 种方案 × 1 次重复、最多 2 轮修复的最坏情况为 21 次文本调用、30 次视觉调用，共 51 次；默认总 Token 停止阈值为 600000。
- 任何重新运行都必须重新确认费用预算后才能使用 `--confirm-real-calls`。
- 首次获批运行已生成 `.runs/visionforge-eval/baseline-20260815-01/report.json`，共尝试 10 次模型调用、观察到 19604 Token，远低于 51 次/600000 Token 上限；本地参考图渲染和真实浏览器预检通过。
- 首次运行没有形成可比较的业务指标：SaaS 三个试验暴露了协议未把 `layout.children` 识别为嵌套区域的问题；随后一次 DeepSeek 和六次 DashScope 请求发生连接拒绝，9 个试验均作为失败证据保留，未手工改写报告。
- 已根据真实失败校准协议：允许组件引用唯一的嵌套 `layout.children` 区域，同时继续拒绝未知或重复区域；失败试验现在会记录验证失败前已消耗的 Token。相关默认回归共 123 项通过。
- 经用户再次授权，校准后的第二次运行已生成 `.runs/visionforge-eval/baseline-20260815-02/report.json`；共尝试 30 次模型调用、观察到 243016 Token，没有超过额外 51 次/600000 Token 上限。首次 10 次/19604 Token 的失败报告继续保留，没有覆盖或改写。
- 第二次运行只有 SaaS 注册页的三种方案形成完整横向结果：三者构建和 DOM/交互均通过，视觉均未过 85 分门禁；一次生成和纯浏览器反馈均为 65 分，Browser + VLM 经两轮修复后为 75 分，中间轮曾达到 85 分但随后回退，说明视觉修复存在不稳定性。
- 数据分析页三次分别因 DeepSeek 空内容、Qwen `component.properties` 类型不符合 Schema、DeepSeek 截断 JSON 而失败；电商页两次因模型引用不存在的本地图片导致构建失败，一次因 DeepSeek 空内容失败。
- 电商页的 Browser Fixer 连续两轮未修好。已确认 Browser Run 仅截取构建错误末尾，丢失了开头的 `/assets/thumb-caramel.jpg` 无法解析这一根因；这是 Runtime 证据保真问题，不应归因于 Fixer 能力。
- 当前真实基线不能证明三方案中任何一个具有稳定交付优势：9 个试验交付通过率均为 0；它的价值是暴露了结构化输出可靠性、构建证据截断和视觉修复回退三个具体问题。
- 人工校准先只检查 SaaS 的参考图和三张最终截图，记录人与 VLM 对 65/65/75 分及 P1/P2 严重级别的分歧。完成前不调整 85 分阈值，也不对固定评测任务反复调用模型调参。

批次 8 方向修正：

- 两次真实报告作为 `web_visual` 场景的探索性失败证据保留，不再用来评价核心 Multi-Agent Coding 能力。
- 开放式网页设计存在多种合理实现，单一 VLM 视觉分数缺少确定标注，不适合作为第一个 MVP 的主要通过标准。
- `VF-CALIBRATE-001` 暂缓；只有后续明确需要衡量参考图还原能力时，才使用独立视觉缺陷集和人工标注重新启动。

## 核心 Coding MVP 批次

### 批次 9：产品重新定位与客观评测设计

| ID | 优先级 | 状态 | 内容 | 验收条件 |
|---|---|---|---|---|
| CORE-DIRECTION-001 | P0 | 已完成 | 将产品重新定位为多模态输入的 Coding Multi-Agent Harness | 文档明确核心能力、场景边界和非目标；VisionForge 作为可选场景保留 |
| CORE-MVP-001 | P0 | 已完成 | 定义第一个通用 Coding MVP 的闭环和完成条件 | completed 只由编译、测试、隐藏断言、权限和回归等 Runtime 证据决定 |
| CORE-EVAL-DESIGN-001 | P0 | 已完成 | 设计容易判断的固定 Coding 任务和多 Agent 对照 | 任务具有确定输入、隐藏验收和唯一通过事实；指标不依赖抽象审美分数 |

批次 9 验收记录：

- 核心输入允许文本、图片、音频和视频 Artifact；输入模态不绑定输出项目类型。
- Requirement Analyst、Planner、Developer、Tester、Reviewer 和 Fixer 是可组合角色，不要求每个任务全部启用。
- Validator 按场景选择：语言测试、构建、API、CLI、浏览器 DOM/交互或视觉专项；通用任务不加载 Visual Reviewer。
- 第一组客观任务覆盖函数 Bug、API 校验、跨文件功能、图片规格/错误证据和确定性修复闭环；最终由固定测试或隐藏测试判定。
- 对照方案改为单 Agent、Planner + Developer、Planner + Developer + Tester/Fixer，用于回答多 Agent 协作是否提高 Coding 交付率。
- 本批只修正文档和实施顺序，没有调用外部模型、修改 Runtime 或删除 VisionForge 能力；详细方案见 `Plan/Plan09.md`。
- 无需用户手动检验；文档差异通过 `git diff --check` 校验。

### 批次 10A：Core 插件边界

| ID | 优先级 | 状态 | 内容 | 验收条件 |
|---|---|---|---|---|
| CORE-PLUGIN-001 | P0 | 已完成 | 建立不依赖具体业务场景的插件 SPI 和注册表 | Core 可在零插件下运行；插件按 Core API 版本显式注册；场景引用有命名空间；注册失败不污染 Registry；恢复时校验插件身份与版本 |

批次 10A 验收记录：

- 新增 `PluginManifest`、`ScenarioPlugin`、`PluginRegistrationContext`、`ScenarioRegistration` 和 `PluginRegistry`；Core 只提供接口与显式可信注册，不动态导入或安装第三方代码。
- 场景使用 `plugin_id:scenario` 引用；Manifest 声明与实际注册必须完全一致，注册过程先 staging、校验成功后再原子提交。
- Core API 版本不兼容、插件未启用、场景未注册、重复注册和错误工厂输出都有独立确定性错误语义。
- Registry 创建的场景由 `RegisteredScenarioProfile` 包装；`ScenarioRunState` 和 SQLite 保存插件 ID/版本，恢复时版本不一致会被确定性拒绝，旧的非插件场景保持空身份兼容。
- 注册上下文完成后关闭，插件不能在启动阶段之外继续修改 Registry；Core 模块禁止导入 `visionforge` 的架构测试已加入。
- 通用 `OpenAICompatibleClient` 的 JSON Schema 名称由 `visionforge_response` 改为中性的 `structured_response`，避免 Core 模型层携带业务命名。
- 本批没有迁移或修改 VisionForge、Web、模型路由行为和媒体协议；工作区中既有的 `visionforge-planned-architecture.html` 保持不动。
- 完整默认回归 123 项通过（4 项真实浏览器类跳过）；Python 编译检查通过；无需用户手动检验、没有外部模型调用。
- 详细决策见 `Plan/Plan11.md`。

### 批次 10B：事实与验证权边界

| ID | 优先级 | 状态 | 内容 | 验收条件 |
|---|---|---|---|---|
| CORE-TRUTH-001 | P0 | 已完成 | 建立 `Claim`，区分 observation、inference 和 proposal | 每个 Claim 的来源、证据引用和不确定性可追踪；模型推断不能伪装成 Runtime 事实 |
| CORE-VERIFICATION-001 | P0 | 已完成 | 建立三态 `VerificationOutcome` 和不可变 `VerificationRecord` | 只允许 passed/failed/unknown；unknown 保持 Artifact 未验证；无有效执行证据不能标记 VERIFIED |
| CORE-VERIFICATION-AUTH-001 | P0 | 已完成 | 收紧 Artifact 验证权并分离执行成功和验收通过 | Worker 正常返回只代表执行完成；输出默认 UNVERIFIED；模型/Worker 伪造验证字段会被拒绝；completed 仍由 Runtime 决定 |

批次 10B 验收记录：

- 新增 `ClaimKind`、`Claim`、`VerificationOutcome` 和不可变 `VerificationRecord`，支持严格字段校验和映射往返。
- observation 必须引用证据，inference 必须写明不确定性；passed/failed 必须包含独立执行证据且不能用被验证 Artifact 自证，unknown 不会把 Artifact 标成通过。
- `ArtifactStore.record_verification()` 原子检查 subject、Artifact 证据、superseded 状态和重复记录；验证记录随 ArtifactValidation 和 SQLite Runtime Snapshot 持久化恢复。
- `ArtifactDraft` 拒绝 metadata 中伪造的验证状态与引用字段；正文中的 `passed`、`verified` 等只作为不可信业务数据保存，不会影响 Artifact 的 `UNVERIFIED` 状态，避免 Core 误伤业务 Schema。
- `TaskRunResult.success` 和 `GraphExecutionResult.succeeded` 只表示执行成功；Executor 不再自动验证全部输出、晋升长期记忆或宣布 completed，新增默认 unknown 的 `acceptance_outcome` 和 `accepted`。
- 回归发现 VisionForge 旧逻辑让 quality gate 同时充当验证对象和自身证据；现已改为 build/browser/review 验证 quality gate，再由 quality gate 验证其他周期产物，没有改变场景流程。
- 新增 7 项事实边界测试；完整默认回归 130 项通过（4 项真实浏览器类跳过），Python 编译和差异检查通过；无真实模型、媒体或网络调用。
- 详细决策见 `Plan/Plan12.md`。

### 批次 10C：通用多模态需求与验收协议

| ID | 优先级 | 状态 | 内容 | 验收条件 |
|---|---|---|---|---|
| CORE-INPUT-001 | P0 | 已完成 | 建立通用 `RequirementEvidence`，引用 text/image/audio/video Artifact | MIME、大小、来源、哈希、派生关系和访问分类受控；非法组合被拒绝 |
| CORE-REQUIREMENT-001 | P0 | 已完成 | 建立与 UI 无关的 `CodingRequirement` | 目标、交付物、约束、验收、仓库范围、假设、开放问题、证据和扩展引用可版本化往返；模型不能扩大权限 |
| CORE-ACCEPTANCE-001 | P0 | 已完成 | 建立 `AcceptanceCriterion` 和 `EvidenceGrant` | 验收项指向命名空间 Validator；Role 只能读取 Runtime 授权证据；插件扩展不污染 Core 字段 |
| CORE-VALIDATOR-001 | P0 | 已完成 | 建立任务级 Validator Profile | Runtime 根据冻结清单选择 build/test/API/CLI/browser 或插件 Validator；模型不能增加、删除或降低最终门禁 |

批次 10C 验收记录：

- 新增 `RequirementEvidence`、`CodingRequirement`、`RepositoryScope`、`AcceptanceCriterion`、`EvidenceGrant`、`ValidatorSpec` 和 `ValidatorProfile` 1.0；严格校验类型、MIME、SHA-256、相对范围、命名空间和 JSON 可序列化值。
- RequirementEvidence 会与真实 Artifact 的引用、MIME、大小和原始内容哈希核对；CodingRequirement 与所有协议支持稳定摘要和 JSON 往返。
- Runtime 采用保守 RepositoryScope 子集判断，结构化需求不能扩大读写范围或删除 prohibited actions；插件扩展只通过 Artifact 引用进入 Core。
- ValidatorProfile 同时冻结 Validator 配置和 AcceptanceCriterion 摘要；保留同 ID 但降低 expected_result、required 或描述同样会被拒绝。
- TaskContext 可携带 CodingRequirement；启用后 Executor 强制要求独立注入匹配的 ValidatorProfile、RequirementEvidence 和 EvidenceGrant，授权与 Task、Role、引用、操作和到期时间绑定。
- 新增 Runtime `ValidatorRegistry + ValidatorProfileRunner`：缺失能力和执行异常变成 unknown；每次生成可审计 report Artifact，只有组合 profile gate 能改变最终验证状态。
- VerificationRecord 绑定 subject hash 和可选 Workspace hash；`ArtifactStore.is_verified()` 检查新鲜度，`TaskSpec.required_verified_inputs` 在 Worker 调用前拒绝过期或无效证明并支持 SQLite 往返。
- 新增 11 项需求/验证测试并扩展 1 项事实测试；完整默认回归 142 项通过（4 项真实浏览器类跳过），Python 编译和差异检查通过；无真实模型、媒体、浏览器或网络调用。
- 详细决策见 `Plan/Plan13.md`。

### 批次 10D：Role 优先的多 Worker 路由

| ID | 优先级 | 状态 | 内容 | 验收条件 |
|---|---|---|---|---|
| CORE-WORKER-DESCRIPTOR-001 | P0 | 已完成 | 为 Worker 声明 ID、支持 Role、能力、输入/输出协议、策略标签和可用性 | 同一 Role 可注册多个实现；描述与实例解耦并可生成不可变快照 |
| CORE-WORKER-ROUTER-001 | P0 | 已完成 | Role 作为第一键，按能力、协议、策略和可用性确定性选择 Worker | 不满足硬条件的 Worker 不进入评分；稳定 tie-break；选择结果及理由可审计 |
| CORE-WORKER-SAFETY-001 | P0 | 已完成 | 建立缺失能力和职责隔离语义 | 无合格 Worker 时结构化 blocked，不跨 Role 或降级要求；Reviewer/Validator 不能审批同一执行实例自己的产物 |

批次 10D 验收记录：

- 新增不可变 `WorkerDescriptor`、`WorkerSelectionRequest/Decision`、候选淘汰原因和 Registry Snapshot；描述与实例分离，同一 Role 可注册多个 Worker，旧 `register(role, worker)` 保持兼容。
- Runtime 严格按 Role、能力、输入协议、输出协议、策略标签、职责隔离和可用性过滤；所有条件都是硬门槛，最后只按 priority 与 worker ID 稳定决胜，不跨 Role、不降低要求。
- `TaskSpec` 可声明路由要求和 `independent_from_tasks`，字段随 SQLite TaskGraph Snapshot 恢复；选择结果和每个候选的淘汰阶段也随 Checkpoint 保存。
- 无合格 Worker 时产生结构化 `WorkerSelectionError` 并将任务标为 blocked；Worker 不会被调用，依赖节点保持阻塞，不伪装成模型执行失败。
- Executor 为接纳的 Artifact 写入不可伪造的 Runtime producer provenance；Reviewer/Tester 自动排除输入产物的生产 principal，ValidatorRegistry 也拒绝同 principal 自证，结果保持 unknown。
- 新增 7 项路由与职责隔离测试；完整默认回归 149 项通过（4 项真实浏览器类跳过），Python 编译和差异检查通过；无真实模型、媒体、浏览器或网络调用。
- 详细决策见 `Plan/Plan14.md`。

### 批次 10E：VisionForge 插件适配

| ID | 优先级 | 状态 | 内容 | 验收条件 |
|---|---|---|---|---|
| VF-PLUGIN-001 | P0 | 已完成 | 将现有 `WebVisualScenario` 包装为 `visionforge:web_visual` | Web 从 PluginRegistry 解析场景；未启用插件时 Core 与通用入口正常运行 |
| VF-BOUNDARY-001 | P0 | 已完成 | 将 UI Spec、Visual Review 和视觉门禁固定在 VisionForge 插件边界 | 使用 `visionforge:*` Artifact/Validator 命名空间；Core 不 import、不解释且不内置视觉通过逻辑 |

批次 10E 验收记录：

- 新增 `VisionForgePlugin` 1.0.0，显式声明 `web_visual` 场景及所需 Runtime 能力；Core 仍为空注册表启动，不会自动 import 或启用 VisionForge。
- Web Composition Root 显式创建 Registry、注册插件并解析 `visionforge:web_visual`；缺少 Registry 或场景时安全拒绝，不回退到直接装配。
- `VisionForgeScenarioRunner` 通过 `ScenarioRegistration.create()` 获取包装后的 Profile，场景状态和 SQLite Snapshot 保存 `plugin_id=visionforge`、插件版本和场景身份，恢复时继续使用 Core 的漂移校验。
- UI Spec、参考图、实际截图、Browser Run、Visual Review、Quality Gate 和最终 Run 的 Artifact kind 已迁移到 `visionforge:*`；视觉 Validator 原有 `visionforge:quality_gate` 保持命名空间化。
- 新增插件清单、零插件、Web 装配、缺失插件、Artifact 命名空间和真实场景身份持久化测试；完整默认回归 152 项通过（4 项真实浏览器类跳过）。
- 本批没有做目录大迁移、删除 Legacy Runner、调用真实模型/浏览器、上传媒体或改变视觉评分逻辑；详细决策见 `Plan/Plan15.md`。

### 批次 11：确定性 Coding 任务集与对照评测

| ID | 优先级 | 状态 | 内容 | 验收条件 |
|---|---|---|---|---|
| CORE-VALIDATOR-IMPLEMENTATIONS-001 | P0 | 已完成 | 为 Core 注册受控 build/test/CLI Validator 并接入 Profile Runner | 白名单命令、超时、退出码、stdout/stderr 证据和 Workspace 绑定可审计；缺失工具保持 unknown |
| CORE-EVAL-FIXTURE-001 | P0 | 已完成 | 建立第一个固定函数 Bug 任务和 Runtime 私有隐藏验收 | starter 稳定失败、参考修复稳定通过；隐藏源码不进入 Agent Workspace；任务文件哈希冻结 |
| CORE-EVAL-001 | P0 | 已完成 | 建立固定本地 Coding 任务和隐藏验收 | 每个任务可离线复位；失败原因可定位；最终结果不依赖模型评分 |
| CORE-EVAL-REPORT-001 | P0 | 已完成 | 建立 starter/参考修复自校准和版本化离线 JSON 报告 | 坏题不能通过校准；报告记录任务指纹、Validator、交付、耗时、越权和 Workspace 绑定且不泄漏隐藏源码 |
| CORE-ABLATION-PROTOCOL-001 | P0 | 已完成 | 冻结单 Agent、双角色和完整修复闭环的 Artifact、预算与指标协议 | 三种方案共用任务/Validator/预算；反馈不串线；脚本化 dry-run 覆盖首次通过、修复、越权和预算分支 |
| CORE-ABLATION-MODEL-ADAPTER-001 | P0 | 已完成 | 将供应商无关 ModelClient 适配为四种评测 Worker | Plan/Patch/Diagnosis 使用版本化结构化输出；Prompt、可见 Artifact、用量和源码外发预检可审计；Fake Model 回归通过 |
| CORE-ABLATION-001 | P1 | 暂缓 | 使用冻结配置实际比较单 Agent、双角色和完整修复闭环 | 报告构建/测试/交付/首次通过/修复/回归/越权/Token/耗时/人工介入；脚本 dry-run 不计入效果结论 |

批次 11A 验收记录：

- 新增 `ControlledCommandRunner` 和 `CommandValidator`，Core Composition Root 只能按完整 argv 白名单注册 `core:build`、`core:test` 和 `core:cli`；命令不通过 shell，并使用固定 Workspace、清理后的环境、关闭 stdin 和独立进程组。
- 超时、缺失工具、策略拒绝和执行异常保持 `unknown`；非预期退出码、缺少固定输出或零测试为 `failed`；全部命令满足冻结断言才为 `passed`。
- 命令证据 Artifact 保存退出码、耗时、脱敏头尾日志、原长度和 SHA-256；断言使用未裁剪的进程输出，避免日志裁剪导致误判。
- 新增版本化 `python-tax-rounding` 固定任务。starter 和公开测试进入 Agent Workspace；隐藏检查只注入独立验证副本；参考答案只用于证明任务夹具可解。
- `suite.json` 固定任务文件 SHA-256、允许写入路径和 Validator 命令。清单哈希漂移、保留隐藏目录和符号链接会被拒绝。
- 离线证据确认 starter 的 build/公开测试通过但隐藏门禁失败；应用参考修复后 build、公开测试和隐藏检查全部通过，最终结论由绑定 Workspace 的 Profile gate 给出。
- 新增 8 项命令、三态、安全边界和固定任务纵向测试；完整默认回归 160 项通过（4 项真实浏览器类跳过）。
- 本批没有调用真实模型、网络、浏览器或上传媒体；详细设计见 `Plan/Plan16.md`。

批次 11B 验收记录：

- 固定任务集从一个函数 Bug 扩展为三类：十进制舍入、API payload 输入契约、跨文件库存 CLI；仍不使用网页或视觉评分。
- `python-user-payload` 由 build、公开测试和 Runtime 私有检查验证对象类型、允许字段、email、age/bool 与数值边界。
- `python-inventory-cli` 同时验证 `pricing.py` 和 `cli.py`，由 build/test/CLI Profile 检查折扣结果、stdout、非法输入退出码和 stderr。
- 新增 `FixedCodingEvaluationRunner`，每次从冻结 starter 创建新 Workspace，并分别运行 starter 与参考修复；只有 starter=failed 且 solution=passed 才通过逐题校准。
- 版本化 1.0 JSON 报告记录 suite manifest SHA-256、每个 Validator 三态、交付、失败摘要、耗时、越权次数和验证 Workspace 哈希；不保存隐藏源码、具体隐藏输入、临时路径或失效 Artifact 引用。
- 实际离线运行得到 3 个 starter 全部失败、3 个参考修复全部通过，`calibration_passed=true`；构造“starter 已能通过”的坏题时校准会失败。
- 新增 4 项多任务、复位、报告和坏题测试；完整默认回归 164 项通过（4 项真实浏览器类跳过），Python 编译和差异检查通过。
- 本批没有调用真实模型、网络、浏览器或上传媒体；详细设计见 `Plan/Plan17.md`。

批次 11C 验收记录：

- 新增 `AblationStrategyProfile`，冻结 `single_agent`、`planner_developer` 和 `planner_developer_tester_fixer` 的 Role、阶段、输入/输出 Artifact 和同一 `AblationBudget`。
- Worker 继续由 Role-first `WorkerRegistry` 按能力、协议、`offline-eval` 策略和 principal 隔离选择；Tester principal 与 Implementer 分离。
- 单 Agent/普通 Developer 看不到 Validator Feedback；Tester 只看到 Runtime 生成的结构化失败摘要；Fixer 额外看到 Test Diagnosis。隐藏测试、具体隐藏输入和参考答案不会进入 Worker 请求或 JSON 报告。
- Patch 仍由 `PatchIntegrator` 应用，修改公开测试或越过任务允许路径会明确失败并增加越权计数；每轮修复后重新运行原冻结 build/test/CLI Profile。
- 调用前按最大 Token 预留检查预算，返回后登记脚本或模型用量；预算耗尽保持 unknown。离线 Runner 默认禁止登记模型用量，真实模式必须显式开启。
- 脚本化 dry-run 完成 3 任务 × 3 方案共 9 个 trial：单 Agent 0/3、Planner + Developer 首次通过 3/3、完整方案经一轮修复通过 3/3；这些是刻意编排的控制流测试，不是多 Agent 效果结论。
- 报告记录首次通过、修复成功、轮数、Worker 调用、脚本/模型 Token、耗时、越权、人工介入、Validator 结果、budget/profile 指纹和阶段可见性审计。
- 新增 7 项策略、隔离、预算、越权、模型误接入和报告测试；完整默认回归 171 项通过（4 项真实浏览器类跳过）。
- 本批没有调用真实模型、网络、浏览器或上传媒体；详细设计见 `Plan/Plan18.md`。

批次 11D 验收记录：

- 新增供应商无关 `ModelAblationWorker`，将 Planner、Implementer、Tester 和 Fixer 注册为 `model-eval` Worker；Role 仍是第一路由键，四个 principal 相互独立。
- 新增 Plan/Patch/Diagnosis 1.0 JSON Schema 和本地严格解析。未知字段、非法版本、未授权/受保护路径会被拒绝；Diagnosis 没有模型可写的通过字段。
- `prepare()` 在零网络状态生成确定性请求：只投影 Stage 可见 Artifact，按文件裁剪源码，拒绝 `.env`、隐藏测试、参考答案和 Runtime 私有路径，并记录无正文的 SHA-256/字符数/截断披露清单与请求哈希。
- 真正调用客户端前检查 text/structured_output/tool_calling 能力；响应 Artifact 记录协议/Prompt 版本、provider/model、Token、延迟和披露审计。Runner 原有调用前预算与真实模型显式开关保持有效。
- Fake Model 完整链路先产生失败候选，再经独立 Tester/Fixer 修复，最终由真实本地隐藏测试裁决通过；另覆盖能力缺失、Patch 越权、秘密路径披露和伪造 `passed=true`。
- 新增 7 项 Model Worker 测试；完整默认回归 178 项通过（4 项真实浏览器类跳过），差异检查与 Python 编译通过。
- 本批没有读取 `.env`、调用真实模型、访问网络、上传源码/图片或运行浏览器；详细设计见 `Plan/Plan19.md`。

批次 11E 预检记录（真实运行前）：

- 新增 Core `ModelCallBudget` 与 `BudgetedModelClient`。四个 Role 共享调用/Token 预算，HTTP/解析失败和缺少 usage 都按整笔预留计费，下一次调用必须先有足够 Token 余额。
- 新增零环境变量 `ModelClientFactory.config_for_provider()`，固定 provider/model/base URL、temperature、结构化输出、输出上限和重试；真实消融强制 `max_retries=0` 和供应商 `max_tokens`。
- 新增版本化实验配置、调用估算和源码披露 preflight。3 个任务的三方案最少 15 次、最坏 21 次请求；披露 starter/公开测试，不披露 `.env`、隐藏验收或参考答案。
- 新增真实 CLI，默认不读取 `.env`；只有 `--confirm-real-calls` 与匹配的 preflight SHA-256 同时存在才加载凭据并执行。
- 当前冻结 DashScope `qwen3.7-plus`、temperature 0、最多 21 次请求、300,000 accounted Token、单次输出 4,000 Token；preflight SHA-256 为 `a645b66f56a000f642b9447372d2fb4248792260f19f53675a97c0079cc87524`。
- 新增 7 项测试；完整默认回归 185 项通过（4 项真实浏览器类跳过），编译和差异检查通过。
- 本阶段没有读取 `.env`、访问网络或调用真实模型；用户随后要求进入下一批，`CORE-ABLATION-001` 已暂缓。详细设计见 `Plan/Plan20.md`。

### 批次 12：逐步接通多模态输入

| ID | 优先级 | 状态 | 内容 | 验收条件 |
|---|---|---|---|---|
| CORE-IMAGE-001 | P1 | 已完成 | 图片中的规格、架构图或错误证据进入 Coding Requirement | 与对应文本任务共用同一隐藏测试；记录需求提取准确性 |
| CORE-AUDIO-001 | P1 | 进行中 | 音频需求转成受控文本和 Requirement Artifact | 原音频、转录、结构化需求和代码结果可追踪；最终仍由代码测试判定 |
| CORE-VIDEO-001 | P1 | 待开始 | 录屏提取操作步骤和 Bug 证据 | 时间点、操作、预期/实际结果可追踪；回归测试复现并验证修复 |

批次 12A 验收记录：

- 新增 Core `ImagePerceptionWorker`，图片只在受控感知节点进入 ModelClient；下游普通 Planner 只读取 `core:image_observation`，不重复传图。
- 视觉感知和文本规划继续共用 `planner` Role，但分别通过 `vision_understanding`/`task_planning`、输入输出协议和 `multimodal`/`text` 策略选择不同 Worker。
- 图片必须同时满足 RequirementEvidence、Task/Role/Artifact 绑定的 `read + vision:inspect` EvidenceGrant、字节数、SHA-256 和 PNG/JPEG 签名，任何错误都在模型调用前拒绝。
- 感知协议只允许 observations、带 uncertainty 的 inferences 和 unreadable regions；Runtime 转为已有 Claim，拒绝模型添加 `passed`、验收标准、命令或权限字段，输出 Artifact 保持 unverified。
- 新增确定性感知 precision/recall/F1，固定事实漏报降低 recall，额外幻觉降低 precision，不使用抽象视觉审美评分。
- text/image 两条路径复用同一个 `python-tax-rounding` 隐藏 Validator，应用相同候选后 Validator 集合和结果完全一致。
- 新增 7 项图片证据、Role-first 路由、授权、能力、完整性、幻觉和同验收测试；完整默认回归 192 项通过（4 项真实浏览器类跳过）。
- 本批没有读取 `.env`、访问网络、调用真实模型或上传媒体；详细设计见 `Plan/Plan21.md`。

## 暂缓的通用优化

以下工作保留，但在核心 Coding MVP 跑通前不推进：

- 通用记忆测评、检索权重调优、向量或混合检索。
- 符号级调度冲突分析和更复杂的通用 DAG。
- 自由 Agent 聊天、多租户和复杂长期记忆。
- Spring Boot 等后端业务自动生成。
- 与核心 Coding 闭环无关的通用工具调用平台。

## 已完成的 Harness 基线

- `ScenarioRuntime + ScenarioProfile + ConvergenceDecision` 统一控制多轮 DAG 和终态。
- `SQLiteScenarioRunStore` 保存轮次清单并复用 `RuntimeSnapshot` 完成跨轮恢复。
- `ArtifactDraft` 收回共享 Artifact 接纳权；VisionForge Worker 使用隔离 staging store。
- 动态局部 FixTask、受影响验证和最终完整质量门禁。
- TaskGraphRuntime、尝试次数、生命周期、Artifact 与 Workspace 哈希持久化和恢复。
- 文件、符号、测试和 Artifact 实体索引。
- 长期记忆去重、失效、过期和 `supersedes` 版本替代。
- 结构化 Working Memory、统一上下文预算和常见敏感信息持久化前脱敏。

## 当前基线

- 基线提交：`4a357ef chore: archive daily progress 2026-08-19`
- 当前测试：`python3 -m unittest discover -s tests -q`，192 个测试通过（4 个真实浏览器类默认跳过）。
- 浏览器闭环：批次 2 的 5 个浏览器测试、批次 3 的 6 个纵向链路测试、批次 4 的 6 个修复闭环测试和批次 6 的固定参考图渲染测试共 18 项显式通过。
- Vue 构建：固定 Vue 3.5.40、Vite 7.3.6、`@vitejs/plugin-vue` 6.0.8、Playwright 1.62.0；`pnpm run build` 已通过。
- 当前环境 PATH 未提供 Node/npm；已使用 Codex 工作区 Node 24.19.0 与 pnpm 11.19.0 完成锁文件和构建验证。
