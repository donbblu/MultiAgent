# Plan27：Harness 评测驱动演进、本地可信执行边界与 Skill 候选治理

日期：2026-08-24

讨论主题：在保持 `PROD-01B → 01C → 01D → 01E` 主线不变的前提下，冻结 Harness 行为优化的证据协议、当前本地子进程安全边界，以及未来重复经验生成 Skill 候选的治理方式。

## 目标与背景

当前项目已经具备多个 Agent、DAG、Memory、模型路由、Validator、多模态和 277 项默认单元测试，但仍存在三类容易误导后续开发的问题：

1. 增加 Agent、Prompt、Memory、路由或重试机制后，缺少隔离对照与 Held-out，无法判断提升来自 Harness、模型差异、样本过拟合还是偶然成功；
2. 子进程执行入口的环境继承、命令门禁和回收语义不一致，现有宿主进程执行容易被误述为安全沙箱；
3. 重复问题是否应该自动沉淀为 Skill 尚无治理边界，自动激活可能把错误经验永久传播；
4. “Runtime 是项目本体”的简称会漏掉编排、Context/Memory、Eval、Security、Incident 和 Plugin 治理，需要统一 Harness、Runtime Kernel、Plugin 和 Model 的术语层级。

本计划只归档本次形成的策略，不实现新的 Runtime 行为，不调用真实模型，不访问外部仓库，不安装 Evo-Bench 或 `evo-hq/evo`，也不改变当前生产批次顺序。

## 候选方案对比

### Harness 演进方式

| 方案 | 核心思路 | 优点 | 缺点、成本与风险 | 适用条件 |
|---|---|---|---|---|
| 依靠直觉持续增加机制 | 看到失败就增加 Agent、Prompt、Memory、Reviewer 或重试 | 推进快、前期文档少 | 无法归因，容易过拟合；脚本结果可能被误写成真实模型收益 | 只适合不可发布的探索草案 |
| L1 人工评测驱动演进 | 人工提出可证伪假设，每次只改变一个主要机制，通过冻结 Baseline、Validation 和独立门禁决定保留或回滚 | 当前基础设施即可执行，权力边界清晰，证据可审计 | 需要设计任务集、固定变量和报告；小样本时只能给出有限结论 | 当前阶段 |
| L2 Agent 辅助演进 | Agent 生成 ChangeProposal 或候选 Patch，独立 Eval Runtime 评测 | 可提高候选探索效率 | 依赖持久实验索引、Backend、隔离执行、Handoff 和发布验证；存在越权与评测泄漏风险 | PROD-01B～04 与 INC-03 完成后 |
| L3 生产自主演进 | 系统自动修改、验证并晋升生产 Harness | 闭环速度高 | 风险最高；需要完整 Shadow/Canary/Rollback、审批、退役和事故运营 | INC-03～05 成熟后重新立项，当前非目标 |

### 项目术语与产品边界

| 方案 | 核心思路 | 优点 | 缺点、成本与风险 | 适用条件 |
|---|---|---|---|---|
| 把 Runtime 称为整个项目 | 用 Thread、Invocation、状态和恢复概括产品 | 简短，突出执行内核 | 会遗漏编排、Context/Memory、工具、评测、安全、事故学习和 Skill，容易再次把内核当成产品全部 | 只适合描述执行控制层 |
| Harness 是项目本体，Runtime 是执行内核 | Harness 组合并治理 Runtime、Agent、Context、工具、Eval、Security、Incident 与 Plugin | 产品边界完整，并保持 Runtime 的权威控制职责 | 需要同步 README、FAQ、Plan 和历史表述 | 当前项目定位 |

### 当前执行安全边界

| 方案 | 核心思路 | 优点 | 缺点、成本与风险 | 适用条件 |
|---|---|---|---|---|
| 维持三个 Runner 各自实现 | 保留当前环境、timeout 和清理差异 | 无迁移成本 | 容易泄露父环境、出现旁路和残留进程，验收口径不一致 | 不再接受 |
| 把现有宿主进程描述为 Sandbox | 仅依赖 cwd、路径检查和进程组 | 对外表述简单 | 不具备 UID、文件系统、网络或资源隔离，属于虚假安全声明 | 禁止采用 |
| `local_trusted_execution/v1` | 在可信本机和可信仓库前提下，统一最小环境、绝对 executable/完整 argv、deadline、输出上限和清理屏障 | 能降低当前已知风险，适合个人演示，实施范围可控 | 仍是同 UID 宿主进程；不能防御恶意依赖、网络、秘密或进程组逃逸 | 当前个人本地可信任务 |
| 生产 Sandbox | 低权限身份或 rootless 容器、文件系统 containment、默认断网、配额、Secret Broker 和持久回收 | 可支持陌生代码、多用户和真实秘密 | 平台与运维成本高，依赖 PROD-01C/02/03 | 风险边界越过本地可信模式时 |

### 重复经验的 Skill 落点

| 方案 | 核心思路 | 优点 | 缺点、成本与风险 | 适用条件 |
|---|---|---|---|---|
| 自动生成并激活 Skill | 模型发现重复模式后直接改变 Runtime 行为 | 自动化程度高 | 推测可能变事实，错误经验会扩散，缺少审批和退役 | 禁止采用 |
| 只读 Skill 候选箱 | 从持久事件和验证证据生成 `PROPOSED` 候选，人工审批后再进行 Offline Eval、独立 Review 和 Shadow | 可审计、可拒绝、可退役，保留人工控制 | 依赖 Journal、Incident/LearningStore 与 INC-04；实现较晚 | PROD-05/INC-04 |
| 完全不做 Skill 沉淀 | 所有重复问题由人工处理 | 简单、安全 | 无法积累可复用认知流程 | 只适合早期临时阶段 |

## 最终选择

1. 当前采用 **L1 人工评测驱动演进**。所有会改变 Agent 行为的 Harness 修改使用 `Baseline → 失败证据 → 可证伪假设 → 单一 Mutation → Validation → Held-out 或确定性故障矩阵 → KEEP/ROLLBACK/INCONCLUSIVE`。L2 保留为后续受控候选能力，L3 明确为当前非目标。
2. 当前子进程边界采用 **`local_trusted_execution/v1` 契约**，但状态仅为“边界与验收口径已冻结，代码未验收”。它只能处理本人控制的可信仓库和可丢弃 Workspace，不得称为生产安全沙箱。
3. Skill 采用 **延后的只读候选箱**。系统未来只能自动产生 `LearningItem(kind=skill, status=PROPOSED)` 或等价投影，不能直接激活；该能力不插队 PROD-01B，等待 PROD-05/INC-04。
4. 固定术语为 **Harness 是项目本体、Runtime Kernel 是执行内核、Plugin 是专业能力、Model/Backend 是可替换负载**。术语澄清不修改既有领域模型或 PROD 顺序。
5. 当前推进顺序保持 `PROD-01B → 01C → 01D → 01E`。本次策略都是跨批次门禁、产品边界或后续提醒，不是新的插队生产子系统。

## 选择理由

- L1 能用当前固定任务、Hidden Validator 和脚本化消融资产建立最小证据纪律，同时承认 3 个任务和 Fake Model 不足以证明真实模型泛化收益。
- 独立 Eval 层必须冻结最终 Oracle、权限、预算、分母和 Held-out；否则被测 Harness 可以通过修改裁判获得虚假提升。
- `local_trusted_execution/v1` 如实反映当前个人本地使用场景，既不忽略已有环境继承与清理缺口，也不在没有需求和平台证据时提前引入完整容器平台。
- Skill 候选需要跨任务持久证据、去重、正反例、人工审批、版本和退役；这些前置能力尚未落地，延后比制作无事实源的演示更可靠。
- Harness/Runtime 分层能同时保留“Runtime 独占状态、权限和 Acceptance 强制执行”与“Harness 还负责编排、上下文、评测和治理”两项事实，避免术语再次反向收窄产品。

## 架构或流程

### Harness Evolution

```text
Failure / Workload / Incident Evidence
  → freeze Baseline, mutation_axis and manifests
  → one falsifiable Hypothesis and one primary Mutation
  → run complete Validation trials
  → safety lexicographic gates
  → freeze the best historical candidate
  → sealed Held-out once, or deterministic N/A track
  → KEEP / ROLLBACK / INCONCLUSIVE / INVALID
  → Regression / Detector / Policy / Validator / Adapter / Runbook / Skill
```

Policy Agent、Evolver 和 Eval 使用不同 principal；涉及连续性时使用不同 AgentSession。最终 EvalOracle、EvalAcceptancePolicy、HiddenValidator、安全边界、BudgetLedger、计分和完整分母不可被候选 Harness 修改。

### 本地可信执行

```text
trusted_local admission + input/profile digest
  → versioned Environment/Command Profile
  → absolute executable + exact argv + private HOME/TMPDIR
  → supervised process group
  → success/failure/timeout/cancel/readiness failure
  → TERM → grace → KILL → wait/reap → owned resource check
  → success result or CLEANUP_FAILED / SANDBOX_REQUIRED
```

### Skill 候选箱

```text
RuntimeEvent / Verification / Incident / human correction
  → stable versioned fingerprint
  → at least 3 independent real tasks/incidents in one Scope
  → at least 1 verified successful example
  → classify correct landing place
  → PROPOSED Skill candidate
  → human approval → Offline Eval → independent Review → Shadow
  → Active / Rejected / Superseded / Retired
```

重试、Replay、Shadow 和故障注入不重复计数；确定性禁止项归 Runtime/Policy/Validator，重现缺陷归 Regression，Provider 差异归 Adapter，人工操作归 Runbook，只有重复且依赖判断的认知流程才适合作为 Skill。

## 执行步骤

1. 后续所有影响 Agent 行为的 Plan 增加 Harness Evolution 实验小节，预注册 Baseline、证据、单一 Mutation、固定项、门禁、代价和决策。
2. PROD-01B 走确定性轻量轨：Held-out、Evolver principal、样本量和统计效果标记 `N/A`，使用事务中断故障矩阵、正常路径和回归决定 KEEP/ROLLBACK/INCONCLUSIVE，不得阻塞基础设施开发。
3. 用户明确启动本地安全 Patch 后，再统一 ControlledCommandRunner、BrowserProcessRunner 和 Legacy ProjectWorkspace.run；在 A～H 验收全部通过前保持“契约冻结、实现未验收”。
4. PROD-01B/01E 提供跨任务 Journal、查询、事故指纹和证据后，PROD-05/INC-04 再设计 LearningStore 和 Skill Candidates API/Web。
5. 第一次真实 Harness Evolution Pilot 必须重新获得模型调用授权，并单独冻结任务数量、模型、预算、重复次数和 sealed held-out cohort。

## 约束与风险

- 不得把内部 Harness Evolution Experiment 称为已复现官方 Evo-Bench，也不得把 `evo-hq/evo` 描述为已安装或已集成。
- Fake Model、脚本和单元测试只证明控制流或确定性协议，不能冒充真实模型或生产收益。
- Held-out 一旦向人工或 Evolver 暴露，该 cohort 对后续调参立即退役；修改阈值、分母、样本或删除失败 Trial 会使实验 `INVALID`。
- 安全硬门禁采用字典序，不能用平均质量提升抵消 false accepted、越权、污染、重复副作用、迟到接纳、预算突破或评测泄漏。
- `local_trusted_execution/v1` 不提供低权限 UID/GID、OS 文件系统 containment、默认断网、硬资源配额、Secret Broker 或恶意依赖防护；越界时必须返回 `SANDBOX_REQUIRED`。
- Skill 候选不能直接改变 Active Runtime；人工批准只进入草案和 Offline Eval，仍需独立 Review、Shadow 和退役治理。
- 本次不授权真实模型、网络、媒体、外部仓库、秘密、宿主提权或不可逆副作用。

## 待验证事项

- PROD-01B 的状态表、Journal、Outbox 和 BudgetLedger 能否在 SQLite 单事务下保持原子、一致和可恢复。
- `local_trusted_execution/v1` 的环境 allowlist 是否兼容所有受控工具，A～H 负向/正常对照能否无旁路通过。
- 第一次真实 Harness Evolution Pilot 的样本量、重复次数和 sealed held-out 是否足以形成有限但可信的泛化结论。
- 跨任务事件和事故证据能否稳定生成 Skill 指纹，并有效区分 Skill、Regression、Policy、Validator、Adapter 和 Runbook。
- 评测报告能否完整保留失败、缺失、重试、用量、配置 hash 和 Evidence，而不是只展示最佳 Run。

## 待办事项

- 继续实施 PROD-01B，不因本计划改变当前优先级。
- 在下一份适用 Plan 中使用确定性轻量 Harness Evolution 模板，并记录明确的 KEEP/ROLLBACK/INCONCLUSIVE。
- 用户重新授权后再实施 `local_trusted_execution/v1`，完成统一 Supervisor、Profile、输出脱敏和 A～H 验收。
- 在 PROD-05/INC-04 前重新评估 `PROD-05-SKILL-CANDIDATE-INBOX`，未经确认不得提前激活。
- 在建立真实模型 Pilot 前扩充任务家族和对人工 Evolver 密封的 held-out；当前 3 个 Coding 任务只作为管线校准资产。
