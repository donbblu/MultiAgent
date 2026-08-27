# SEC-EXEC-01 VerificationReport

> 本文件是 `local_trusted_execution/v1` 的 EXPECTED_RED、实现、攻击、回归和决定证据入口。`Plan` 与 `HANDOFF` 定义应当实现什么，测试提供可执行 Oracle，本报告只记录实际发生了什么。当前状态不是安全验收或 Runtime Acceptance。

> 2026-08-27 优先级更新：用户决定先完成 [`MVP-CLOSE-01`](../Plan/Plan29.md) 作品集版项目闭环。本报告及既有实现证据保持有效，当前决定仍为 `INCONCLUSIVE / KEEP_NOT_ISSUED`；余下 Browser/Renderer、完整 target adversarial、full regression 和最终安全 Review 转为后续认证，不再阻塞 MVP 完成。

## 0. 报告身份

| 字段 | 值 |
|---|---|
| `report_schema` | `verification-report/v1` |
| `report_id` | `VR-SEC-EXEC-01` |
| `created_at` | `2026-08-25` |
| `last_updated` | `2026-08-27` |
| `contract_ref` | [`HANDOFF.md`](../HANDOFF.md) 的 `local_trusted_execution/v1` A～H |
| `plan_amendment_ref` | [`PA-2026-08-25-SEC-EXEC-01-FIRST`](../Plan/Plan26.md) |
| `decision_ref` | [`SecurityProblem.md`](../SecurityProblem.md) |
| `step_log_ref` | [`Project Step Log`](STEP-LOG.md) 的 `SEC-HIST-001`～`SEC-HIST-019` 与 `TRACE-20260826-*` |
| `evidence_status` | `MOCK_STRUCTURAL_IMPLEMENTATION_REVIEWED / POSIX_NO_TARGET_NARROW_REVIEWED / POSIX_TARGET_AND_BROWSER_PENDING` |
| `lifecycle_status` | `IMPLEMENTED_CANDIDATE` |
| `decision` | `INCONCLUSIVE` |
| `runtime_acceptance` | `NOT_ISSUED` |

当前冻结的 A～H Oracle及已复现mock blocker已收口，并新增各一次watchdog-only与arm→ACK→disarm真实零target窄执行；两档均完成双独立Review和exact cleanup。它仍不是 `KEEP`：target-bearing POSIX生命周期、真实Browser/E2E、Renderer/browser binary契约与最终独立Review尚未完成。

## 1. 变更与非变更边界

用户于 2026-08-25 正式选择方案 A；以下是当时冻结的生产路线顺序。它自 2026-08-27 起已由 Plan29 后置，不是当前 MVP 执行顺序：

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
| HEAD | `0f9e41ad76d7a25deee0a28de42a422707a6f24d` |
| production/tests vs HEAD before red card | clean |
| worktree | dirty；包含已批准 Amendment、交接/说明文档、既有问题文档整理和本红卡；精确状态以 `git status` 为准 |
| OS | macOS `26.5` / arm64 |
| Python | `3.9.6`，`/usr/bin/python3` |
| model/network/real secret | 均未使用 |

冻结文件哈希：

| 文件 | SHA-256 |
|---|---|
| `demo/tests/test_local_trusted_execution_expected_red.py` | `1e63489f6c33b1bf4ac90b4d1ac4ed4f97f796ac4022d9de8193f4224fcb7bb4` |
| `demo/tests/test_local_trusted_execution_behavior_expected_red.py` | `1ce0cc46136ffc8970304c7f1c3dede0205b97fd010602a1c6924561518f03a0` |
| `demo/coding_workflow/local_execution.py` | `90be53ffd9df1f5527b343d6ab01166ed2dcbae320b87b0a53356e2758e4320b` |
| `demo/coding_workflow/local_execution_approval.py` | `f578db36aad208b0f0104c94f6ffaba99f2dfe53558e0d59a27505e563066143` |
| `demo/coding_workflow/command_validators.py` | `5405aec9b5e2985a0cb23b10843a5a1d69a075b87e6ce83825af9121824a6be8` |
| `demo/coding_workflow/workspace.py` | `88420c7cea21b75d342848cd3d505c8565fd8fcf2106acdf7bc78b0c24988e5e` |
| `demo/coding_workflow/policy.py` | `4ed5833304e61e9645895b5e436e5c2751245e3d4e2957b588ae25aa15cd6bce` |
| `demo/coding_workflow/visionforge/browser.py` | `d2159829f6fc0a54bbe1edc9345e422abc8b3805d896aaf7aa68bd6fa5608d06` |
| `demo/coding_workflow/visionforge/evaluation_runtime.py` | `1e9248b7a3494b58eea9bcdd2bf4f9fb79cdff2ed8028a49a5cdf87c46b874ed` |
| `demo/coding_agent_cli.py` | `0ea0782aff81da64f2f3ee54f4030187463bb97005cc8ecccefcf040625a92eb` |
| `demo/visionforge_eval_run.py` | `286c32570e5a4bf74b0ada92dd6f1d319beb6f765287068e5b22c20934b92730` |
| `demo/tests/test_local_execution_supervisor.py` | `fa04f0750f5164829af1e67954cfe24c6186ada96a8811f909a3caa7aed6e430` |
| `demo/tests/test_local_execution_approval.py` | `015b3f785750a5820bb4c2548776d37d5acff0926997e0b4b5c292bb54a3756e` |
| `demo/tests/test_visionforge_eval_composition.py` | `5b0f06177898d167af5979d5c85be717bb57a55840f57eca0f95f5743972f983` |

## 3. 首轮 A～H 结构红卡

首轮只冻结每个门禁的第一个可线性化缺口，并保证 unittest 能发现全部 8 项；它不会用八个结构断言冒充完整安全验收。完整行为、POSIX 进程和正常路径 Oracle 同时冻结在下表，必须在最终 `KEEP` 前全部转成可执行证据。

| 门禁 | 首轮测试 | 当前 EXPECTED_RED 签名 | 最终必须补齐的行为 Oracle |
|---|---|---|---|
| A / admission | `test_a_admission_contract_is_public_and_runtime_owned` | Runtime scope 缺少版本、`trusted_local`、`SANDBOX_REQUIRED` 及 input/profile digest 协议标记 | 缺失、dict 伪造、模型来源、过期、Workspace/input/profile digest 漂移和越界需求都在 spawn 前返回 `SANDBOX_REQUIRED`，spawn/PID/副作用为 0；只有 Composition Root 绑定的合法确认可启动一次 |
| B / 环境、FD、秘密 | `test_b_entrypoints_do_not_inherit_parent_environment` | Legacy 在缺失 Runtime-owned confirmation 时仍触达 process backend；Browser 复制父环境；Core 可任意扩展环境 | 五个前台/后台入口的初始继承环境只含 Profile；父 sentinel/fake key/proxy/SSH/注入变量命中 0；`stdin=DEVNULL`、`close_fds=True`、登记外 FD 为 0；HOME/TMPDIR 每次唯一、0700，返回后不存在 |
| C / 命令 Profile | `test_c_only_absolute_registered_profile_reaches_spawn` | 版本/profile digest 协议缺失；Legacy 在缺失 admission 时仍触达 basename resolution backend | 只有 Composition Root 解析并登记的绝对 executable、完整 argv、cwd、env 和 digest 可启动；字段缺失/漂移、参数变化、Workspace 同名 executable、放宽 deadline/output 均零 spawn；调用方只可收紧 |
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

2026-08-26 实现前可满足性审计又发现一项 Oracle 自相矛盾：历史结构 B/C 要求无 `trusted_local` 的 Legacy 调用触达 `subprocess.run`，但已冻结行为 A 对同调用要求零 spawn 与 `SANDBOX_REQUIRED`。按 Step Log `TRACE-20260826-023`～`026` 最小修正后，结构 B/C 只固定 admission 前不得触达 backend；完整 env/FD/HOME/TMP 与绝对 executable/argv/limit 继续由行为 B/C 权威覆盖。新结构哈希为 `1e63489f6c33b1bf4ac90b4d1ac4ed4f97f796ac4022d9de8193f4224fcb7bb4`；pre-implementation 复跑仍为 `8F/0E/0S`，behavior-first 合并仍为 `25F/0E/0S`。独立只读复审 principal=`/root/core_design`，结论 `APPROVE`、blocking finding 0。历史哈希和当时批准保留在 Step Log，未被倒填。

### 4.2 Mock A～H 行为红卡定版

行为红卡必须在 **全新专用解释器中先于其他项目模块加载**。测试安装进程级 audit backstop、suite-level tripwire 和受跟踪的线程/Task/本地 IPC；每项产品入口仍由 Fake Process/Signal/Network 边界接管。该护栏只防止红卡误触真实进程、INET 网络或信号，不是 Python/native-extension 或 OS 沙箱。

冻结文件：

| 文件 | SHA-256 |
|---|---|
| `demo/tests/test_local_trusted_execution_behavior_expected_red.py` | `1ce0cc46136ffc8970304c7f1c3dede0205b97fd010602a1c6924561518f03a0` |

原实现前候选hash=`036d101bfd157e1513b3c0e02994926fbd0f9d95a19f9a6397e3eb7682f9ad19`。2026-08-26 在清空父环境、`PYTHONWARNINGS=error` 的 fresh Python 3.9.6 解释器中执行两次，结果均为 `17 tests / 17 failures / 0 errors / 0 skipped`；退出时仅 `MainThread`，`active_audit=None`，retained tripwire stack 为 0。按 behavior-first 顺序与 8 项结构卡合并运行，结果为 `25 tests / 25 failures / 0 errors / 0 skipped`。`py_compile`、AST 形状检查和 `git diff --check` 均通过；17 个测试各只有一个末尾 `assertEqual(violations, [])`，没有 skip 或 `expectedFailure`。

独立只读定版审查 principal 为 `/root/sec_option_a_review`，锁定原实现前hash=`036d101b…9ad19`后结论为`APPROVE`、blocking finding 0。审查明确核对 admission 的跨入口一次性、非法请求零 challenge、Profile/output-limit 漂移、Workspace/Browser 路径、cleanup/quarantine/recovery、持久写历史与下游脱敏、旧 Result/Artifact 兼容、全 `demo` 进程入口扫描和 test-only manifest。该批准只允许把 **mock 行为 EXPECTED_RED** 保存为后续生产实现 Oracle；当前`1ce0cc46…8f03a0`的qualified semantic manifest correction又经Gate-02两名原reviewer独立复核为`APPROVE/blocking=0`。真实 PID/PGID/port/handle/marker 消失仍由 POSIX 卡证明，也不构成 `SEC-EXEC-01` `KEEP`、最终安全 Review 或 Runtime Acceptance。

2026-08-26 实现期可满足性审计又以 append-only 方式修正五组 fixture 输入：replay control 使用签发时的默认 limits；shadow Browser 显式绑定 fake `/usr/bin/pnpm`；Browser 路径与 shared challenge helper 显式绑定 Runtime-owned `workspace_root`；FakeManaged 只在真实 fake cleanup trace 完成后公开 evidence，供 readiness 异常传播；F 的 Legacy deadline 与令牌一致，且 dev token 在全部测试写入完成后才签发。Correction-02/03/04/05 均经独立只读复审为 `APPROVE`、blocking 0；旧 v7 `63cb6660...4474d` 的运行与批准历史保留在 Step Log `SEC-HIST-012`，未倒填。POSIX helper/fixture修复后，H的test-only manifest仍指旧行号`697/345`；`TRACE-20260826-105`先以H定向`1 failure / 1 pass / 0 error/skip`捕获精确漂移，再改为当前AST实体`709/410`。`TRACE-20260826-108/112`进一步把test-only manifest冻结为`path + API + qualified owner + occurrence`，行号只保留为诊断；人工平移100行不改变安全事实，出现第二个call会改变occurrence，把call移入另一class的同名method也会改变qualified owner。当前行为Oracle hash=`1ce0cc46136ffc8970304c7f1c3dede0205b97fd010602a1c6924561518f03a0`，H定向`2/2`与behavior-first combined `25/25`均通过；restricted scanner语义、生产边界与断言未放宽。

### 4.3 POSIX 夹具与 reviewed no-target 窄证据（未批准 target workload）

当前 reviewed POSIX no-target 工件哈希为：

| 文件 | SHA-256 |
|---|---|
| `demo/tests/_local_execution_posix_smoke_runner.py` | `20da45a18465a753a83c8388b0dd48863a0b7dbffdd8fdbb1eb83a782083448b` |
| `demo/tests/test_local_execution_posix_smoke_runner.py` | `bd0d2654870c9b59ec11e4e2bf73de49b5b47cb213d13050c732ed788b831d02` |
| `demo/tests/test_local_execution_posix_smoke.py` | `bca89a4f92d329477927972a58f1f3ac7139940e53fb79edfbceb5322812d44f` |
| `demo/tests/_local_execution_posix.py` | `a87ed9f82e93877cb473f7c47120a2e73cc18fc75c82e3437c8878f75b002999` |
| `demo/tests/fixtures/local_execution_process.py` | `80ecd65de830f5d61c3e2e9a1dd6948e8207cada79001a418910b57330d206d8` |
| `demo/tests/test_local_execution_posix_safety.py` | `266b8a328d79af523465355905618ad969ae6ae39c3bf92910e0055f9d149bdd` |

早期候选（旧hash，见`SEC-HIST-013`～`017`）的21项pure safety虽全绿，独立复审仍以顶层child登记窗和watchdog终端join为由给`REVISE`；它不代表当前六hash。后续append-only修复引入spawn observation/ACK、ACK freshness、candidate exact binding、terminal-no-escape与BaseException defer，当前pure safety为`39/39`并经独立窄复审`APPROVE/blocking=0`。这只批准fixture的mock/structural安全属性，仍不自动授权target。

#### 4.3.1 Reviewed POSIX no-target窄证据

- **What**：冻结六工件上分别执行一次watchdog-only（run_id `...0001`）和一次arm→ACK→disarm（run_id `...0002`）。后者只生成并删除command tuple，未执行target。
- **Why**：按最小风险阶梯分别验证watchdog ready→close→join，以及arm lease/ACK→`disarmed_no_spawn`收敛，不跳入target生命周期。
- **Effect**：两次均exact one test、0 skip/failure/error、terminal clean/join、零target。watchdog-only PASS receipt为477B/SHA256 `8a4d0ecb236c2760d2d974e19e4a76d1872bc26b0fcce826ee7f887f866e45f4`；arm-disarm PASS receipt为471B/SHA256 `d2b2d9084194ed74b5fac7e4befb9adfdf19582d8c133e5779b1cf70e6d29e85`，cleanup canonical为454B/SHA256 `fb586152ab326a0ebffd3f306cc88fd3b071b57863fd4efbac7f7a0f2be3dff6`。两档execution与producer-bound exact cleanup均经两名独立reviewer `APPROVE/blocking=0`，对应scope已不存在。
- **Evidence**：watchdog-only见`TRACE-20260826-091`～`097`；arm-disarm见`TRACE-20260826-098`～`104`。本次同步基线tail=`TRACE-20260826-104`，STEP-LOG SHA256=`defef02ef5f8f69ae9e98ba986c508a4af5e9c9e762e34063643bd179382c53d`。
- **Limits**：`0.153947709`、`0.000727167`、`0.18214575`、`0.000002917`均只是原始且不可信的tool wall telemetry，不作为performance、deadline或security证据。该证据不证明target、异常生命周期、OS hard-wall、`KEEP`或Runtime Acceptance。

#### 4.3.2 Target-entry evidence contract（历史冻结设计；后续已完成一次窄执行）

本节记录执行前冻结的合同：首个target候选只允许可信fixture的`stdout_short`，当时不创建runner、不执行tuple，也不授权其他mode。后续已按该合同构建并双审独立于no-target runner、默认skip的artifact，并在`TRACE-20260826-119`～`121`只执行一次`stdout_short`开发smoke；该证据只证明**fixture + `ExternalProcessGuard`**的受管生命周期。绑定production hash仅用于静态接口兼容，不能冒充production Runtime的真实进程验收；其他mode、重复执行与production integration仍需新的独立预注册、证据工件与Review。

1. **Exact launch binding**：checked-in runner/test、source-only loader、direct root-owned Python、literal flags、exact环境/raw argv/FQ test/run-id，以及runner/test/helper/fixture/safety/production hashes；不得使用discovery、`-k`、module selector、pyc或环境推导。runner只能经`ExternalProcessGuard.spawn_observing_popen()`执行其刚生成的exact tuple，且只允许一次。
2. **唯一证据authority**：receipt中的PID/PGID/SID、输出、wait、signal、absence、marker与cleanup字段，必须从该wrapper保留的**同一个实际强`Popen` handle**、guard/watchdog原始manifests以及dirfd/no-follow读取的实际文件重构；任意Result/DTO、自报布尔值、预填digest或测试常量都不是authority。pure card必须从原始trace重算canonical digest，并对handle identity、wait、manifest、输出、probe、marker任一字段变异给出负卡。
3. **Reap、group与四阶段证据**：collector必须对exact强handle调用`wait(timeout=remaining)`，记录调用、返回码与调用后的`poll()`，只有wait返回且returncode=`0`才可给`direct_child_reaped=true`。随后须以manifest绑定的leader/grandchild PID/PGID/SID核验受管cleanup，最终对exact PGID作absence probe；四阶段cleanup必须恰为`term/kill/wait_reap/verify`四个mapping，每项含`phase/attempted/outcome`，并绑定真实signal trace、wait trace与probe trace。未实际发生的TERM/KILL须写`attempted=false/not_required`，不得以固定四字符串冒充动作。`target_absent=true`仅在direct child已reap、登记PID均消失且最终PGID probe为absent后成立。
4. **冻结输出与marker Oracle**：`stdout_short`成功输出精确为stdout `b"fixture-short-stdout\n"`（21 bytes，SHA256 `31a4f97e50dcaff8cf73da9e16143f07598f4d51623e76b96eeb11e290a052bd`）和stderr `b"fixture-short-stderr\n"`（21 bytes，SHA256 `52f9ffd3b99c00ced3109c306dd52f58be09c814f312759532cb4f7d05da6f21`）；必须来自exact handle的实际pipe capture，常量只能作为比较Oracle。marker稳定检查只能在direct child reap且PGID absent之后开始，固定fixture tick=`0.05s`、quiet window=`0.15s`、poll interval=`0.02s`、timeout=`2.0s`。每个样本记录`monotonic/size/mtime_ns/sha256`；至少9个连续样本的monotonic须严格递增，九个样本的snapshot `(size,mtime_ns,sha256)`须完全相同，且末次monotonic减首次monotonic须`>=0.15s`。receipt另绑定final bytes/size/mtime/SHA256。pure card须分别覆盖时间不递增、late write、mtime/size/digest漂移、样本不足和时间跨度不足。
5. **互斥终态与失败证据**：状态只能沿下列路径产生，任何失败不得发布PASS或被重跑覆盖。
   - scope创建前拒绝：输出bounded canonical `REJECTED_PRE_SCOPE`记录；它绑定schema/case/test/run、producer/dependency/production hashes、exact argv/env digest、枚举reason和`post_hash=true`，明确`scope_created=false`，没有scope也没有cleanup动作。
   - scope创建后且terminal已独立证明：在scope内原子发布`FAIL_TARGET_SCOPE_RETAINED`，绑定scope dev/inode/uid、known-tree、原始evidence digests、terminal proof、失败reason与`post_hash=true`，保留现场等待Review。
   - terminal不能证明：原子发布`QUARANTINED_TARGET_SCOPE_RETAINED`，绑定同一scope与最后可证trace；禁止删除、重试或签发后续target。后续只能由另一个hash-pinned recovery/reverification工件**追加**独立schema=`sec-exec-posix-target-recovery/v1`、status=`TARGET_TERMINAL_RECOVERY_PROVEN`的receipt；该receipt须绑定原quarantine receipt SHA、scope dev/inode/uid、原登记PID/PGID/SID、当前raw absence trace/digest、producer/dependency/production hashes与`post_hash=true`。它不得修改/替换原quarantine receipt；只有该recovery receipt先经双独立Review，才能作为“terminal已证明”的release source进入受控清理。
   runner/watchdog只负责其已登记资源；不得由operator手工`kill/killpg`、猜PID或先删scope再补证据。
6. **Canonical success receipt**：状态固定为`PASS_TARGET_SCOPE_RETAINED`，并绑定schema、case/test/run、producer/dependency/production hashes、scope dev/inode/uid、known-tree digest、执行argv digest、strong-handle identity、leader/grandchild PID/PGID/SID、spawn observation/ACK digest、terminal reason、raw wait/signal/probe/output/marker trace digests、四阶段cleanup evidence/digest、`target_absent=true`、`direct_child_reaped=true`、marker final digest/size/mtime与稳定样本摘要、`post_hash=true`和unittest全零非成功计数。字段、顺序和exact canonical bytes须先由pure card重构并独立复审。
7. **Producer-bound cleanup与receipt**：成功scope或已证明terminal absent的失败scope，均须先由两名独立reviewer核tree/stat/origin+release receipt chain/absence，才可由同hash runner的独立`--verify-clean`路径，以已固定dev/inode的parent dirfd、no-follow、known-tree、identity与late-write重验删除。cleanup receipt的schema精确为`sec-exec-posix-target-cleanup/v1`，字段精确绑定`schema/run_id/case/test/origin_status/origin_receipt_sha256/release_status/release_receipt_sha256/source_status/source_receipt_sha256/scope_dev/scope_ino/scope_uid/parent_dev/parent_ino/producer_hash/dependency_hashes/production_hash/preclean_known_tree_digest/delete_trace_digest/postclean_parent_dirfd_absence_trace_digest/scope_absent/post_hash/status`。
   - 直接成功或已证明terminal的失败：`origin`分别为`PASS_TARGET_SCOPE_RETAINED`或`FAIL_TARGET_SCOPE_RETAINED`，`release_status=NOT_REQUIRED`、`release_receipt_sha256=null`，`source=origin`。
   - quarantine释放：`origin_status=QUARANTINED_TARGET_SCOPE_RETAINED`且绑定原receipt；`release_status=TARGET_TERMINAL_RECOVERY_PROVEN`且绑定上一条独立recovery receipt；`source=release`。缺任一链节均禁止删除。
   delete trace须逐项绑定dirfd-relative删除的name/dev/inode/outcome；删除后在同一仍打开的parent dirfd上重核parent dev/inode未变，并以no-follow scope-name stat=`ENOENT`及同run-id前缀枚举为空形成raw absence trace。只有该raw trace可产生`scope_absent=true`与其digest；`post_hash=true`不能替代absence。success清理状态为`TARGET_SCOPE_CLEANUP_COMPLETE`，失败清理为`FAILURE_SCOPE_CLEANUP_COMPLETE`。任一步中断/异常只允许无cleanup receipt的保留状态。receipt字段、顺序、canonical bytes、stdout exact match、raw delete/absence trace及`scope_absent=true`须由pure card重构和mutation负测；cleanup完成后还必须由两名独立reviewer再次核receipt、parent-dirfd absence evidence和exact root absence，才可把scope记为已释放。target仍存活、身份不确定、terminal unknown、recovery未审、tree漂移、late write或receipt chain不匹配时，禁止删除且不得发布cleanup receipt。
8. **平台残余接受边界**：仅适用于单用户、同UID、hash-pinned可信fixture与可丢弃Workspace；`Popen`返回至spawn-observed发布窗、无pidfd的PID reuse TOCTOU、`killpg/waitpid`与文件系统syscall hard-wall、same-UID替换/hardlink、setsid/double-fork/恶意依赖和host sandbox均不被形式化消除。若需要敌对代码或硬实时保证，固定结论为`SANDBOX_REQUIRED`，不得用本工件降级放行。

本节合同与qualified-owner H Oracle先由Gate-02两名原reviewer独立复核为`APPROVE/blocking=0`；随后artifact以pure/static卡、默认constructor=0、Python3.9 compile、唯一Popen/no-bypass及成功/拒绝/失败/隔离/cleanup mutation矩阵收口，并只执行一次`stdout_short`开发smoke。artifact批准和这次窄执行都不是持续execution授权，更不是`KEEP`或Runtime Acceptance；不得据此重跑或扩展到其他target。

### 4.4 统一执行实现与 Composition Root（当前候选）

`TRACE-20260826-043`～`059` 保存了实现、失败复审、修正和复核的完整链。当前候选完成：

- Core-owned 五个版本化 Profile、exact argv registry、绝对 executable、最小环境与 opaque/global one-shot admission；
- 单一生产 `subprocess.Popen` owner，Core、Legacy、VisionForge 前后台入口全部委托；
- cleanup absolute deadline、真实 phase outcome、Workspace cleanup fence/quarantine/recovery、reader/watchdog terminal gate；
- bounded head/tail、原文 chars/SHA、Core/Browser 单一脱敏事实源、UTF-8 replacement 与 post-spawn 异常重建；
- Core/DAG/CLI/Web/eval 的 exact-bool、Composition-owned fresh approver；trial runner 按实际 project root 单独绑定。

当前 production 静态扫描只命中：

```text
coding_workflow/local_execution.py: subprocess.Popen(...)
```

`subprocess.run`、shell、spawn/fork/exec 等第二执行边界为 0。这个结论是仓库源码静态门禁，不代表 native extension 或敌对依赖无法绕过 Python。

### 4.5 当前 mock/structural 证据与复审链

当前behavior hash=`1ce0cc46…8f03a0`与结构卡组合后，在清空父环境并用永久audit/tripwire的专用解释器运行：`25/25 pass`，0 failure/error/skip，unittest耗时`29.140s`。同一target-entry gate还重跑H `2/2`（`5.899s`）、POSIX safety `39/39`（`0.361s`）、runner pure card `32/32`（`0.045s`）、默认smoke `3 tests / 1 pass / 2 skip / constructor_calls=0`，全部0 failure/error；均未执行target。Gate-02最终双独立Review均为`APPROVE/blocking=0`，详见`TRACE-20260826-111`～`115`；批准范围仅为qualified-owner Oracle与future evidence contract。较早明确列举、不会触发真实进程的父级组合运行supervisor、approval、eval composition、Browser脱敏与exact-policy为`56/56 pass`，最终supervisor专项为`28/28 pass`。

实现不是一次审查通过：

1. `TRACE-046`：`REVISE`，发现开放 Core argv、foreground totality、无界 background reader/lease、5 秒 cleanup 与 Renderer/root 问题；
2. `TRACE-049`：`REVISE`，发现 poll 绕 Finalizer、quoted secret、abandon 强引用、terminal/quarantine 竞态与 eval root；
3. `TRACE-052`：`REVISE`，发现自然退出残留 PGID、escaped/JSON secret 与 invalid UTF-8 exception bytes；
4. `TRACE-055`：安全线批准上述三项，但架构线 `REVISE`，发现已 reap leader 没有真实一秒 TERM grace；
5. `TRACE-058`：原 finding reviewer 对 50ms disappearance/no-KILL 与完整一秒 grace/KILL 的纯 mock 修正给 `APPROVE`、blocking 0。

当前所有**已复现的 mock/structural blocker**均有对应首红、修复、终绿和独立窄复审。批准范围仍不包含真实进程生命周期、真实 Browser 或最终 `KEEP`。

### 4.6 合规偏差与不得冒领的证据

`TRACE-054` 记录一次实现代理误跑完整 `tests.test_command_validators`：它启动了多次受信 `/usr/bin/python3` workload，timeout 路径包含真实 cleanup signal；全部位于临时目录、没有网络/模型/真实秘密或仓库外写入。该运行违反当批 pure-mock 预注册边界，结果 `10/11` 被整体排除，**不是合规通过证据**。

较早 IMPL-05 哈希曾执行 non-behavior full discovery：`run=481, pass=477, skip=4, 0F/0E`。最终 IMPL-07 哈希没有重跑这个会触发真实 subprocess 的完整集合，因此该全量结果只保留为历史正常路径证据，不可描述成当前最终哈希的 full regression。下一次全量/真实 Browser/POSIX 运行必须另行预注册明确授权和安全夹具。

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
- `TRACE-20260826-002`：以隔离父环境重新执行 structural、behavior、combined、POSIX mock safety 和 baseline 的精确 cwd/命令/exit/计数/耗时；
- `TRACE-20260826-043`～`059`：approval、统一实现、Composition Root、四轮 `REVISE`、逐项修正、合规偏差与当前 mock/structural checkpoint。
- `TRACE-20260826-091`～`097`：watchdog-only唯一真实执行、raw evidence、exact cleanup及双Review；
- `TRACE-20260826-098`～`104`：arm→ACK→disarm零target执行、raw correction、execution/cleanup双Review及exact cleanup。
- `TRACE-20260826-105`～`121`：target合同修订、默认禁用artifact、pure/static双Review、单次`stdout_short`开发smoke及受控cleanup；不外推为production integration。
- `TRACE-20260826-122`～`124`：单次Legacy `ProjectWorkspace` production path窄证据。
- `TRACE-20260827-125`～`149`：CLI可见报告、Guard-backed timeout+pure quarantine、VisionForge preflight、168项聚焦回归和五批scope/hash checkpoint。

历史回填没有保存的原始 runner 或命令明确标为 `MISSING/UNKNOWN`，不得从摘要补造。本次同步基线为tail `TRACE-20260826-104`、STEP-LOG SHA256 `defef02ef5f8f69ae9e98ba986c508a4af5e9c9e762e34063643bd179382c53d`；当前 Step Log、SEC report 与红卡仍是 `WORKTREE_ONLY`，尚无本批 Git commit，因此它们是 content-hash checkpoint，不是 clean release checkpoint。

## 6. 后续实现与验证顺序

1. mock/structural实现、两档真实POSIX零target窄证据、Gate-02合同、默认禁用target artifact及一次`stdout_short`开发smoke均已完成；状态仍为`INCONCLUSIVE / KEEP_NOT_ISSUED`。
2. target artifact与窄receipt已经存在，但只覆盖一次可信fixture开发smoke，不是重复执行授权、完整PGID/port/marker adversarial或production Runtime验收。继续禁止未经新PRE_REGISTER的returned tuple、target `Popen`、`success_orphan`、端口/崩溃workload和完整真实测试集合。
3. 完整 SEC-EXEC 实现/测试/证据闭包已提交并推送；当前作品集主线改为 `MVP-CLOSE-01`。更多POSIX target、真实Browser/Renderer、full regression和增强版`PROD-01B-3B-2`全部后置，恢复时必须分别另行PRE_REGISTER，不能合并推进。
4. 若未来恢复安全认证，Browser先冻结ReferenceImageRenderer（预渲染hash-pinned资产或明确Profile）与Profile-owned browser binary，再迁移4个陈旧E2E fixture，不能恢复任意environment注入。
5. 未来获授权的真实Browser正常对照、对应最终哈希full regression、compileall、静态no-bypass与`git diff --check`全部通过后，再进行独立最终Review。
6. 只有上述required门禁全部通过且final Review blocking finding为0，未来才能决定`KEEP (local_trusted_execution/v1 only)`；在此之前保持`INCONCLUSIVE`。MVP 完成不得被解释成安全 `KEEP`、Runtime Acceptance 或生产沙箱验收。

## 7. Harness Evolution / INC 联动

- `lifecycle_status=IMPLEMENTED_CANDIDATE`，`decision=INCONCLUSIVE`；当前 mutation 只允许统一本地执行边界与其验证夹具，不同时改变模型、Prompt、路由或 Outbox。
- 真实模型、Evolver、Validation/Held-out、query budget、样本量和统计效果为 `N/A`；本批证明确定性安全边界，不主张模型能力提升。
- 风险目录增加父环境秘密继承、错误 executable、reserved/symlink escape、cleanup failure、残留进程/端口、raw output secret 和未登记 subprocess bypass。
- 当前已有开发期mock/structural首绿、正常对照及两档真实零target POSIX窄证据，但target-bearing POSIX与真实Browser Detector证据仍缺；没有生产IncidentSignal/Ledger、Replay、MTTD/MTTR，也不提前完成`INC-01`。
- 所有 sentinel 都是明确标记的假值；未读取 `.env`，未调用真实模型、外部网络或外部服务。
