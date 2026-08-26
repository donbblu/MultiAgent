# SEC-EXEC-01 VerificationReport

> 本文件是 `local_trusted_execution/v1` 的 EXPECTED_RED、实现、攻击、回归和决定证据入口。`Plan` 与 `HANDOFF` 定义应当实现什么，测试提供可执行 Oracle，本报告只记录实际发生了什么。当前状态不是安全验收或 Runtime Acceptance。

## 0. 报告身份

| 字段 | 值 |
|---|---|
| `report_schema` | `verification-report/v1` |
| `report_id` | `VR-SEC-EXEC-01` |
| `created_at` | `2026-08-25` |
| `last_updated` | `2026-08-26` |
| `contract_ref` | [`HANDOFF.md`](../HANDOFF.md) 的 `local_trusted_execution/v1` A～H |
| `plan_amendment_ref` | [`PA-2026-08-25-SEC-EXEC-01-FIRST`](../Plan/Plan26.md) |
| `decision_ref` | [`SecurityProblem.md`](../SecurityProblem.md) |
| `step_log_ref` | [`Project Step Log`](STEP-LOG.md) 的 `SEC-HIST-001`～`SEC-HIST-019` 与 `TRACE-20260826-*` |
| `evidence_status` | `EXPECTED_RED_ONLY` |
| `lifecycle_status` | `FROZEN` |
| `decision` | `INCONCLUSIVE` |
| `runtime_acceptance` | `NOT_ISSUED` |

`EXPECTED_RED_ONLY` 只表示预先冻结的测试准确证明能力尚未实现。它不是产品事故、回归、通过证据或 `KEEP` 依据。

## 1. 变更与非变更边界

用户于 2026-08-25 正式选择方案 A，当前顺序固定为：

```text
SEC-EXEC-01
  → 增强版 PROD-01B-3B-2
  → BudgetLedger / Runtime-only Acceptance / 查询恢复
```

本切片只建立单用户、本人控制的 macOS/POSIX **可信本地执行**基线：版本化 Command/Environment Profile、可信绝对 executable、`trusted_local` admission、同一进程组监督、同步清理屏障、Workspace 路径门禁、输出限长与脱敏。

本切片明确不承诺：

- 容器、VM、独立 UID/GID 或 OS 文件系统 containment；
- 默认断网、CPU/内存/PID/磁盘硬配额或生产 Secret Broker；
- 多租户、陌生/恶意依赖、敌对代码、`setsid`/double-fork/daemon 逃逸；
- Supervisor 崩溃后的持久 Reaper、真实外部副作用或生产 Sandbox；
- 真实模型调用、真实秘密、非 loopback 网络或依赖安装。

完成 A～H 也只能得到上述受限范围的 Verification/`KEEP`，不能生成 Runtime Acceptance。

## 2. 当前 subject

| 字段 | 值 |
|---|---|
| branch | `main` |
| HEAD | `f66e71e02c206dd361f18f58f669824ae7de6cab` |
| production/tests vs HEAD before red card | clean |
| worktree | dirty；包含已批准 Amendment、交接/说明文档、既有问题文档整理和本红卡；精确状态以 `git status` 为准 |
| OS | macOS `26.5` / arm64 |
| Python | `3.9.6`，`/usr/bin/python3` |
| model/network/real secret | 均未使用 |

冻结文件哈希：

| 文件 | SHA-256 |
|---|---|
| `demo/tests/test_local_trusted_execution_expected_red.py` | `294c53d8194af9e7ae6d6e5324d5fd2bcb0a5bef8ec7547aee4d9fc69baf08da` |
| `demo/tests/test_local_trusted_execution_behavior_expected_red.py` | `63cb6660e72312e0ee3e085056566966ce3e725e191b6ff79001fa13aaf4474d` |
| `demo/coding_workflow/command_validators.py` | `948880b67e310f28911f3649886c80cd64de8642b6c656dadf78fb8be7f8dfa9` |
| `demo/coding_workflow/workspace.py` | `e01c622e0b8becf6d0a536a66fd806252df0d9662a1005767ab3034189359698` |
| `demo/coding_workflow/visionforge/browser.py` | `0e1d67211d04e142b6c766c123c2cdbb11065764405c7402f35c520756154fe5` |
| `demo/coding_workflow/policy.py` | `056655a83b046b9d72c7bf72ac7fb11980c450f17e05c1bf2cbdf2f8cf68062f` |
| `demo/coding_agent_cli.py` | `83b8412f16ecd45bf6ba1e02086d2aa162f61e51715c51abee967e8f77211580` |

## 3. 首轮 A～H 结构红卡

首轮只冻结每个门禁的第一个可线性化缺口，并保证 unittest 能发现全部 8 项；它不会用八个结构断言冒充完整安全验收。完整行为、POSIX 进程和正常路径 Oracle 同时冻结在下表，必须在最终 `KEEP` 前全部转成可执行证据。

| 门禁 | 首轮测试 | 当前 EXPECTED_RED 签名 | 最终必须补齐的行为 Oracle |
|---|---|---|---|
| A / admission | `test_a_admission_contract_is_public_and_runtime_owned` | Runtime scope 缺少版本、`trusted_local`、`SANDBOX_REQUIRED` 及 input/profile digest 协议标记 | 缺失、dict 伪造、模型来源、过期、Workspace/input/profile digest 漂移和越界需求都在 spawn 前返回 `SANDBOX_REQUIRED`，spawn/PID/副作用为 0；只有 Composition Root 绑定的合法确认可启动一次 |
| B / 环境、FD、秘密 | `test_b_entrypoints_do_not_inherit_parent_environment` | Legacy 隐式继承；Browser 复制父环境；Core 可任意扩展环境 | 五个前台/后台入口的初始继承环境只含 Profile；父 sentinel/fake key/proxy/SSH/注入变量命中 0；`stdin=DEVNULL`、`close_fds=True`、登记外 FD 为 0；HOME/TMPDIR 每次唯一、0700，返回后不存在 |
| C / 命令 Profile | `test_c_only_absolute_registered_profile_reaches_spawn` | 版本/profile digest 协议缺失；Legacy 将 basename 交给 spawn | 只有 Composition Root 解析并登记的绝对 executable、完整 argv、cwd、env 和 digest 可启动；字段缺失/漂移、参数变化、Workspace 同名 executable、放宽 deadline/output 均零 spawn；调用方只可收紧 |
| D / Workspace 路径 | `test_d_workspace_api_rejects_reserved_paths_and_symlink_escape` | `.env/.git/.runtime/.runs/.verification/.harness-hidden-tests/solution` 均被接受 | Harness 文件 API 与 Browser cwd/log/spec/result/screenshot 对绝对路径、`..`、保留路径和 symlink escape 全部在写入/spawn 前拒绝；外部 canary 不变；对子进程只声明 cwd，不宣称 OS containment |
| E / 生命周期 | `test_e_cleanup_failure_and_quarantine_are_typed` | `CLEANUP_FAILED`、`SANDBOX_REQUIRED` 与 quarantine 生命周期协议缺失 | success/nonzero/timeout/cancel/异常/background stop/readiness failure 全部进入 `TERM→grace→KILL→wait/reap→核对`；失败返回 `CLEANUP_FAILED` 并隔离 Workspace，人工解除前下一次零 spawn |
| F / 输出边界 | `test_f_result_representations_never_retain_raw_secret_text` | Core raw assertion、Browser 和 Legacy Result 的 repr/to_dict 保留 fake secret | stdout/stderr/server log 统一使用 head+marker+tail、原长度、原文 SHA-256 与脱敏；所有 Result/repr/Artifact/Event/SQLite/下一轮输入中 fake sentinel 明文命中 0；不外推为 OS 内存硬配额 |
| G / 正常对照 | `test_g_frozen_profile_manifest_has_all_normal_controls` | 版本与五个冻结 Profile ID 均未进入 Runtime manifest | `core_validator/legacy_workspace_verify/visionforge_build/visionforge_dev/visionforge_browser` 的 executable/argv/deadline/grace/barrier/output 与 digest 精确；原有 success/failure/timeout/cancel/Artifact 字段保持兼容；真实 Browser 对照独立运行 |
| H / 无旁路 | `test_h_no_process_entrypoint_bypasses_the_supervisor` | 三个 legacy 入口文件仍有 4 个直接调用：Core 1、Legacy 1、Browser 2 | 声明范围只允许统一 Supervisor backend 的一个 `Popen` owner，`subprocess.run` owner 为 0；AST scanner 也能识别 import alias；test-only 跨进程夹具必须列入 manifest，不误判为生产入口 |

### 3.1 契约澄清

门禁 B 检查的是 **exec 时从父进程继承的初始环境**。`pnpm/node` 等已登记可信工具在 exec 后自行生成的内部变量必须进入正常路径 manifest，但不误算为父环境继承；它们仍不得包含父 sentinel 或秘密。否则“child 中任何未列变量为 0”的字面要求会让合法工具路径永远无法通过。

`trusted_local` 不是模型可提交的 bool 或普通 dict。最小实现由用户在入口确认信任范围，Composition Root 在 spawn 前签发/构造绑定当前 Workspace digest、input digest、Profile digest、期限和来源的 opaque 值对象；缺失、来源为模型或任一 digest 漂移都 fail-closed。

为使“合法确认只启动一次”可在不暴露内部类层次的情况下形成黑盒 Oracle，独立预审批准冻结唯一最小 admission seam：包根 `issue_trusted_local_confirmation(*, workspace_digest, input_digest, profile_digest, expires_at_monotonic)`，以及四个现有直接进程入口可缺省的 `trusted_local=` 关键字。合法请求只缺确认时，结构化 `SANDBOX_REQUIRED` 的 `confirmation_request` 映射必须且只含 Runtime 实际计算的 `{workspace_digest,input_digest,profile_digest}`；测试与 Composition Root 用这份 challenge 调 issuer 后原样重试，不冻结 digest 的私有 preimage 或 canonical JSON。非法 argv、路径、网络、秘密或副作用请求不得获得 challenge。issuer token 类型与内部字段、Supervisor/Profile 类名和实现文件均不属于契约；token 必须 opaque、一次性且不能由 bool/dict/JSON/模型 payload 重建。缺失/伪造/过期/漂移均为 `SANDBOX_REQUIRED`。

`CLEANUP_FAILED` 只用于 spawn 后无法证明清理完成，并须返回 Runtime 生成的 quarantine ID/generation、结构化 `cleanup_evidence` 与 `cleanup_evidence_digest`。黑盒恢复冻结为两阶段 operator seam：`request_local_execution_recovery(*, quarantine_id)` 只有在重新证明全部 owned 资源消失后，才返回只含 `quarantine_id/quarantine_generation/workspace_digest/input_digest/profile_digest/cleanup_evidence_digest/recovery_evidence_digest` 的 `recovery_request`；调用方把其中三个通用 digest 交给既有 issuer，再用所得 token 调 `recover_local_execution_quarantine(*, quarantine_id, recovery_confirmation)`。recover 必须在同一线性化临界区再次核对 owned resources、当前 generation 和 evidence digest；错误 ID、资源仍存活或并发再出现、过期/复用 token、证据或 generation 漂移都必须保持 quarantine。Admission/recovery token 必须域分离，跨协议误用一律拒绝；成功后才允许相同 Workspace 的新鲜 admission 正常 spawn。两个恢复函数只供 Composition Root/operator 使用，不得暴露无授权的裸 clear，也不冻结 evidence 的私有 canonical preimage。

## 4. EXPECTED_RED 运行

独立红卡审查发现，未冻结候选曾把一组具体 Python 类名和 factory 名称误当成能力要求；该候选属于测试设计缺陷，已在正式冻结前撤销。当前红卡只固定协议、入口和行为边界，允许实现自由选择模块、类与方法名称。

### `SEC-RED-20260825-001`

在 `demo/` 执行：

```bash
/usr/bin/env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 LC_ALL=C.UTF-8 HOME=/private/tmp/multiagent-sec-red-home TMPDIR=/private/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 /usr/bin/python3 -m unittest tests.test_local_trusted_execution_expected_red -v
```

结果：发现并执行 8 项，`8 failures / 0 errors / 0 skipped`，耗时 `0.012s`，exit code `1`。A～H 每项各产生一个预注册失败，签名与第 3 节一致；没有导入阶段短路、skip 或 `expectedFailure`。

这 8 项是 `EXPECTED_RED`，不是 8 个产品缺陷。若已 KEEP 的旧测试转红，必须单独登记真实 regression，不能并入红卡计数。

### 4.1 独立红卡预审

| 字段 | 值 |
|---|---|
| reviewer principal | `/root/sec_option_a_review` |
| independence | 只读；未参与候选修改 |
| recommendation | `approve` |
| blocking findings | `0` |

首轮审查曾指出两项阻塞测试设计问题：3B-1 checkpoint 过度声称当前 `Plan26.md` 仍匹配历史哈希，以及安全红卡把具体 Python API 名称误冻结为能力。两项均在复审前修正；复审核对当前测试哈希、8 项 EXPECTED_RED、101 项正常基线、报告链接和 H 的结构卡范围后批准。这个批准只允许进入生产实现，不是 `SEC-EXEC-01` 最终安全 Review、`KEEP` 或 Runtime Acceptance。

### 4.2 Mock A～H 行为红卡定版

行为红卡必须在 **全新专用解释器中先于其他项目模块加载**。测试安装进程级 audit backstop、suite-level tripwire 和受跟踪的线程/Task/本地 IPC；每项产品入口仍由 Fake Process/Signal/Network 边界接管。该护栏只防止红卡误触真实进程、INET 网络或信号，不是 Python/native-extension 或 OS 沙箱。

冻结文件：

| 文件 | SHA-256 |
|---|---|
| `demo/tests/test_local_trusted_execution_behavior_expected_red.py` | `63cb6660e72312e0ee3e085056566966ce3e725e191b6ff79001fa13aaf4474d` |

2026-08-26 在清空父环境、`PYTHONWARNINGS=error` 的 fresh Python 3.9.6 解释器中执行两次，结果均为 `17 tests / 17 failures / 0 errors / 0 skipped`；退出时仅 `MainThread`，`active_audit=None`，retained tripwire stack 为 0。按 behavior-first 顺序与 8 项结构卡合并运行，结果为 `25 tests / 25 failures / 0 errors / 0 skipped`。`py_compile`、AST 形状检查和 `git diff --check` 均通过；17 个测试各只有一个末尾 `assertEqual(violations, [])`，没有 skip 或 `expectedFailure`。

独立只读定版审查 principal 为 `/root/sec_option_a_review`，锁定上述哈希后结论为 `APPROVE`、blocking finding 0。审查明确核对 admission 的跨入口一次性、非法请求零 challenge、Profile/output-limit 漂移、Workspace/Browser 路径、cleanup/quarantine/recovery、持久写历史与下游脱敏、旧 Result/Artifact 兼容、全 `demo` 进程入口扫描和 test-only manifest。该批准只允许把 **mock 行为 EXPECTED_RED** 保存为后续生产实现 Oracle；真实 PID/PGID/port/handle/marker 消失仍由 POSIX 卡证明，也不构成 `SEC-EXEC-01` `KEEP`、最终安全 Review 或 Runtime Acceptance。

### 4.3 POSIX 夹具安全预检（未批准 workload）

为后续 adversarial 卡准备的候选夹具当前哈希为：

| 文件 | SHA-256 |
|---|---|
| `demo/tests/_local_execution_posix.py` | `a00978afa4df611fe20df30abea4cb6d106583c6c555c3ca944cebbfadbc3451` |
| `demo/tests/fixtures/local_execution_process.py` | `034eea969031f6493e9d5dba5537673a491a50232e2d94ca42e327d33e65077f` |
| `demo/tests/test_local_execution_posix_safety.py` | `f0a90bb1a67d26e602986b2d05e334bfa2639818af03742064c1482b08290080` |

纯 mock/direct 安全卡执行 `21 tests`，全部通过，0 failure/error/skip；它覆盖 idle deadline、arm ACK、ACK 后重新起算 target deadline、grandchild start gate、PID/PGID/SID 漂移拒绝、close 幂等和 watchdog join。独立复审仍给出 `REVISE`：顶层 `Popen` 成功到 leader 自登记之间，watchdog 尚未取得稳定 PID；此外 emergency-stop 与 join 同时失败时，`close()` 仍可能在活 watchdog 存在时抛错。因此 **不得运行 `success_orphan`、端口、崩溃或任何真实 workload smoke**，也不得把 21 项 mock 结果写成 POSIX 生命周期已验收。macOS/Python 3.9 缺少 pidfd 导致的 `getpgid/getsid → killpg` 极短竞态，在可信单用户、非敌对 PID reuse 范围内可登记为残余风险，但不能表述为形式化消除。

复审只允许 watchdog-only、未 arm 的最小生命周期检查。2026-08-26 在清空父环境后执行该检查：watchdog PID `90751` 正常退出并被 join，`result.clean=true`，`target_spawn=0`，随后才删除临时根。这个结果只证明 watchdog 自身的无目标关闭路径，不解除上述两个 workload blocker。

## 5. 既有正常路径对照

### `SEC-BASELINE-20260825-001`

同样在清空父环境后执行：

```bash
/usr/bin/env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 LC_ALL=C.UTF-8 HOME=/private/tmp/multiagent-sec-baseline-home TMPDIR=/private/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 /usr/bin/python3 -m unittest tests.test_command_validators tests.test_workflow tests.test_visionforge_browser tests.test_visionforge_runner tests.test_visionforge_rework tests.test_visionforge_evaluation -q
```

结果：`run=101`，其中 97 项通过、4 个真实 Browser E2E 按设计跳过，0 failure/error，耗时 `1.767s`。这证明红卡没有破坏既有安全相关可信基线；它不证明 `SEC-EXEC-01` 已实现。

2026-08-26 在 v7 行为红卡定版后，以同一模块集合和清空父环境重新执行：`run=101`，97 pass、4 skip、0 failure/error，耗时 `1.691s`。POSIX 纯 mock/direct 安全卡同时重跑 `21/21` 通过；两项都不解除 4.3 节的真实 workload 禁令。

### 5.1 Step Log 索引与 2026-08-26 可复制重跑

本报告保留本切片的权威验证结论；逐步候选链、修订理由、实际效果、缺失证据和 checkpoint 状态统一索引在 [`STEP-LOG.md`](STEP-LOG.md)：

- `SEC-HIST-001`～`004`：Option A、契约与结构红卡 draft/final；
- `SEC-HIST-005`～`012`：behavior 红卡 8 个候选及每轮 `REVISE/APPROVE`；
- `SEC-HIST-013`～`017`：POSIX helper/fixture、安全卡与 watchdog-only smoke；
- `SEC-HIST-018`～`019`：正常路径 baseline 与当前文档/checkpoint 边界；
- `TRACE-20260826-002`：以隔离父环境重新执行 structural、behavior、combined、POSIX mock safety 和 baseline 的精确 cwd/命令/exit/计数/耗时。

历史回填没有保存的原始 runner 或命令明确标为 `MISSING/UNKNOWN`，不得从摘要补造。当前 Step Log、SEC report 与红卡仍是 `WORKTREE_ONLY`，尚无本批 Git commit；因此它们是 content-hash checkpoint，不是 clean release checkpoint。

## 6. 后续实现与验证顺序

1. 结构红卡与 mock A～H 行为红卡均已独立预审完成，结论 `approve`、blocking finding 0；这只解除进入生产实现的前置阻塞。
2. 下一小批新增单一 Supervisor/Profile/Admission 实现，并让 Core、Legacy、Browser 三类入口全部委托；保持旧 Result/Artifact 字段兼容，先让结构与 mock 行为卡转绿。
3. POSIX fixture 在顶层 child 稳定归属和 watchdog 必须 join 两个 blocker 关闭前继续禁止真实 workload；不得用 mock 结果替代真实 PID/PGID/port/marker Oracle。
4. 首绿并修复 fixture blocker 后新增独立 POSIX adversarial 卡，覆盖同 PGID child/grandchild、TERM→KILL、cleanup audit、Workspace quarantine、loopback port 和 background readiness；不覆盖已明确非目标的 daemon/setsid 逃逸。
5. 定向门禁、真实 Browser 正常对照、全量回归、compileall、静态 no-bypass 和 `git diff --check` 全部通过后，再进行独立最终 Review。
6. 只有 required 门禁全部通过且 blocking finding 为 0，才能决定 `KEEP (local_trusted_execution/v1 only)`；否则必须 `ROLLBACK` 或 `INCONCLUSIVE`。

## 7. Harness Evolution / INC 联动

- `lifecycle_status=FROZEN`，`decision=INCONCLUSIVE`；当前 mutation 只允许统一本地执行边界，不同时改变模型、Prompt、路由或 Outbox。
- 真实模型、Evolver、Validation/Held-out、query budget、样本量和统计效果为 `N/A`；本批证明确定性安全边界，不主张模型能力提升。
- 风险目录增加父环境秘密继承、错误 executable、reserved/symlink escape、cleanup failure、残留进程/端口、raw output secret 和未登记 subprocess bypass。
- 当前只有开发期 EXPECTED_RED 和正常对照；没有生产 Detector、IncidentSignal/Ledger、Replay、MTTD/MTTR，也不提前完成 `INC-01`。
- 所有 sentinel 都是明确标记的假值；未读取 `.env`，未调用真实模型、外部网络或外部服务。
