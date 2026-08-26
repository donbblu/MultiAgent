# Project Step Log

> `schema=project-step-log/v1`。本文件是项目级、追加式的过程证据索引，回答每个逻辑步骤“做了什么、为什么做、预期与实际效果、证据在哪里、审查后如何处置”。它不记录私密推理，也不替代 Plan、VerificationReport、Harness Evolution Experiment、Incident/RuntimeEvent 或 Runtime Acceptance。

## 0. 证据优先级与当前状态

发生冲突时，优先级固定为：**匹配 SHA-256/commit 的原始 Artifact 与测试输出 > 对应 VerificationReport > 本 Step Log 摘要 > HANDOFF**。发现冲突只能在文件末尾追加 `CORRECTION`，不得就地改写旧条目。

| 字段 | 当前值 |
|---|---|
| `created_at` | `2026-08-26T10:30:19+08:00` |
| `base_branch` | `main` |
| `base_head` | `f66e71e02c206dd361f18f58f669824ae7de6cab` |
| `checkpoint_status` | `WORKTREE_ONLY` |
| `git_checkpoint` | `PENDING — 当前 Amendment、SEC 文档与红卡仍在 dirty/untracked 工作树中，尚无本批 commit` |
| `current_slice` | `SEC-EXEC-01` |
| `current_gate` | `EXPECTED_RED 已冻结；生产 Profile/Admission/Supervisor 尚未实现；POSIX workload 仍被禁止` |

## 1. 后续强制记录协议

从 `TRACE-20260826-001` 起，本文件只允许在末尾追加。一个明确目标的命令组可以作为一个逻辑步骤；纯只读导航可合并记录，但任何会改变范围、判断、实现、验证结果、Review disposition 或 Git checkpoint 的动作必须单独入账。

1. 修改契约、Oracle、生产代码，或运行会影响决策/带副作用的实验前，先追加 `PRE_REGISTER`。
2. 动作完成后、进入下一状态前追加 `ACTUAL`；失败、部分完成、重试和撤回也必须记录。
3. 独立审查结果单独追加 `REVIEW`；`REVISE/REJECT/PENDING` 不得据此收口。
4. 每个里程碑追加 `CHECKPOINT`，记录 content hash、`git status --short`、commit SHA（若存在）和 commit 后状态。
5. 纠错只追加 `CORRECTION` 并填写 `supersedes_entry_id`；候选迭代是新的 `ACTUAL`，不得删除失败历史。
6. 不得留空或写“同上”。不适用写 `N/A — 原因；后续归属`；预注册可写 `TBD — owner + 冻结时点`，`ACTUAL` 不得保留 `TBD`。
7. 历史缺口写 `MISSING/UNKNOWN — 原始证据未保存，禁止补造`；回填条目标记 `HISTORICAL_RECONSTRUCTION` 并同时保留实际 `recorded_at`。
8. 命令必须记录 cwd、清理过的完整命令、exit code、pass/fail/error/skip、耗时和关键输出；不得把秘密值写入日志。必须区分 mock/fake、loopback、真实外部资源和未运行。
9. 每个 `ACTUAL` 至少记录 base HEAD、相关 dirty scope、变更 Artifact SHA-256、证据引用、实际效果、剩余风险和下一步。
10. Step Log 的 `REVIEW` 不是 `KEEP` 或 Runtime Acceptance；未提交的 content checkpoint 也不得表述成可从 Git commit 复现的 release checkpoint。

固定 entry 类型：`PRE_REGISTER | ACTUAL | REVIEW | CORRECTION | CHECKPOINT | PROTOCOL_CHANGE`。固定审查处置：`NOT_REQUESTED | PENDING | APPROVE | APPROVE_WITH_NOTES | REVISE | REJECT`。

## 2. 历史回填边界

下列 `SEC-HIST-*` 条目于 2026-08-26 根据当前仓库、[`SEC-EXEC-01.md`](SEC-EXEC-01.md) 与本任务留存的审查消息回填，统一标记为 `HISTORICAL_RECONSTRUCTION`。它们保存可验证的候选哈希、结果和处置；没有原始 runner/命令的字段明确标为 `MISSING/UNKNOWN`。从这些摘要不能反推出未保存的完整输出，也不能把工作树哈希冒充 Git commit。

### 2.1 决策、契约与结构红卡

| step_id | occurred_at / status | 做了什么 | 为什么 | 实际效果 | 证据 / Review |
|---|---|---|---|---|---|
| `SEC-HIST-001` | `2026-08-25` / `APPROVED, ACTIVE` | 用户选择方案 A；顺序改为 `SEC-EXEC-01 → enhanced 01B-3B-2 → Budget/Acceptance/query recovery` | 3B-2 虽非纯技术依赖，但现有环境继承、清理与原文输出风险会在继续执行模型候选代码、CLI/Web 或 01D 时扩大 | 安全边界成为当前 P0；不重开已 KEEP 切片、不删除 3B-2，也不授权真实模型、秘密或外网 | [`SecurityProblem.md`](../SecurityProblem.md)、[`Plan26.md`](../Plan/Plan26.md) Amendment；review=`用户批准` |
| `SEC-HIST-002` | `2026-08-25` / `FROZEN, NOT_IMPLEMENTED` | 冻结 `local_trusted_execution/v1` A～H、5 个 Profile、opaque one-shot confirmation、三 digest challenge、cleanup/quarantine/recovery/domain separation | 实现前固定能力与非目标，避免结果出来后移动门槛，同时不把可信本地执行冒充生产 Sandbox | Plan/Backlog/HANDOFF/README/Learning Path/PROD report 同步；生产代码未改 | [`HANDOFF.md`](../HANDOFF.md) 契约、[`SEC-EXEC-01.md`](SEC-EXEC-01.md) §1/§3；早期过冻草案的完整命令=`MISSING/UNKNOWN` |
| `SEC-HIST-003` | `2026-08-25` / `REVISE, WITHDRAWN` | 首个 8 项结构红卡候选，SHA-256=`7c85c0388192fbe8749c23a612b0715282f14ba5f864f2abf2b0a501cfa50949`，曾冻结 10 个具体公共对象 | 先证明 A～H 的首个可线性化缺口 | 留存摘要为 `8F/0E/0S`、`0.011s`；Review 发现具体 Python API 过冻，且 3B-1 checkpoint 错把当前 Plan 哈希当历史哈希，因此撤回 | 原始完整 runner/命令=`MISSING/UNKNOWN`；Review=`REVISE` |
| `SEC-HIST-004` | `2026-08-25` / `FROZEN EXPECTED_RED` | 修订结构红卡，只冻结协议、入口和行为；SHA-256=`294c53d8194af9e7ae6d6e5324d5fd2bcb0a5bef8ec7547aee4d9fc69baf08da` | 保留实现自由并消除测试设计假红 | `8F/0E/0S`、`0.012s`；独立 Review `APPROVE`、blocking=0，只允许进入生产实现 | [`SEC-EXEC-01.md`](SEC-EXEC-01.md) §4/§4.1；supersedes=`SEC-HIST-003` |

### 2.2 Mock 行为红卡候选链

以下每一行都是一个实际候选，不因最终 v7 被批准而删除。历史候选的完整 shell 命令均为 `MISSING/UNKNOWN — 原始命令未保存`；结果是留存的 fresh/mock-only 汇总，不是 POSIX 或 Runtime Acceptance。

| step_id | SHA-256 | 做了什么 / 为什么修订 | 结果与实际效果 | 独立处置 / supersedes |
|---|---|---|---|---|
| `SEC-HIST-005` | `586de1176787c2888dd0ceb09a512cfd90f760c5d81678d779ed3d6494143c8e` | 首个 behavior 候选，16 项纯 mock A～H；目的是把结构缺口扩成入口行为 | `16F/0E/0S`，测试文件无真实 `Popen/run`，diff-check pass；覆盖不足 | review 结论=`MISSING/UNKNOWN`；后被 `SEC-HIST-006` 取代 |
| `SEC-HIST-006` | `a2eb66a69d01dc47920d2c66ae915abf9d13ae2b55eded3cd125523c8b7061` | 扩为 17 项，加入 challenge/one-shot/digest drift/path/cleanup-recovery/domain/Profile | `17F/0E/0S`，py_compile/diff pass；Review 找到真实 signal/残线程风险、Legacy backend 不兼容、raw log、非法请求、E/G/H 证据缺口等 7 组 blocker | `REVISE`；supersedes=`SEC-HIST-005` |
| `SEC-HIST-007` | `976680d7b581676b3def0dab480cf30da44dedb44cd669f75baf361823255394` | 修复第一轮 blocker，并增加低层 tripwire、线程、F/G/H 检查 | 两轮 `17F/0E/0S`，combined `25F/0E/0S`，0 tripwire/残线程；仍有低层 API、未登记线程、running raw log、E domain/evidence、H alias/manifest 等 10 组 blocker | `REVISE`；supersedes=`SEC-HIST-006` |
| `SEC-HIST-008` | `badd984855f5b9a976f86cacf6e9afd0575433c6459029fd6e71dc695dd86076` | v3：fresh 专用解释器、永久 audit/suite trap、扩展 invalid/E/F/G/H | 两轮 `17F/0E/0S`，combined `25F/0E/0S`，MainThread、无 tripwire；Review 发现并发护栏/alias 假红、global one-shot、E 真实行为、F 真实 sink、G/H scanner 等 blocker | 两路 `REVISE`；supersedes=`SEC-HIST-007` |
| `SEC-HIST-009` | `5bc9a0065a2e5875784ecb1137455e3eef57144da3d6df00236aa5bf8c5b955b` | v4：继续修 deep alias、async/thread、cleanup、Artifact 与 scanner | 两轮 `17F/0E/0S`，combined `25F/0E/0S`，warnings/tripwire=0、MainThread、compile/AST pass；仍有 alias overlay、async socketpair、`_thread` error、Core cleanup、低层 raw-write 等 10 个 blocker | `REVISE`；supersedes=`SEC-HIST-008` |
| `SEC-HIST-010` | `54bfe9e472c82e94d188cce279a7175290a23902b3a115d947057820c09d00de` | v5：关闭上一轮确定问题，强化 F/G/H 与 cleanup trace | 两轮 `17F/0E/0S`，combined `25F/0E/0S`，warnings/tripwire=0、MainThread；仍有 probe 顺序、`os.open/write` 历史、9999 limit token、owner alias、slots/property、PIPE/value_source/dynamic import 问题 | `REVISE`；supersedes=`SEC-HIST-009` |
| `SEC-HIST-011` | `863c666ad7f05d09b31220fd1107dd2d4693cbba470ffce159f4635379905eee` | v6：补 E 顺序、limit 拒绝、父环境 sentinel、低层写历史、真实 pipe 与 owner provenance | 两轮 `17F/0E/0S`，combined `25F/0E/0S`，MainThread、retained=0、AST pass；最终复审仍复现 cached `os.open/write/close` alias、任意 slots/property、value_source 语义 3 个 blocker | `REVISE`；supersedes=`SEC-HIST-010` |
| `SEC-HIST-012` | `63cb6660e72312e0ee3e085056566966ce3e725e191b6ff79001fa13aaf4474d` | v7：关闭最后 3 个 blocker并锁定最终 mock A～H Oracle | 两轮 `17F/0E/0S`，combined `25F/0E/0S`，仅 MainThread、`active_audit=None`、retained=0，compile/AST/diff pass | 独立 `APPROVE`、blocking=0；supersedes=`SEC-HIST-011`；仅冻结 mock EXPECTED_RED |

### 2.3 POSIX 夹具、安全预检与基线

| step_id | occurred_at / status | 做了什么 | 为什么 | 实际效果 | 证据 / Review |
|---|---|---|---|---|---|
| `SEC-HIST-013` | `2026-08-26` / `DRAFT, SUPERSEDED` | 首个 watchdog/helper/fixture 安全骨架：helper=`b964e4d1a24c74a7f6be57395f84fb691ae60df17511038884ee344ff3c99e25`，fixture=`edf9c1e85e171fce85582b60d216892de59c7252344c9da77b927fb758381944`，test=`323efac65922ceb36b73e2fd66e0abeb2bc70dc874d01f5cb541f0445ead53da` | 为真实 PID/PGID/port/marker adversarial 准备 test-side fail-safe cleanup | compile pass；未接行为卡、未启动 watchdog/target/真实进程 | 完整命令与独立 disposition=`MISSING/UNKNOWN` |
| `SEC-HIST-014` | `2026-08-26` / `DRAFT, SUPERSEDED` | 加 crash-window/PGID reuse/registration 安全：helper=`a20434ef625252d6f7116ac8f9c660fada0eb5118ba54fe833cf4b89e25302cf`，fixture=`74e0d172ac23bc0ce98428a38eec3c41b9e37c1758d6ba8ef623a37450be0e24`，test=`bd3818e3788367066f442c15bee6cfc495b7ef0864fecadac5278137cf2c4aaa` | 避免未知/复用进程组被错误 signal，并让注册失败 fail loudly | 留存结果 `13/13` mock/direct pass；无真实 spawn；后续 Review 仍要求 deadline、spawn-registration、identity 和 join 修订 | 完整命令=`MISSING/UNKNOWN`；supersedes=`SEC-HIST-013` |
| `SEC-HIST-015` | `2026-08-26` / `DRAFT, SUPERSEDED` | 加 arm ACK/deadline、gated grandchild、close/join 顺序：helper=`e45f98a61fc8bd438118e9a0de7f3c9ed1841a00c3ae3890d0ffd9deeb37f6b2`，fixture=`10c3a82f23ea702959579b25edc9841e3b569071a8a8547cfc0eef0e041f0c9f`，test=`8e70dd755d27faddc47781286d47afb21499b9687cd0703edb886cdf19a283c3` | 缩小 arm/registration 窗，并要求临时根在 watchdog join 后才能删除 | 留存结果 `17/17` mock/direct pass；SID/idle 规则仍继续修订；无真实 target | 完整命令=`MISSING/UNKNOWN`；supersedes=`SEC-HIST-014` |
| `SEC-HIST-016` | `2026-08-26` / `DRAFT, WORKLOAD_BLOCKED` | 最终预检候选：helper=`a00978afa4df611fe20df30abea4cb6d106583c6c555c3ca944cebbfadbc3451`，fixture=`034eea969031f6493e9d5dba5537673a491a50232e2d94ca42e327d33e65077f`，test=`f0a90bb1a67d26e602986b2d05e334bfa2639818af03742064c1482b08290080` | 在不运行 workload 的前提下验证 idle/arm ACK/deadline/start gate/PID-PGID-SID drift/close/join | `21/21` mock/direct pass；Review=`REVISE`：顶层 spawn 到 leader 自登记仍有无 owner 窗，emergency-stop+join 双失败可带活 watchdog 返回；真实 workload 禁跑 | [`SEC-EXEC-01.md`](SEC-EXEC-01.md) §4.3；supersedes=`SEC-HIST-015` |
| `SEC-HIST-017` | `2026-08-26` / `PASS, NARROW_SCOPE` | 只运行未 arm、无 target 的 watchdog-only 生命周期 smoke | 在真实 workload 禁跑时，只验证 watchdog 自身关闭与 join | PID `90751` 退出且 joined，`result.clean=true`、`target_spawn=0`，之后删除 temp root；不解除 `SEC-HIST-016` blocker | 完整命令=`MISSING/UNKNOWN`；[`SEC-EXEC-01.md`](SEC-EXEC-01.md) §4.3 |
| `SEC-HIST-018` | `2026-08-25/26` / `PASS, CONTROL_ONLY` | 清空父环境运行 6 个既有安全相关模块 baseline | 证明新增红卡/夹具没有破坏旧可信路径 | 初次 `101 pass/4 skip/0F/0E`, `1.767s`；v7 后 `101 pass/4 skip/0F/0E`, `1.691s`；不证明 SEC 已实现 | 初次完整命令见 [`SEC-EXEC-01.md`](SEC-EXEC-01.md) §5；v7 后独立 shell=`MISSING/UNKNOWN` |
| `SEC-HIST-019` | `2026-08-26` / `DOCUMENTED, WORKTREE_ONLY` | 把 v7、combined 25F、final Review、POSIX blocker、baseline 与下一实现动作写入 SEC report/HANDOFF | 提供单一接续入口并防止把 mock/POSIX/KEEP 混淆 | 核心阶段可复盘；候选历史和完整命令此前缺失，且文档/红卡尚无 Git checkpoint | [`SEC-EXEC-01.md`](SEC-EXEC-01.md)、[`HANDOFF.md`](../HANDOFF.md)；commit=`PENDING` |

## 3. 追加式正式条目

### TRACE-20260826-001

- `step_id / entry_type / recorded_at / occurred_at`：`TRACE-20260826-001 / PRE_REGISTER / 2026-08-26T10:30:19+08:00 / 2026-08-26`
- `principal / slice / plan_ref`：`/root / project-traceability + SEC-EXEC-01 / Plan26 Harness Evolution 与 SEC Amendment`
- `what / why / expected_effect_or_gate`：新增唯一项目级 Step Log，回填可证的 SEC 候选链，并把未来 `PRE_REGISTER→ACTUAL→REVIEW→CHECKPOINT` 规则接入 Plan、HANDOFF 和 SEC report；原因是关键结论虽已记录，但不能审计每轮候选、理由、效果和缺失证据；目标是从本条起让所有决定性逻辑步骤可追溯。
- `scope / non_goals`：只改文档与证据索引；不改生产代码/测试语义，不运行真实模型、INET、秘密或 POSIX workload，不替代 Verification/Acceptance。
- `baseline`：`branch=main; HEAD=f66e71e02c206dd361f18f58f669824ae7de6cab; worktree=dirty；保留所有既有/用户变更；输入哈希见 §0 与 SEC-HIST-004/012/016`
- `commands`：预注册阶段只读审计使用 `git status --short`、`rg`、`sed`、`shasum`；这些导航命令不作为能力验证。
- `result / effect`：`PENDING — 文档修改与验证完成后追加 ACTUAL`。
- `artifacts / evidence`：计划新增本文件，并链接 [`SEC-EXEC-01.md`](SEC-EXEC-01.md)、[`HANDOFF.md`](../HANDOFF.md)、[`Plan26.md`](../Plan/Plan26.md)。
- `review`：`disposition=PENDING; reviewer=/root 自检 + 两路只读盘点；正式独立 Review=NOT_REQUESTED`
- `supersedes_entry_id`：`NONE`
- `git_checkpoint`：`content_snapshot=PENDING; commit=PENDING — 未获授权创建 commit`
- `next_action`：完成文档接线，复跑冻结测试与 baseline，只追加实际结果和最终文件哈希。

### TRACE-20260826-002

- `step_id / entry_type / recorded_at / occurred_at`：`TRACE-20260826-002 / ACTUAL / 2026-08-26T10:30:19+08:00 / 2026-08-26（精确开始时刻 MISSING/UNKNOWN）`
- `principal / slice / plan_ref`：`/root / SEC-EXEC-01 evidence refresh / SEC report §4-§5`
- `what / why / expected_effect_or_gate`：在隔离父环境中重新执行最终结构红卡、behavior 红卡、behavior-first combined、POSIX mock safety 和既有 baseline；补齐一组从现在可复制的命令，确认文档批次开始时冻结哈希的行为没有漂移。
- `scope / non_goals`：structural/behavior 使用 fake/mock 边界；POSIX 只运行 mock/direct safety；baseline 的 4 个真实 Browser E2E 按设计 skip；未运行真实 workload、模型、INET、秘密或副作用。
- `baseline`：`branch=main; HEAD=f66e71e02c206dd361f18f58f669824ae7de6cab; worktree=dirty; structural=294c53d8…; behavior=63cb6660…; POSIX=a00978af…/034eea96…/f0a90bb1…`
- `commands`：cwd=`<repo>/demo`；公共前缀=`/usr/bin/env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 LC_ALL=C.UTF-8 HOME=/private/tmp/multiagent-trace-home TMPDIR=/private/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1`；具体命令如下：

```bash
/usr/bin/env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 LC_ALL=C.UTF-8 HOME=/private/tmp/multiagent-trace-home TMPDIR=/private/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPYCACHEPREFIX=/private/tmp/multiagent-trace-structural /usr/bin/python3 -m unittest tests.test_local_trusted_execution_expected_red -v
/usr/bin/env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 LC_ALL=C.UTF-8 HOME=/private/tmp/multiagent-trace-home TMPDIR=/private/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPYCACHEPREFIX=/private/tmp/multiagent-trace-behavior PYTHONWARNINGS=error /usr/bin/python3 -m unittest tests.test_local_trusted_execution_behavior_expected_red -v
/usr/bin/env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 LC_ALL=C.UTF-8 HOME=/private/tmp/multiagent-trace-home TMPDIR=/private/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPYCACHEPREFIX=/private/tmp/multiagent-trace-posix /usr/bin/python3 -m unittest tests.test_local_execution_posix_safety -v
/usr/bin/env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 LC_ALL=C.UTF-8 HOME=/private/tmp/multiagent-trace-home TMPDIR=/private/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPYCACHEPREFIX=/private/tmp/multiagent-trace-combined PYTHONWARNINGS=error /usr/bin/python3 -m unittest tests.test_local_trusted_execution_behavior_expected_red tests.test_local_trusted_execution_expected_red -q
/usr/bin/env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 LC_ALL=C.UTF-8 HOME=/private/tmp/multiagent-trace-home TMPDIR=/private/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPYCACHEPREFIX=/private/tmp/multiagent-trace-baseline /usr/bin/python3 -m unittest tests.test_command_validators tests.test_workflow tests.test_visionforge_browser tests.test_visionforge_runner tests.test_visionforge_rework tests.test_visionforge_evaluation -q
```

- `result / effect`：structural exit=`1`, `8F/0E/0S`, `0.012s`；behavior exit=`1`, `17F/0E/0S`, `15.800s`；POSIX safety exit=`0`, `21 pass/0F/0E/0S`, `0.019s`；combined exit=`1`, `25F/0E/0S`, `15.566s`；baseline exit=`0`, `101 pass/0F/0E/4S`, `1.725s`。红卡 exit 1 是冻结的 EXPECTED_RED；结果与当前未实现状态一致。实际效果=`achieved`，未解除 POSIX workload 禁令。
- `artifacts / evidence`：测试文件哈希与 §0/SEC report 一致；本轮不创建原始日志 Artifact，命令与汇总保存在本条。
- `review`：`disposition=NOT_REQUESTED — 仅重现已批准 Oracle；不替代 final Review`
- `supersedes_entry_id`：`NONE — 新证据，不改写历史结果`
- `git_checkpoint`：`content_snapshot=测试文件哈希匹配; commit=PENDING — 本轮未创建 commit`
- `next_action`：完成 Plan/HANDOFF/SEC report 的 Step Log 接线，校验链接、哈希、diff 与工作树边界，然后追加本次文档 ACTUAL。

## 4. 后续条目模板（复制到文件末尾）

```text
### <entry_id>
- step_id / entry_type / recorded_at / occurred_at：
- principal / slice / plan_ref：
- what / why / expected_effect_or_gate：
- scope / non_goals：
- baseline：branch=<...>; HEAD=<...>; worktree=<...>; input_hashes=<...>
- commands：cwd=<...>; <sanitized exact command | NOT_RUN + reason>
- result / effect：exit=<...>; tests=<pass/fail/error/skip>; duration=<...>; achieved=<yes|partial|no>; <summary>
- artifacts / evidence：<path + SHA-256>; refs=<...>
- review：disposition=<...>; reviewer=<...>; independence=<...>; findings=<...>; ref=<...>
- supersedes_entry_id：NONE | <entry_id + correction reason>
- git_checkpoint：content_snapshot=<...>; commit=<sha | PENDING/N/A + reason>; status=<...>
- next_action：
```

## 5. 本批收口条目

### TRACE-20260826-003

- `step_id / entry_type / recorded_at / occurred_at`：`TRACE-20260826-003 / CORRECTION / 2026-08-26T10:33:41+08:00 / 2026-08-26`
- `principal / slice / plan_ref`：`/root / SEC-EXEC-01 evidence refresh / TRACE-20260826-002`
- `what / why / expected_effect_or_gate`：补全 `TRACE-20260826-002` 中为可读性缩写的输入哈希；追加纠正而不改写已记录条目，以示范 append-only 规则。
- `scope / non_goals`：只纠正 baseline/artifact 标识，不改变命令、结果、处置或 POSIX workload 禁令。
- `baseline`：`branch=main; HEAD=f66e71e02c206dd361f18f58f669824ae7de6cab; worktree=dirty`
- `commands`：cwd=`<repo>`；`shasum -a 256 demo/tests/test_local_trusted_execution_expected_red.py demo/tests/test_local_trusted_execution_behavior_expected_red.py demo/tests/_local_execution_posix.py demo/tests/fixtures/local_execution_process.py demo/tests/test_local_execution_posix_safety.py`
- `result / effect`：exit=`0`；完整 SHA-256：structural=`294c53d8194af9e7ae6d6e5324d5fd2bcb0a5bef8ec7547aee4d9fc69baf08da`；behavior=`63cb6660e72312e0ee3e085056566966ce3e725e191b6ff79001fa13aaf4474d`；helper=`a00978afa4df611fe20df30abea4cb6d106583c6c555c3ca944cebbfadbc3451`；fixture=`034eea969031f6493e9d5dba5537673a491a50232e2d94ca42e327d33e65077f`；POSIX safety=`f0a90bb1a67d26e602986b2d05e334bfa2639818af03742064c1482b08290080`。与 SEC report 冻结值一致。
- `artifacts / evidence`：上述 5 个文件；无文件修改。
- `review`：`disposition=NOT_REQUESTED — 机械哈希纠正`
- `supersedes_entry_id`：`TRACE-20260826-002 — 仅替代其中缩写的 input_hashes/artifact hash 表述`
- `git_checkpoint`：`content_snapshot=完整测试哈希已记录; commit=PENDING`
- `next_action`：记录本次文档接线的 ACTUAL 与 WORKTREE_ONLY checkpoint。

### TRACE-20260826-004

- `step_id / entry_type / recorded_at / occurred_at`：`TRACE-20260826-004 / ACTUAL / 2026-08-26T10:33:41+08:00 / 2026-08-26`
- `principal / slice / plan_ref`：`/root / project-traceability + SEC-EXEC-01 / Plan26 Append-only Project Step Log Protocol`
- `what / why / expected_effect_or_gate`：创建项目级 Step Log；回填 Option A、结构红卡、8 个 behavior 候选、4 个 POSIX 夹具阶段、watchdog-only smoke 与 baseline；把未来强制流程接入 Plan26/HANDOFF，并在 SEC report 增加索引。原因是旧记录足以复盘里程碑但不足以逐轮审计；实际目标是让后续每个决定性步骤在动作前后都有公开、可复制的证据。
- `scope / non_goals`：只修改 `VerificationReports/STEP-LOG.md`、`VerificationReports/SEC-EXEC-01.md`、`HANDOFF.md`、`Plan/Plan26.md`；保留工作树中其他既有/用户变更；未改生产代码或测试，未运行真实模型、外网、秘密、POSIX workload 或不可逆副作用。
- `baseline`：`branch=main; HEAD=f66e71e02c206dd361f18f58f669824ae7de6cab; initial worktree=dirty; STEP-LOG new; SEC report untracked; HANDOFF/Plan26 already modified by approved Amendment`
- `commands`：文件修改使用 `apply_patch`；验证 cwd=`<repo>`，执行 `git diff --check`、`git diff --no-index --check /dev/null VerificationReports/STEP-LOG.md`、`git diff --no-index --check /dev/null VerificationReports/SEC-EXEC-01.md`、`shasum -a 256 <四个本批文档和五个冻结测试文件>`、`test -f <链接目标>` 与 `rg -n "STEP-LOG|Append-only Project Step Log|TRACE-20260826" ...`。
- `result / effect`：tracked `git diff --check` exit=`0`；两个 untracked new-file no-index check 均 exit=`1` 且输出为空（只有“存在 diff”，无 whitespace error）；链接目标检查/索引扫描 exit=`0`；冻结测试哈希未漂移。实际效果=`achieved`：历史缺口显式化，未来协议已进入权威 Plan 与新窗口入口。
- `artifacts / evidence`：`VerificationReports/SEC-EXEC-01.md` SHA-256=`3438409143b02ff29e762b7e4432d4b1c089aeda7f1f141561d105a5356b09fb`；`HANDOFF.md`=`496296a5586a0762ed60812f46f77bb52abf1f368e6751f5d36f7ed1cf6301b3`；`Plan/Plan26.md`=`eac8d66cc7b40411e0f4710bed9fd0692f3410cd653da45b8f004462e0b3de40`；Step Log 在本条追加前的 prefix hash=`0788b2c4179c49c03a9096a8b205228d2a13a8118676f30998843eda6ac155cb`，最终 self hash 因自引用不能写入自身，须由外部交接或后续 Git checkpoint 锁定。
- `review`：`disposition=NOT_REQUESTED; 两路只读盘点分别审计历史缺口和协议字段/插入位置，但未对最终修改后的 Artifact 做独立批准；不冒充 final Review`
- `supersedes_entry_id`：`NONE — 新增协议与索引，不改写 SEC 历史结论`
- `git_checkpoint`：`content_snapshot=上述 SHA-256 + 当前 Step Log prefix; commit=PENDING — 用户未要求创建 commit; status=WORKTREE_ONLY`
- `next_action`：下一生产修改开始前，先在文件末尾追加新的 `PRE_REGISTER`，目标为统一 Profile/Admission/Supervisor 首批实现；POSIX workload 在两个 fixture blocker 关闭并复审前继续禁跑。

### TRACE-20260826-005

- `step_id / entry_type / recorded_at / occurred_at`：`TRACE-20260826-005 / CHECKPOINT / 2026-08-26T10:33:41+08:00 / 2026-08-26`
- `principal / slice / plan_ref`：`/root / project-traceability / Plan26 Step Log Protocol`
- `what / why / expected_effect_or_gate`：保存本次“补历史 + 建未来协议”的 content checkpoint；明确未提交状态，防止后续把工作树记录误称为 Git release checkpoint。
- `scope / non_goals`：checkpoint 只覆盖 `TRACE-20260826-004` 声明的四个文档；其他 dirty/untracked 文件不归本条重新认领或改写。
- `baseline`：`branch=main; HEAD=f66e71e02c206dd361f18f58f669824ae7de6cab`
- `commands`：cwd=`<repo>`；`git status --short` 与 `shasum -a 256 VerificationReports/STEP-LOG.md VerificationReports/SEC-EXEC-01.md HANDOFF.md Plan/Plan26.md`；Step Log 的最终 self hash 在本条追加完成后由外部交接读取。
- `result / effect`：`git status` 仍为 dirty；本批文档内容已落盘且非本批测试哈希保持不变；`commit=PENDING`。效果=`partial`：content 可读可散列，但尚不能从单一 Git commit 重现。
- `artifacts / evidence`：稳定的非 self 文档哈希见 `TRACE-20260826-004`；本文件最终哈希由本任务最终交接给出。
- `review`：`disposition=PENDING — 独立 final Artifact Review 未请求`
- `supersedes_entry_id`：`NONE`
- `git_checkpoint`：`status=WORKTREE_ONLY; base_head=f66e71e02c206dd361f18f58f669824ae7de6cab; commit=PENDING`
- `next_action`：如需版本化 checkpoint，先确认并隔离工作树所有权后再创建明确 scoped commit；在此之前不得称 clean/committed。

### TRACE-20260826-006

- `step_id / entry_type / recorded_at / occurred_at`：`TRACE-20260826-006 / CORRECTION / 2026-08-26T10:33:41+08:00 / 2026-08-26`
- `principal / slice / plan_ref`：`/root / project-traceability / project-step-log/v1`
- `what / why / expected_effect_or_gate`：初次组装本文件时，`TRACE-003`～`005` 的块一度被补丁锚点插入 `TRACE-002` 的命令与结果之间；在首次对外交接/checkpoint 前将其移动到模板之后，并保留本纠正说明。
- `scope / non_goals`：只纠正文档布局；条目文字、测试结果、哈希、Review 与处置语义未改变。
- `baseline`：`branch=main; HEAD=f66e71e02c206dd361f18f58f669824ae7de6cab; worktree=dirty`
- `commands`：使用 `apply_patch` 重新定位完整条目块；随后以 heading/line scan 和 Markdown fence 计数验证结构。
- `result / effect`：`achieved — TRACE-002 字段连续，TRACE-003～006 位于文件末尾；该构建期布局错误未进入已提交 checkpoint`
- `artifacts / evidence`：`VerificationReports/STEP-LOG.md`
- `review`：`disposition=NOT_REQUESTED — 文档布局纠正`
- `supersedes_entry_id`：`NONE — 只记录发生过的构建期纠正，不替代任何证据条目`
- `git_checkpoint`：`status=WORKTREE_ONLY; commit=PENDING`
- `next_action`：执行最终结构、空白、链接、哈希与 status 检查；最终 self hash 在任务交接中报告。

### TRACE-20260826-007

- `entry_id / step_id / entry_type / recorded_at / occurred_at`：`TRACE-20260826-007 / TRACEABILITY-BOOTSTRAP-001 / PROTOCOL_CHANGE / 2026-08-26T10:36:30+08:00 / 2026-08-26`
- `principal / slice / plan_ref`：`/root / project-traceability / Plan26 Append-only Project Step Log Protocol`
- `what / why / expected_effect_or_gate`：把“逻辑步骤”和“账本条目”分开：一个稳定 `step_id` 可由多个唯一 `entry_id` 的 `PRE_REGISTER→ACTUAL→REVIEW→CHECKPOINT` 构成；否则动作前后会被误写成互不相关的步骤。并澄清 §0 表格是创建时快照，当前状态永远以文件末尾最新有效 `CHECKPOINT/CORRECTION` 为准。
- `scope / non_goals`：从下一逻辑步骤起采用 v1.1 字段；既有 `TRACE-001`～`006` 为 bootstrap 条目，按 `entry_id=step_id` 解释，不改写其历史文字或结果。
- `baseline`：`schema=project-step-log/v1; branch=main; HEAD=f66e71e02c206dd361f18f58f669824ae7de6cab; worktree=dirty`
- `commands`：使用 `apply_patch` 在 Plan26 增加 `entry_id/step_id` 关系，并只在本文件末尾追加本协议变更。
- `result / effect`：`achieved — 后续一次实现小批可用同一 step_id 串联预注册、实际执行、独立审查和 checkpoint；顶部状态不再被误当成可变投影`
- `artifacts / evidence`：[`Plan26.md`](../Plan/Plan26.md)、本文件。
- `review`：`disposition=PENDING — 纳入本轮独立文档审查`
- `supersedes_entry_id`：`§4 的 bootstrap 模板 — 仅对未来条目采用下方 v1.1 模板；历史条目保持原义`
- `git_checkpoint`：`status=WORKTREE_ONLY; commit=PENDING`
- `next_action`：后续条目采用下方 v1.1 模板，并追加本轮独立 REVIEW 与最终 content checkpoint。

```text
### <entry_id>
- entry_id / step_id / entry_type / recorded_at / occurred_at：
- principal / slice / plan_ref：
- what / why / expected_effect_or_gate：
- scope / non_goals：
- baseline：branch=<...>; HEAD=<...>; worktree=<...>; input_hashes=<...>
- commands：cwd=<...>; <sanitized exact command | NOT_RUN + reason>
- result / effect：exit=<...>; tests=<pass/fail/error/skip>; duration=<...>; achieved=<yes|partial|no>; <summary>
- artifacts / evidence：<path + SHA-256>; refs=<...>
- review：disposition=<...>; reviewer=<...>; independence=<...>; findings=<...>; ref=<...>
- supersedes_entry_id：NONE | <entry_id + correction reason>
- git_checkpoint：content_snapshot=<...>; commit=<sha | PENDING/N/A + reason>; status=<...>
- next_action：
```

### TRACE-20260826-008

- `entry_id / step_id / entry_type / recorded_at / occurred_at`：`TRACE-20260826-008 / TRACEABILITY-BOOTSTRAP-001 / CORRECTION / 2026-08-26T10:38:50+08:00 / 2026-08-26（TRACE-007 精确追加秒数 MISSING/UNKNOWN）`
- `principal / slice / plan_ref`：`/root / project-traceability / TRACE-20260826-007`
- `what / why / expected_effect_or_gate`：撤销把 `TRACE-007` 的 `10:36:30` 当成可验证精确时间；该值未由当时的 `date` 输出锁定。保留日期与条目顺序，精确秒数按缺失证据处理。
- `scope / non_goals`：只纠正时间证据，不改变 v1.1 协议内容、Plan 修改或任何测试结果。
- `baseline`：`branch=main; HEAD=f66e71e02c206dd361f18f58f669824ae7de6cab; worktree=dirty`
- `commands`：cwd=`<repo>`；`date '+%Y-%m-%dT%H:%M:%S%z'` 返回 `2026-08-26T10:38:50+0800`，用于锁定本纠正条目的 recorded_at。
- `result / effect`：`achieved — TRACE-007 的协议事实保留，但其精确 recorded_at 由本条标记为 MISSING/UNKNOWN，未补造`
- `artifacts / evidence`：本文件。
- `review`：`disposition=PENDING — 纳入本轮独立文档审查`
- `supersedes_entry_id`：`TRACE-20260826-007 — 仅替代其中 recorded_at 精确秒数的证据声明`
- `git_checkpoint`：`status=WORKTREE_ONLY; commit=PENDING`
- `next_action`：等待独立 Review，随后追加 REVIEW 与最终 checkpoint。

### TRACE-20260826-009

- `entry_id / step_id / entry_type / recorded_at / occurred_at`：`TRACE-20260826-009 / SEC-EXEC-01-BASELINE / CORRECTION / 2026-08-26T10:41:49+08:00 / 2026-08-25/26`
- `principal / slice / plan_ref`：`/root / SEC-EXEC-01 baseline / SEC report §5`
- `what / why / expected_effect_or_gate`：纠正 `SEC-HIST-018` 与 `TRACE-002` 的结构化计数语义。unittest 的 `Ran 101 tests ... OK (skipped=4)` 表示总运行 101，其中 97 个非 skipped 成功、4 skip，而不是 101 pass 再加 4 skip。
- `scope / non_goals`：只纠正 baseline 汇总；不改变 exit code、failure/error、测试集合、耗时或 SEC 实现状态。
- `baseline`：`branch=main; HEAD=f66e71e02c206dd361f18f58f669824ae7de6cab; worktree=dirty`
- `commands`：`NOT_RUN — 使用 TRACE-002 已保存的 runner 汇总；本轮只修正文档计数解释`
- `stop_or_rollback_conditions`：`N/A — 纯证据纠正；若原始 runner 与已保存汇总冲突则必须再追加 CORRECTION`
- `result / effect`：权威结构化表述为：初次与 v7 后历史 baseline 均 `run=101, failures=0, errors=0, skipped=4, successful_non_skipped=97`；本轮可复制重跑同样 `run=101, failures=0, errors=0, skipped=4, successful_non_skipped=97`，耗时 `1.725s`。SEC report §5 已同步精确措辞。
- `artifacts / evidence`：[`SEC-EXEC-01.md`](SEC-EXEC-01.md) §5、`TRACE-20260826-002` runner 输出。
- `remaining_risks`：历史 v7 后重跑的完整 shell 仍为 `MISSING/UNKNOWN`；本纠正不证明 SEC 已实现。
- `review`：`disposition=APPROVE_WITH_NOTES; reviewer=/root/sec_report_gap_review; independence=只读、未编辑；finding=计数总数与 skip 语义`
- `supersedes_entry_id`：`SEC-HIST-018 与 TRACE-20260826-002 — 仅替代 baseline pass/skip 计数表述`
- `git_checkpoint`：`status=WORKTREE_ONLY; commit=PENDING`
- `next_action`：升级模板以显式包含停止/回滚条件与剩余风险，然后冻结 hash 供 final Review。

### TRACE-20260826-010

- `entry_id / step_id / entry_type / recorded_at / occurred_at`：`TRACE-20260826-010 / TRACEABILITY-BOOTSTRAP-001 / PROTOCOL_CHANGE / 2026-08-26T10:41:49+08:00 / 2026-08-26`
- `principal / slice / plan_ref`：`/root / project-traceability / Plan26 Append-only Project Step Log Protocol`
- `what / why / expected_effect_or_gate`：把 Plan 已要求的 `stop_or_rollback_conditions` 与 `remaining_risks` 加入操作模板，避免执行者只读模板时漏记停止条件或残余风险；下方 v1.2 模板取代先前 bootstrap/v1.1 模板用于未来条目。
- `scope / non_goals`：只升级未来模板；旧条目不回写，缺失字段继续按其产生时 schema 与后续纠正解释。
- `baseline`：`schema=project-step-log/v1.1; branch=main; HEAD=f66e71e02c206dd361f18f58f669824ae7de6cab; worktree=dirty`
- `commands`：使用 `apply_patch` 只在 EOF 追加 v1.2 变更与模板。
- `stop_or_rollback_conditions`：若模板与 Plan 强制字段再次不一致，停止新生产修改并先追加 `PROTOCOL_CHANGE/CORRECTION`。
- `result / effect`：`achieved — 未来 PRE_REGISTER 可直接填写停止/回滚条件，ACTUAL 可直接填写剩余风险，不再依赖自由文本暗含`
- `artifacts / evidence`：本文件；[`Plan26.md`](../Plan/Plan26.md) Step Log Protocol。
- `remaining_risks`：Markdown 仍依赖人工纪律；目前没有 CI schema linter，该工程化门禁需另行预注册，不能在本批冒充已实现。
- `review`：`disposition=PENDING; reviewer=/root/sec_report_gap_review; independence=只读；等待 final verdict`
- `supersedes_entry_id`：`TRACE-20260826-007 — 只替代其 v1.1 future-entry template；entry_id/step_id 与创建快照澄清继续有效`
- `git_checkpoint`：`status=WORKTREE_ONLY; commit=PENDING`
- `next_action`：以 v1.2 模板追加独立 REVIEW 与最终 content checkpoint。

```text
### <entry_id>
- entry_id / step_id / entry_type / recorded_at / occurred_at：
- principal / slice / plan_ref：
- what / why / expected_effect_or_gate：
- scope / non_goals：
- baseline：branch=<...>; HEAD=<...>; worktree=<...>; input_hashes=<...>
- commands：cwd=<...>; <sanitized exact command | NOT_RUN + reason>
- stop_or_rollback_conditions：
- result / effect：exit=<...>; tests=<run/pass/fail/error/skip>; duration=<...>; achieved=<yes|partial|no>; <summary>
- artifacts / evidence：<path + SHA-256>; refs=<...>
- remaining_risks：
- review：disposition=<...>; reviewer=<...>; independence=<...>; findings=<...>; ref=<...>
- supersedes_entry_id：NONE | <entry_id + correction reason>
- git_checkpoint：content_snapshot=<...>; commit=<sha | PENDING/N/A + reason>; status=<...>
- next_action：
```

### TRACE-20260826-011

- `entry_id / step_id / entry_type / recorded_at / occurred_at`：`TRACE-20260826-011 / SEC-EXEC-01-BASELINE / CORRECTION / 2026-08-26T10:43:17+08:00 / 2026-08-26`
- `principal / slice / plan_ref`：`/root / project-traceability / TRACE-20260826-009`
- `what / why / expected_effect_or_gate`：纠正 `TRACE-009` 的 Review provenance：`/root/sec_report_gap_review` 当时只提供 freeze 前预审 finding，并未签发 `APPROVE_WITH_NOTES` 或任何 final disposition。
- `scope / non_goals`：只替代 `TRACE-009.review`；baseline 计数纠正继续有效，不推断最终 Review 结果。
- `baseline`：`branch=main; HEAD=f66e71e02c206dd361f18f58f669824ae7de6cab; worktree=dirty`
- `commands`：`NOT_RUN — 根据 reviewer 明确 provenance 更正追加记录`
- `stop_or_rollback_conditions`：final reviewer verdict 到达前，Step Log 只能保持 `PENDING`，不得追加 `APPROVE` 或收口声明。
- `result / effect`：`achieved — TRACE-009.review 的权威解释改为 disposition=PENDING; source=pre-review finding only; reviewer 尚未给 final verdict`
- `artifacts / evidence`：本文件；reviewer 的 freeze 前 provenance clarification。
- `remaining_risks`：最终 Artifact Review 仍待重新锁 hash 后执行。
- `review`：`disposition=PENDING; reviewer=/root/sec_report_gap_review; independence=只读；findings=计数语义 finding 已修，但尚未签发 final disposition`
- `supersedes_entry_id`：`TRACE-20260826-009 — 仅替代其 review 字段`
- `git_checkpoint`：`status=WORKTREE_ONLY; commit=PENDING`
- `next_action`：重新计算四份文档哈希并请 reviewer 对稳定内容给最终 verdict。

### TRACE-20260826-012

- `entry_id / step_id / entry_type / recorded_at / occurred_at`：`TRACE-20260826-012 / TRACEABILITY-BOOTSTRAP-001 / REVIEW / 2026-08-26T10:44:24+08:00 / 2026-08-26`
- `principal / slice / plan_ref`：`/root/sec_report_gap_review / project-traceability / Plan26 Step Log Protocol`
- `what / why / expected_effect_or_gate`：独立只读审查历史覆盖、缺失证据、未来协议可执行性、状态边界、链接、哈希与 checkpoint 声明；目的是防止本批自我批准。
- `scope / non_goals`：审查冻结内容；未编辑文件，未运行测试、真实 workload、网络或信号；不签发 `KEEP` 或 Runtime Acceptance。
- `baseline`：初审 frozen hashes：`STEP-LOG=40673e23f143a4d1856bb68c5430c07bb01ce780b155b49eb535fdbb200ecd0e; SEC=8881799a244e1a591c537d3162d950a4ae0a8e6ce3a44754e54708df60fd8aa8; HANDOFF=496296a5586a0762ed60812f46f77bb52abf1f368e6751f5d36f7ed1cf6301b3; Plan26=e9ce448dcf802c7a485c2af2f3f18070fbd0c6026ebb2aad741bd0e6fdb32412`
- `commands`：`NOT_RUN — reviewer 只读检查文件、链接、hash 与现有验证输出`
- `stop_or_rollback_conditions`：任一 blocking finding 未通过 EOF correction 关闭时，保持 `REVISE`，不得 checkpoint 为 approved。
- `result / effect`：初审 `REVISE, blocking=2`：错误补造 `APPROVE_WITH_NOTES` provenance；schema v1 与所谓 v1.1/v1.2 混用。`TRACE-011` 后只读复核确认第一项已关闭；当前正式处置为 `REVISE, blocking=1`，只剩 schema authority。
- `artifacts / evidence`：本文件、[`SEC-EXEC-01.md`](SEC-EXEC-01.md)、[`HANDOFF.md`](../HANDOFF.md)、[`Plan26.md`](../Plan/Plan26.md)。其余验收项通过：历史 What/Why/Effect/证据、MISSING/UNKNOWN、精确 baseline 计数、未来字段、EXPECTED_RED/POSIX/Acceptance/Git 边界、链接和 whitespace。
- `remaining_risks`：schema blocker 未关闭前，执行者可能把 template revision 误认为 document schema。
- `review`：`disposition=REVISE; reviewer=/root/sec_report_gap_review; independence=只读、未参与编辑; blocking_findings=1 after TRACE-011`
- `supersedes_entry_id`：`NONE — 正式记录 reviewer verdict`
- `git_checkpoint`：`status=WORKTREE_ONLY; commit=PENDING; review_gate=NOT_CLEARED`
- `next_action`：追加 schema authority CORRECTION，重新锁 Step Log hash并只读复核尾部。

### TRACE-20260826-013

- `entry_id / step_id / entry_type / recorded_at / occurred_at`：`TRACE-20260826-013 / TRACEABILITY-BOOTSTRAP-001 / CORRECTION / 2026-08-26T10:44:24+08:00 / 2026-08-26`
- `principal / slice / plan_ref`：`/root / project-traceability / TRACE-20260826-007, TRACE-20260826-010`
- `what / why / expected_effect_or_gate`：消除 schema authority 歧义：权威 document schema 从创建至今始终是 header 与 Plan 固定的 `project-step-log/v1`；`TRACE-007` 的 “v1.1” 与 `TRACE-010` 的 “v1.2” 只表示 `template_revision=1.1/1.2`，不是 schema 版本。
- `scope / non_goals`：只纠正 `TRACE-007/010` 的版本字段解释；不升级 document schema，不改 Plan/HANDOFF/SEC，也不改变模板字段内容。
- `baseline`：`document_schema=project-step-log/v1; previous_template_revision=1.1; current_template_revision=1.2; branch=main; HEAD=f66e71e02c206dd361f18f58f669824ae7de6cab`
- `commands`：`NOT_RUN — 只在 EOF 追加权威版本解释`
- `stop_or_rollback_conditions`：若未来变更 document schema，必须同时修改权威 Plan/header并追加独立 `PROTOCOL_CHANGE + REVIEW`；模板字段变化只递增 `template_revision`。
- `result / effect`：`achieved — TRACE-010.baseline 中 schema=project-step-log/v1.1 的权威解释被替代为 document_schema=project-step-log/v1, template_revision=1.1；当前模板为 template_revision=1.2`
- `artifacts / evidence`：本文件 header、[`Plan26.md`](../Plan/Plan26.md) Step Log Protocol。
- `remaining_risks`：仍无自动 schema/template linter；依赖人工追加纪律。
- `review`：`disposition=PENDING — 等待 /root/sec_report_gap_review 对本尾部纠正复核`
- `supersedes_entry_id`：`TRACE-20260826-007 与 TRACE-20260826-010 — 仅替代其中把 template revision 写成 schema version 的表述`
- `git_checkpoint`：`status=WORKTREE_ONLY; commit=PENDING; review_gate=PENDING`
- `next_action`：重新冻结 Step Log hash，等待 reviewer 对唯一剩余 blocker 给 final verdict。

### TRACE-20260826-014

- `entry_id / step_id / entry_type / recorded_at / occurred_at`：`TRACE-20260826-014 / TRACEABILITY-BOOTSTRAP-001 / REVIEW / 2026-08-26T10:46:05+08:00 / 2026-08-26`
- `principal / slice / plan_ref`：`/root/sec_report_gap_review / project-traceability / Plan26 Step Log Protocol`
- `what / why / expected_effect_or_gate`：对两项 blocker 的 EOF corrections 与四份稳定 Artifact 做最终独立只读复核；目标是只在 provenance、schema authority、历史覆盖、未来可执行性、状态边界、链接和 hash 全部一致时批准本过程记录。
- `scope / non_goals`：批准范围仅限过程记录 Artifact；不签发 `SEC-EXEC-01 KEEP`、Runtime Acceptance 或 Git commit，不解除 POSIX workload 禁令；未运行测试、真实 workload、网络或信号。
- `baseline`：`STEP-LOG=06347af56d989af9d71e9ab9ed1cbaf330ebc002e627b2cb33eedd554a6bc19a; SEC=8881799a244e1a591c537d3162d950a4ae0a8e6ce3a44754e54708df60fd8aa8; HANDOFF=496296a5586a0762ed60812f46f77bb52abf1f368e6751f5d36f7ed1cf6301b3; Plan26=e9ce448dcf802c7a485c2af2f3f18070fbd0c6026ebb2aad741bd0e6fdb32412`
- `commands`：`NOT_RUN — reviewer 只读复核 EOF、链接、已保存 hash/whitespace 证据`
- `stop_or_rollback_conditions`：若尾部未关闭 provenance/schema 任一 blocker，处置保持 `REVISE`；若 Review 被外推为 SEC/Runtime/Git/POSIX 验收，立即撤销该外推并追加纠正。
- `result / effect`：`APPROVE; blocking_findings=0`。确认 `TRACE-011` 关闭伪 disposition，`TRACE-013` 锁定 `document_schema=project-step-log/v1` 且 v1.1/v1.2 仅为 template revision；上一轮 `REVISE` 历史保留。五项验收全部满足。
- `artifacts / evidence`：本文件、[`SEC-EXEC-01.md`](SEC-EXEC-01.md)、[`HANDOFF.md`](../HANDOFF.md)、[`Plan26.md`](../Plan/Plan26.md)；review final message。
- `remaining_risks`：记录仍为 `WORKTREE_ONLY`；没有自动 schema linter；历史明确标记的 MISSING/UNKNOWN 仍无法补回；生产 SEC 能力仍未实现。
- `review`：`disposition=APPROVE; reviewer=/root/sec_report_gap_review; independence=只读、未参与编辑; blocking_findings=0`
- `supersedes_entry_id`：`NONE — 关闭 TRACE-012 的 review gate，但保留其 REVISE 历史`
- `git_checkpoint`：`status=WORKTREE_ONLY; commit=PENDING; review_gate=CLEARED_FOR_RECORD_ARTIFACT_ONLY`
- `next_action`：执行最终只读 diff/link/hash/status 检查并追加 WORKTREE_ONLY checkpoint；随后下一生产步骤必须先 PRE_REGISTER。

### TRACE-20260826-015

- `entry_id / step_id / entry_type / recorded_at / occurred_at`：`TRACE-20260826-015 / TRACEABILITY-BOOTSTRAP-001 / CHECKPOINT / 2026-08-26T10:46:55+08:00 / 2026-08-26`
- `principal / slice / plan_ref`：`/root / project-traceability / Plan26 Step Log Protocol`
- `what / why / expected_effect_or_gate`：保存完成历史回填、未来协议接线、证据重跑、两轮 REVISE 修正和最终独立 APPROVE 后的 content checkpoint；明确它仍不是 Git commit。
- `scope / non_goals`：本批只认领 `VerificationReports/STEP-LOG.md`、`VerificationReports/SEC-EXEC-01.md`、`HANDOFF.md`、`Plan/Plan26.md` 的本轮追加/接线；工作树中其他既有/用户变更全部保留且不归本条重新认领。未改生产代码或测试。
- `baseline`：`branch=main; HEAD=f66e71e02c206dd361f18f58f669824ae7de6cab; worktree=dirty; review_gate=APPROVE_FOR_RECORD_ARTIFACT_ONLY`
- `commands`：cwd=`<repo>`；`git diff --check`；对两个 untracked 文档分别执行 `git diff --no-index --check /dev/null <file>`；检查 5 个链接目标与交叉引用；统计 Markdown fences/TRACE ID 唯一性；`shasum -a 256` 四个文档和五个冻结测试文件；`git rev-parse` 与 `git status --short`。
- `stop_or_rollback_conditions`：任一 whitespace/link/duplicate-ID/hash 检查失败、冻结测试哈希漂移、或 reviewer blocking finding 非 0 时不得建立 checkpoint；若 commit 未创建，只能保持 `WORKTREE_ONLY`。
- `result / effect`：tracked diff-check exit=`0`；两个 untracked no-index check exit=`1` 且输出为空（仅表示新文件有 diff，无 whitespace error）；链接 exit=`0`；Markdown fence 数=`8`（4 对），TRACE entry 数=`14` 且无重复；HEAD/branch 未变；review=`APPROVE, blocking=0`。效果=`achieved for content checkpoint`，`partial for versioned checkpoint`。
- `artifacts / evidence`：追加本条前 Step Log SHA-256=`338a0b9e596fd88ce1804cd2269944ec86e25b80477db46a10ad285465fdd388`；SEC report=`8881799a244e1a591c537d3162d950a4ae0a8e6ce3a44754e54708df60fd8aa8`；HANDOFF=`496296a5586a0762ed60812f46f77bb52abf1f368e6751f5d36f7ed1cf6301b3`；Plan26=`e9ce448dcf802c7a485c2af2f3f18070fbd0c6026ebb2aad741bd0e6fdb32412`；五个冻结测试哈希见 `TRACE-003`，最终检查均匹配。
- `remaining_risks`：Step Log 最终 self hash 因自引用只能在本任务外部交接/后续 Git checkpoint 报告；当前无自动 schema linter；历史 MISSING/UNKNOWN 不可恢复；SEC 生产能力仍未实现且 POSIX workload 仍禁跑。
- `review`：`disposition=APPROVE; reviewer=/root/sec_report_gap_review; independence=只读; blocking_findings=0; scope=record artifact only`
- `supersedes_entry_id`：`TRACE-20260826-005 — 以修正后的 schema/provenance、最终 Review 和新稳定文档哈希替代其早期 content checkpoint；保留其未提交历史`
- `git_checkpoint`：`status=WORKTREE_ONLY; base_head=f66e71e02c206dd361f18f58f669824ae7de6cab; commit=PENDING — 用户未要求且 dirty worktree 含多组既有变更`
- `next_action`：下一生产实现小批开始前，用新的稳定 step_id 追加 PRE_REGISTER；目标仍是统一 Profile/Admission/Supervisor 首批实现，真实 POSIX workload 在 fixture blockers 关闭并复审前继续禁跑。

### TRACE-20260826-016

- `entry_id / step_id / entry_type / recorded_at / occurred_at`：`TRACE-20260826-016 / SEC-EXEC-01-CHECKPOINT-001 / PRE_REGISTER / 2026-08-26T10:52:09+08:00 / 2026-08-26`
- `principal / slice / plan_ref`：`/root / SEC-EXEC-01 record + EXPECTED_RED checkpoint / Plan26 SEC Amendment + Step Log Protocol`
- `what / why / expected_effect_or_gate`：按用户明确要求创建并推送方案 A、SEC-EXEC-01 冻结红卡/POSIX 安全夹具与 Step Log 规则的 scoped Git checkpoint；主提交后再用独立小提交记录主 commit SHA，避免把工作树 content hash 冒充 Git checkpoint。
- `scope / non_goals`：include=`HANDOFF.md, LEARNING_PATH.md, OPTIMIZATION_BACKLOG.md, Plan/Plan25.md, Plan/Plan26.md, README.md, VerificationReports/PROD-01B.md, SecurityProblem.md, VerificationReports/SEC-EXEC-01.md, VerificationReports/STEP-LOG.md, demo/tests/test_local_trusted_execution_expected_red.py, demo/tests/test_local_trusted_execution_behavior_expected_red.py, demo/tests/_local_execution_posix.py, demo/tests/fixtures/local_execution_process.py, demo/tests/test_local_execution_posix_safety.py`；exclude 并保留=`demo/track.md, problems.md, prombles.md deletion, Plan/Plan28.md`。不改生产实现，不运行真实模型、INET、秘密或 POSIX workload。
- `baseline`：`branch=main; HEAD=f66e71e02c206dd361f18f58f669824ae7de6cab; origin/main=f66e71e02c206dd361f18f58f669824ae7de6cab; worktree=dirty; staged=empty`
- `commands`：计划在 repo root 执行 scoped `git add -- <include list>`、`git diff --cached --check`、`git diff --cached --name-status`、秘密/临时产物扫描；在 `demo/` 的 sanitized env 下执行 py_compile、behavior-first 25 项 EXPECTED_RED、POSIX mock safety 与 101 项 baseline；随后 `git commit -m "test(sec): freeze trusted local execution gates"`。主 commit 后仅追加 ACTUAL/CHECKPOINT 到本文件并创建 `docs: record SEC checkpoint`，最后 `git push origin main`。
- `stop_or_rollback_conditions`：staged 路径包含 exclude 项；真实 secret/私钥或临时产物命中；compile/diff/link/hash 失败；EXPECTED_RED 不是 `25F/0E/0S`；POSIX safety 不是 `21/21`；baseline 不是 `run=101, failures=0, errors=0, skipped=4`；远端不再是预期祖先/出现 non-fast-forward；或 push 需要超出用户授权的历史改写。
- `result / effect`：`PENDING — PRE_REGISTER 不冒充已执行结果`
- `artifacts / evidence`：本文件；提交前冻结测试 SHA-256 见 `TRACE-003`；过程记录 Artifact 独立 Review 见 `TRACE-014`。
- `remaining_risks`：主提交包含 EXPECTED_RED 测试，不代表安全能力已实现；真实 POSIX workload 仍禁跑；排除项会让提交后工作树继续 dirty；最终 push 的自身 commit SHA/remote 状态只能由外部交接或下一追加条目记录。
- `review`：`disposition=PENDING; scope audit=/root/commit_scope_audit read-only; precommit review=PENDING`
- `supersedes_entry_id`：`NONE`
- `git_checkpoint`：`status=PRE_REGISTERED; commit=PENDING; push=PENDING`
- `next_action`：完成只读预提交审查与门禁；只 stage include 列表并核对 staged diff。

### TRACE-20260826-017

- `entry_id / step_id / entry_type / recorded_at / occurred_at`：`TRACE-20260826-017 / SEC-EXEC-01-CHECKPOINT-001 / CORRECTION / 2026-08-26T10:53:32+08:00 / 2026-08-26`
- `principal / slice / plan_ref`：`/root / public-repository hygiene / Plan26 Step Log Protocol`
- `what / why / expected_effect_or_gate`：在首次 Git checkpoint 前，把 Step Log 六处 host-specific `/Users/<name>/...` cwd 改为稳定的 `<repo>`/`<repo>/demo` 占位符，并同步 Plan 规定公开记录使用 repo-relative cwd；避免发布本机用户名并提高命令可移植性。
- `scope / non_goals`：仅脱敏 cwd 前缀；命令参数、测试结果、时间、hash、Review 与处置语义不变。`/private/tmp` 属测试隔离路径，保留。
- `baseline`：`branch=main; HEAD=f66e71e02c206dd361f18f58f669824ae7de6cab; checkpoint not yet created`
- `commands`：使用 `apply_patch` 精确替换本文件六处 cwd 与 Plan26 一处协议文字；计划用 staged `git grep --cached` 确认 `/Users/` 和真实凭据模式为 0。
- `stop_or_rollback_conditions`：若脱敏破坏命令可复制性、改变任何测试证据，或 staged 扫描仍出现本机路径/真实秘密，则停止提交并修正。
- `result / effect`：`achieved — 公开 Artifact 使用 <repo> 占位符；实际工作目录关系仍精确；原 host-specific 版本从未提交或推送`
- `artifacts / evidence`：本文件、[`Plan26.md`](../Plan/Plan26.md) Step Log Protocol；precommit reviewer `/root/precommit_review` 只读建议。
- `remaining_risks`：`<repo>` 需要执行者先进入 clone 根目录；它不影响已保存绝对 `/usr/bin/env` 与 `/private/tmp` 运行边界。
- `review`：`disposition=APPROVE_WITH_NOTES; reviewer=/root/precommit_review; independence=只读; finding=公开仓库卫生/可移植性，不是凭据泄漏`
- `supersedes_entry_id`：`TRACE-002/003/004/005/008/015 中的 host-specific cwd 字段 — 仅替代路径展示，不改证据语义`
- `git_checkpoint`：`status=PRE_REGISTERED; commit=PENDING; push=PENDING`
- `next_action`：运行提交前门禁，显式 stage allowlist，并确认 exclude 路径均不在 index diff 中。

### TRACE-20260826-018

- `entry_id / step_id / entry_type / recorded_at / occurred_at`：`TRACE-20260826-018 / SEC-EXEC-01-CHECKPOINT-001 / CORRECTION / 2026-08-26T10:54:03+08:00 / 2026-08-26`
- `principal / slice / plan_ref`：`/root / review provenance / TRACE-20260826-017`
- `what / why / expected_effect_or_gate`：纠正 `TRACE-017.review`：precommit reviewer 提供的是只读 finding/高优先建议与 allowlist，不是 `APPROVE_WITH_NOTES` final disposition。
- `scope / non_goals`：只替代 `TRACE-017.review`；路径脱敏事实与结果继续有效。
- `baseline`：`branch=main; HEAD=f66e71e02c206dd361f18f58f669824ae7de6cab; worktree=dirty`
- `commands`：`NOT_RUN — 根据 reviewer 原始措辞纠正 provenance`
- `stop_or_rollback_conditions`：staged 门禁全部完成前保持 `PENDING`，不得把 advice 冒充批准。
- `result / effect`：`achieved — TRACE-017.review 权威解释为 disposition=PENDING; source=precommit read-only finding/advice only`
- `artifacts / evidence`：本文件；`/root/precommit_review` 回报。
- `remaining_risks`：真正可提交性仍取决于 staged allowlist、secret/path scan 与测试门禁。
- `review`：`disposition=PENDING; reviewer=/root/precommit_review; independence=只读; source=finding/advice only`
- `supersedes_entry_id`：`TRACE-20260826-017 — 仅替代 review 字段`
- `git_checkpoint`：`status=PRE_REGISTERED; commit=PENDING; push=PENDING`
- `next_action`：运行门禁并 stage 精确 allowlist。

### TRACE-20260826-019

- `entry_id / step_id / entry_type / recorded_at / occurred_at`：`TRACE-20260826-019 / SEC-EXEC-01-CHECKPOINT-001 / ACTUAL / 2026-08-26T10:57:25+08:00 / 2026-08-26`
- `principal / slice / plan_ref`：`/root / SEC-EXEC-01 precommit gate / TRACE-20260826-016`
- `what / why / expected_effect_or_gate`：完成 scoped staging、提交前测试、隐私/凭据扫描与远端并发检查；验证 PRE_REGISTER 的停止条件未触发，允许创建主 commit。
- `scope / non_goals`：staged 恰为 `TRACE-016` 的 15 个 include 文件；`demo/track.md`、`problems.md`、`prombles.md` deletion、`Plan/Plan28.md` 均未 staged。未运行真实 POSIX workload、模型、INET 或真实秘密。
- `baseline`：`branch=main; HEAD=origin/main=f66e71e02c206dd361f18f58f669824ae7de6cab; divergence=0/0; staged_file_count=15`
- `commands`：在 `<repo>/demo` 使用 sanitized `/usr/bin/env -i ...` 运行 5 个冻结文件 `py_compile`、behavior-first combined unittest、POSIX mock safety、6 模块 baseline；在 `<repo>` 显式 `git add -- <15-file allowlist>`，执行 cached name/status/stat/diff-check、added-line secret/path regex、exclude scan、SHA-256，并 `git fetch origin` 后比较 divergence。
- `stop_or_rollback_conditions`：全部通过：无 staged scope drift、无新增真实 secret/私钥/host username、无 compile/error/skip 漂移、无远端并发更新；真实 workload 继续禁跑。全 index 旧历史含绝对 cwd，因此隐私门禁按 staged **新增行**执行，命中 0，而不是误报旧内容。
- `result / effect`：compile exit=`0`；EXPECTED_RED exit=`1`, `run=25, failures=25, errors=0, skipped=0`, `15.431s`；POSIX mock exit=`0`, `run=21, failures=0, errors=0, skipped=0`, `0.015s`；baseline exit=`0`, `run=101, failures=0, errors=0, skipped=4, successful_non_skipped=97`, `1.727s`；cached diff-check exit=`0`；exclude scan无命中；fetch后 divergence=`0 0`。效果=`achieved`。
- `artifacts / evidence`：五个冻结测试哈希仍为 structural=`294c53d8…`、behavior=`63cb6660…`、helper=`a00978af…`、fixture=`034eea96…`、safety=`f0a90bb…`；本条追加前 Step Log=`855d7ed1a29c51b80b243df3e82fa0f1ecb77c2a43383ded0b9471dd058626b5`；cached stat=`15 files, 14781 insertions, 22 deletions`（本条重新 stage 后行数会增加）。
- `remaining_risks`：主 commit/push 尚未发生；提交后工作树会因 4 个 excluded 变更继续 dirty；EXPECTED_RED 只证明能力尚缺，不是实现失败或安全验收。
- `review`：`disposition=NOT_REQUESTED; scope audit=/root/commit_scope_audit read-only; precommit findings=/root/precommit_review read-only; findings=allowlist enforced, added-line privacy scan clean`
- `supersedes_entry_id`：`NONE — PRE_REGISTER 的实际执行结果`
- `git_checkpoint`：`status=STAGED_AND_VERIFIED; commit=PENDING; push=PENDING`
- `next_action`：重新 stage 本条，复核 cached scope/check，然后创建主 commit；禁止 force push。
