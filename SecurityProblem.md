# 安全问题：功能主线与本地执行安全门禁的优先级

## 文档定位

本文记录一个会反复出现在 Agent Harness 开发中的决策问题：当下一项功能在技术上不依赖安全加固，但项目已经存在可验证的执行风险时，应该继续功能主线，还是先完成安全门禁？

本文是问题分析与决策依据，不是 Runtime Acceptance、生产安全声明或实现授权。代码、测试、Git 和版本化 VerificationReport 仍是事实来源。

## 问题背景

当前项目已经完成 `PROD-01B-3A` 的 durable Outbox intent，以及 `PROD-01B-3B-1` 的本地 claim、NACK 和 expiry-reclaim。正式功能主线的下一批是 `PROD-01B-3B-2`：

```text
claim 已提交并释放 SQLite writer lock
  → 事务外调用 Transport
  → 接收 ACK
  → 新事务追加 immutable Receipt
  → 以当前 ownership CAS 到 PUBLISHED
```

与此同时，仓库已经冻结 `local_trusted_execution/v1` 的安全口径，但当前 CLI、Workspace 和 Browser 执行入口尚未达到该口径。由此产生两个可行顺序：

- **A / 安全优先**：先完成 `SEC-EXEC-01`，再实施增强版 `01B-3B-2`。
- **B / 功能优先**：先完成最小 `01B-3B-2`，但把 `SEC-EXEC-01` 设为再次运行真实模型生成代码、执行候选代码或进入 `PROD-01D` 前的硬门禁。

真正的分歧不是要不要做 `3B-2`，而是安全 Patch 是否必须排在它之前。

## 为什么这个问题重要

这是一个典型的“技术依赖”和“安全发布门禁”不相同的问题。

`3B-2` 主要处理 SQLite Outbox、Transport、ACK、Receipt 和投递恢复。只要使用可信的本地测试，它并不依赖模型生成代码、Browser 或旧 Workspace 执行入口，因此 `SEC-EXEC-01` 不是它的纯技术依赖。

但系统继续运行真实模型生成代码、执行候选代码或接入现有 CLI/Web 主链时，会进入当前尚未统一的子进程边界。此时安全 Patch 又是产品继续扩展前的必要门禁。若只按依赖图排序，容易低估风险；若把所有安全工作都描述成技术前置，又可能让安全范围无限膨胀并阻塞主线。

因此必须同时回答：

1. 当前要实现的功能是否真的需要危险执行入口？
2. 项目是否能确定性地保证开发期间不会误用该入口？
3. 一旦发生误用，后果是否可逆？
4. 推迟安全 Patch 会不会让后续接线和测试建立在错误边界上？

## 当前可验证的执行缺口

### 1. 父进程秘密可能进入子进程

[`demo/coding_agent_cli.py`](demo/coding_agent_cli.py) 会加载 `.env`，随后创建旧的 `ProjectWorkspace`。 [`demo/coding_workflow/workspace.py`](demo/coding_workflow/workspace.py) 调用 `subprocess.run` 时没有传入从空映射构造的显式环境，因此默认继承父进程环境。

[`demo/coding_workflow/visionforge/browser.py`](demo/coding_workflow/visionforge/browser.py) 的 Browser Runner 会复制 `os.environ` 后启动子进程。这样一来，Provider Key、代理、SSH Agent、语言注入变量或其他父环境数据都可能进入并不需要它们的 build、test、dev server 或 browser 进程。

风险不只是在子进程中“能够读取秘密”。如果测试、依赖或候选代码打印环境，秘密还可能继续进入 stdout/stderr、server log、Artifact、Event、SQLite 或下一轮模型输入。

### 2. 命令允许列表不等于可信可执行文件身份

当前部分入口主要检查 executable 名称和 argv，但尚未统一绑定：

- 由受信任 Composition Root 解析的绝对 executable；
- 冻结且不包含 Workspace 的 PATH；
- 完整 argv、cwd、环境和输入摘要；
- 版本化 Profile 及其 digest；
- 调用方只能收紧、不能放宽的限制。

因此“允许 `python3`”并不等价于“确定执行了预先登记的那个 Python”。父环境、PATH 或入口差异仍可能造成机器间漂移，甚至执行错误的同名程序。

### 3. timeout/cancel 不等于副作用已经停止

不同 Runner 当前使用不同的进程创建、超时和终止方式。旧 Workspace 没有统一的进程组监督和清理屏障。直接子进程退出或被杀死后，子孙进程、开发服务器、监听端口、文件句柄或持续写入仍可能存活。

这会造成危险的状态分裂：Runtime 已记录失败、取消或超时，但宿主上的实际副作用还在继续。只有统一执行：

```text
TERM process group
  → 等待 grace
  → KILL process group
  → wait/reap
  → 核对 PID/PGID/port/handle
```

并在核对失败时返回 `CLEANUP_FAILED`，才能把“命令结束”与“资源确实回收”绑定起来。

### 4. 文本策略不能代替系统强制

Task 或 Prompt 中的“不要访问网络”“不要读取秘密”“不要执行命令”是行为要求，不是宿主级隔离。当前版本也不承诺容器、VM、独立 UID、文件系统 containment、默认断网或资源硬配额。

因此项目最多可以称为“面向可信本地任务的受控执行”，不能把普通宿主子进程描述成生产沙箱。

## 方案 A：先完成安全 Patch

### 内容

先完成 `SEC-EXEC-01 local_trusted_execution/v1`，统一 Core Validator、Legacy Workspace、VisionForge build/dev/browser 的 Command/Environment Profile、进程组监督、清理屏障、输出限长和脱敏边界。随后实施带首个本地持久 Sink、ACK/Receipt 故障证据和定向容量门的增强版 `01B-3B-2`。

### 优点

- 在再次运行真实模型生成代码或候选代码前关闭已知的秘密继承风险。
- 让 timeout、cancel、异常和后台 stop 具有统一的资源回收语义。
- 避免 `PROD-01D` 把旧执行边界接入新的持久 Runtime 后再进行二次迁移。
- 正常路径和负向测试可以围绕同一版本化 Profile 建立，不再为多个 Runner 维护平行安全语义。
- 项目的安全表述与实际能力更一致。

### 缺点

- 会延迟 `PROD-01B-3B-2` 和完整 `PROD-01B` 的完成时间。
- Patch 横跨 CLI、Workspace、Core Validator 和 Browser，验证面大于单纯 Outbox 切片。
- 显式环境可能暴露 Python、Node、Playwright 或本地工具对隐式环境变量的依赖，需要补正常路径兼容证据。
- 进程组、后台服务、端口和异常清理在 macOS/POSIX 上存在较多边界条件。
- 如果范围没有冻结，容易从“可信本地执行”膨胀成容器、生产沙箱或多租户隔离，造成主线失焦。

### 对项目进度的真实影响

A 不会推翻或重开 `01B-1/2/3A/3B-1`，不修改已发布 Schema v3，也不删除 `3B-2`。它改变的是近期顺序和里程碑时间：`PROD-01B` 的功能完成会推迟，但项目的可安全演示、可接入和可维护性会前进。

因此 A 不是“对进度没有影响”，而是用一部分功能交付速度换取更低的执行风险和更少的后续迁移债务。

## 方案 B：先完成最小 3B-2

### 内容

保持正式 Plan 的当前顺序，先只实现事务外 Transport、ACK、immutable Receipt、PUBLISHED ownership CAS、重复投递和重启恢复。安全 Patch 继续作为真实模型生成代码、候选代码执行和 `PROD-01D` 前的硬门禁。

### 优点

- 最符合当前正式 Plan，切片边界集中在 Outbox 生命周期。
- 可以更快闭合 `PENDING/CLAIMED → PUBLISHED`，尽早得到可靠发布语义的故障证据。
- 不需要同时处理 Python、Node、Browser 和后台进程兼容性，短期认知负担较低。
- 在只使用可信、确定性的本地单元测试时，安全缺口与本切片没有直接执行依赖。

### 缺点

- 已知安全缺口继续存在，项目必须依赖“暂时不要运行这些入口”的操作纪律。
- 一旦有人误启真实 CLI、Browser 或候选代码执行，秘密和副作用风险立即恢复。
- 当前入口可能继续被文档、演示或后续代码当成可用基线，形成错误的安全预期。
- 若后续在旧执行边界上继续接入 CLI/Web/01D，统一 Supervisor 时会产生额外迁移和回归成本。
- 最小 3B-2 可能暂不包含真实 first-party Sink 和 1k/10k 容量基线，对过度设计与热路径性能的证据较弱。

## 选择 B 的具体风险

### 高风险：秘密泄漏和不可逆外部副作用

如果真实模型生成的代码、测试、依赖脚本或 Browser 子进程获得父环境，它可能读取并输出 Provider Key、代理或其他凭据。当前版本也没有默认断网和生产副作用隔离；一旦凭据进入日志、模型上下文或外部服务，后果可能不可逆。

### 高风险：取消后副作用继续

直接子进程结束不保证子孙进程、端口或后台任务被回收。Runtime 可能已经进入 cancelled/failed，而宿主仍在执行迟到操作，从而破坏状态、审计和副作用的一致性。

### 中风险：安全门禁依赖人工记忆

B 的可接受性依赖团队始终记住“只做纯本地 Outbox，不运行真实执行入口”。随着窗口切换、Agent 交接和演示需求变化，这种 prose-only 门禁容易被遗漏。

### 中风险：环境漂移和不可复现

父环境、PATH、工具安装和隐式变量可能让同一命令在不同机器或不同终端中表现不同。失败可能被误判为模型、Outbox 或 Runtime 问题，增加诊断成本。

### 中风险：后续接线返工

`3B-2` 本身与子进程边界耦合较低，因此立即返工风险有限；真正的返工会在 CLI/Web/`PROD-01D` 使用旧入口后快速上升。安全 Patch 越晚于主链接入，迁移面越大。

### 低到中风险：安全声明与事实不一致

如果 UI、README 或演示把当前入口笼统称为“安全执行”“沙箱”，用户会基于错误边界作出授权决定。即使没有实际事故，这也是产品和审计风险。

## B 在什么条件下仍然可接受

B 只有在以下门禁全部成立时才是可控的临时选择：

1. 只开发 `runtime_persistence`、Transport、Sink、ACK/Receipt 和对应确定性测试。
2. 不调用真实模型生成代码，不执行任何模型或用户提供的候选代码。
3. 不运行现有 Coding CLI、Legacy Workspace、VisionForge build/dev/browser 路径。
4. 不加载 `.env`，不使用生产秘密、外部账号、非 loopback 网络或真实外部副作用。
5. 不进入 `PROD-01D`，不把旧执行入口接到新的持久 Runtime 主链。
6. 所有文档明确标记“安全契约已冻结、实现未验收”，不得宣称生产沙箱或安全基线已完成。
7. 任一条件无法继续保证时立即停止 B，并切换到 `SEC-EXEC-01`。

这些门禁只能降低 B 的临时风险，不能替代安全实现本身。

## 当前建议

推荐选择 **A**，原因不是 `SEC-EXEC-01` 是 `3B-2` 的技术依赖，而是当前已知风险涉及秘密和可能不可逆的外部副作用；同时项目后续的 CLI/Web/`PROD-01D` 会显著扩大旧执行边界的影响面。

为避免 A 失控，范围必须严格冻结在 `local_trusted_execution/v1`：

- 只支持单用户、本人控制的 macOS/POSIX 本地可信任务；
- 统一已登记的进程入口、显式环境、可信 executable、deadline 和清理屏障；
- 完成秘密注入、命令漂移、路径越界、进程残留、输出限长与正常路径对照；
- 不扩展到容器、VM、独立 UID、生产 Secret Broker、多租户或敌对代码沙箱；
- 全部 A～H 门禁通过前，不再次运行真实模型生成代码或候选代码。

建议推进顺序：

```text
确认 Plan Amendment
  → 同步 Plan / Backlog / HANDOFF，并追加 clean checkpoint
  → 实现 SEC-EXEC-01 local_trusted_execution/v1
  → 独立攻击、正常路径回归和 VerificationReport 收口
  → 实施增强版 PROD-01B-3B-2
  → 继续 BudgetLedger、Acceptance 和查询恢复
```

## 决策记录

2026-08-25，用户正式选择方案 A。该决定只冻结顺序：

```text
SEC-EXEC-01
  → 增强版 01B-3B-2
  → BudgetLedger / Acceptance / 查询恢复
```

方案 B 保留为风险分析，不再是当前执行路径。此决定不表示 `SEC-EXEC-01` 已实现、A～H 已通过或 Runtime Acceptance 已签发，也不批准真实模型、真实秘密、外部网络或不可逆副作用。

## 关键决策原则

这个问题可提炼为后续批次通用的五条原则：

1. **技术上可独立，不代表可以安全发布或继续接线。**
2. **Prompt、约定和 Handoff 不能替代 Runtime 强制门禁。**
3. **涉及秘密或不可逆副作用时，错误后果的可逆性比短期开发速度更重要。**
4. **安全 Patch 必须有冻结边界，不能借机扩张成尚未立项的生产平台。**
5. **如果选择暂缓安全实现，必须同时冻结禁止路径、停止条件和下一道不可越过的硬门禁。**

## 相关事实来源

- [`HANDOFF.md`](HANDOFF.md)：当前窗口 Handoff、已选择 A 的接续摘要、`local_trusted_execution/v1` 契约和 A～H 验收。
- [`Plan/Plan26.md`](Plan/Plan26.md)：正式的 `PA-2026-08-25-SEC-EXEC-01-FIRST` 顺序 Amendment、当前生产顺序与 `PROD-01B-3B-2` 契约。
- [`OPTIMIZATION_BACKLOG.md`](OPTIMIZATION_BACKLOG.md)：当前批次状态和下一动作。
- [`VerificationReports/PROD-01B.md`](VerificationReports/PROD-01B.md)：3A/3B-1 的版本化证据、缺口和 3B-2 红卡要求。
- [`demo/coding_workflow/runtime_persistence/outbox.py`](demo/coding_workflow/runtime_persistence/outbox.py)：当前 claim/NACK/reclaim 生命周期实现。
- [`demo/coding_agent_cli.py`](demo/coding_agent_cli.py)、[`demo/coding_workflow/workspace.py`](demo/coding_workflow/workspace.py)、[`demo/coding_workflow/visionforge/browser.py`](demo/coding_workflow/visionforge/browser.py)：当前执行边界的主要证据。
