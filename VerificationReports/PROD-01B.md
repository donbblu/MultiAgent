# PROD-01B VerificationReport

> 本文件是 `PROD-01B` 测试、挑战、缺陷、修复与决策证据的权威入口。`Plan` 负责定义应当实现什么，测试代码负责提供可执行 Oracle，本报告负责记录实际发生了什么；`HANDOFF` 与 Backlog 只保存摘要和本文件链接。若摘要与本报告冲突，以受测文件哈希匹配的本报告条目为准。

> 2026-08-27 路线注记：本报告继续作为已完成 01B 切片的权威历史证据，但不再定义当前执行优先级。报告中的“下一动作”保留其记录发生时的含义；当前先执行 [`Plan29`](../Plan/Plan29.md) 的 `MVP-CLOSE-01`，未完成的 3B-2 与其后生产增强已后置。

## 0. 报告身份与证据规则

| 字段 | 值 |
|---|---|
| `report_schema` | `verification-report/v1` |
| `report_id` | `VR-PROD-01B` |
| `created_at` | `2026-08-25` |
| `last_updated` | `2026-08-27` |
| `contract_ref` | [`Plan/Plan26.md`](../Plan/Plan26.md) |
| `incident_plan_ref` | [`Plan/Plan25.md`](../Plan/Plan25.md) |
| `runtime_acceptance` | `NOT_ISSUED` |

证据状态只使用以下四种值：

- `EXPECTED_RED_ONLY`：能力尚未实现，只证明预先冻结的测试能准确失败；不能算产品缺陷、测试通过或 `KEEP` 依据。
- `FRESH_VERIFICATION`：在本报告绑定的当前文件哈希上重新执行并记录。
- `HISTORICAL_VERIFICATION`：由已有 Plan/HANDOFF、冻结哈希或提交重建；缺失的原始日志不得补造。
- `STALE_OR_INVALID`：报告与受测文件哈希不匹配，或证据不足以支持原结论。

问题分类：

- `PRODUCT_DEFECT`：已经实现且声称应通过的行为被测试或挑战击穿。
- `REGRESSION`：曾经通过的冻结行为重新失败。
- `TEST_DESIGN_DEFECT`：测试 Oracle、自证方式或测试边界错误。
- `CONTRACT_DEFECT`：验收口径存在矛盾或无法形成唯一 Oracle。
- `EVIDENCE_DEFECT`：版本、命令、日志、Review 或哈希不足以复现结论。

只有 `PRODUCT_DEFECT` 和 `REGRESSION` 计入“实现后真实发现的问题”。`EXPECTED_RED` 不计入事故数量、Detector 命中率或覆盖率。

## 1. 当前总览

| 切片 | 状态 | 证据状态 | 实验生命周期 | 最终决策 |
|---|---|---|---|---|
| `PROD-01B-1` | `COMPLETED` | `HISTORICAL_VERIFICATION` | `COMPLETED` | `KEEP` |
| `PROD-01B-2` | `COMPLETED` | `HISTORICAL_VERIFICATION + FRESH_DIRECTED_RECHECK` | `COMPLETED` | `KEEP` |
| `PROD-01B-3` | `IN_PROGRESS`（3A 与 3B-1 已完成；3B-2 待开始） | `3A=FRESH_VERIFICATION`；`3B-1=FRESH_VERIFICATION` | `3A=COMPLETED`；`3B-1=COMPLETED` | `KEEP (3A)` + `KEEP (3B-1 only)`；父切片 `INCONCLUSIVE` |

当前工作区快照：

| 字段 | 值 |
|---|---|
| branch | `codex/multimodal-coding-mvp` |
| HEAD | `99033147fa0583b6573b8bace58e75fbffda859f`（`feat: add atomic runtime outbox intents`） |
| worktree | `dirty`；3B-1 生产实现、公开导出、冻结/攻击测试和收口文档尚未提交，必须绑定下列哈希 |
| OS（当前复核） | macOS `26.5` / build `25F71` / arm64 |
| Python | `3.9.6`，`/Library/Developer/CommandLineTools/usr/bin/python3` |
| SQLite | `3.51.0` |
| model/network/external DB | 均未使用 |

当前受测文件哈希：

| 文件 | SHA-256 |
|---|---|
| `demo/coding_workflow/runtime_persistence/_record_codec.py` | `c16b792a9c3c7948e3a0081f89ab04263b9df5d842cfabfe75de019d55825985` |
| `demo/coding_workflow/runtime_persistence/outbox.py` | `85469347d152684529431337778ed87e92b73af899d85427dc268a1168f49a4d` |
| `demo/coding_workflow/runtime_persistence/sqlite.py` | `7d11ba4d80850fd0dd11d11672c5fee3269185527334896e93f6a7c3bb270d50` |
| `demo/coding_workflow/runtime_persistence/state_event.py` | `9ce7f9fe8f3d16a164df748954cdeea25056f263ee09ad9bfb960c4458523f17` |
| `demo/coding_workflow/runtime_persistence/__init__.py` | `2fbd2f21dd3d935b5150d50208965a6987fc09df3999c1d7b15fc37c70b04432` |
| `demo/coding_workflow/__init__.py` | `a3f5ef7779ad6eca5e9c73ef2ae4486d0ef2b992a187e240696ad60e44746350` |
| `demo/tests/test_runtime_sqlite_uow.py` | `d1fa39f95f2475856096b58a2902e1be5b658d67298e8b80c78bd0cf99ee27be` |
| `demo/tests/test_runtime_thread_event_store.py` | `45a04779bd92a81faeab640419cee7ab634c54c324ce1f877ac76d9382bc2d62` |
| `demo/tests/test_runtime_outbox.py` | `8452ba5f2add07c3cd30e75b5c3ce26ceb941984d58f15e2ab5d20f5e3ab948a` |
| `demo/tests/test_runtime_outbox_adversarial.py` | `aa0ce3a68eee6b667281eaf44ffb8d5461b8a085b9a927810c4cfc3b951b28cc` |
| `Plan/Plan26.md`（3B-1 frozen contract，收口前） | `3be814497aeda592345823bb49a6f6cb95ec3d5bbc536e799c08e9c89628c6c5` |
| `Plan/Plan26.md`（收口状态更新后） | `bc1d8b44d82dc94477abc56d990c54f6267fc0e374372fca4b5eda01b728a93f` |
| `demo/tests/test_runtime_outbox_claim_lifecycle.py`（3B-1 EXPECTED_RED） | `550561e149f423d6cb35828ac9fa51ec4a4275155140e3b1a9bea6e276697813` |
| `demo/tests/test_runtime_outbox_claim_lifecycle_adversarial.py` | `0dbff2e2b3173f4d64977b61bd7b7ef8033b14fd028e3cf3f992bf77cceb3323` |

---

## 2. PROD-01B-1：SQLite Migration 与 UnitOfWork

### 2.1 当前结论

| 字段 | 值 |
|---|---|
| contract identity | `PROD-01B-1` frozen contract；历史未分配独立 InvariantCard ID |
| slice status | `COMPLETED` |
| evidence state | `HISTORICAL_VERIFICATION` |
| decision | `KEEP` |
| KEEP 范围 | 组件级 SQLite schema/migration 与显式 `RuntimeUnitOfWork` 事务底座 |
| 不代表 | 完整 State Store、Journal、Outbox、Incident、生产可靠性或 Runtime Acceptance |

### 2.2 版本与文件哈希

原始 Verification 发生在 `HEAD=12f315e103bb3fd4d8879feb9331bb605ea51a64` 的 dirty 工作区。相同内容随后由 commit `b864b20093f20077424fc81a564ecffecbf7ecb0`（`2026-08-25T01:33:48+08:00`，`feat: add runtime sqlite transaction foundation`）固化，以下冻结哈希已通过该 commit 复核：

| 文件 | 冻结 SHA-256 |
|---|---|
| `demo/coding_workflow/runtime_persistence/sqlite.py` | `52aaad07318ed17415bde9686ada2a6fd9b5effe29938beb271f199e7679ba59` |
| `demo/tests/test_runtime_sqlite_uow.py` | `f1ef68b22517bca828f0a5063e297dfad545de345ef1a4db68682afc8416e13e` |
| `demo/coding_workflow/runtime_persistence/__init__.py` | `55f3c756282514b0b97ada356462964aa350cec651feaa867da36688ce4c04bd` |
| `demo/coding_workflow/__init__.py` | `25af66784c95e1231ed7dfff574d3555c1a69a48d38e20765e5f0fec3efea880` |

当前工作树已叠加 01B-2/3，不能用当前文件哈希冒充 01B-1 冻结候选。

### 2.3 测试环境与命令

历史记录明确保存 Python `3.9.6`、SQLite `3.51.0`、文件型临时 SQLite、无模型/网络/外部数据库。历史 OS/build/CPU 未保存；当前 macOS 信息不能倒推为历史元数据。

执行目录：`/Users/donbblu/codex/multiAgent/demo`

```bash
python3 -m unittest tests.test_runtime_sqlite_uow -q
python3 -m unittest discover -s tests -p 'test_runtime_*.py' -q
python3 -m unittest discover -s tests -q
PYTHONPYCACHEPREFIX=/private/tmp/multiagent-pycache python3 -m compileall -q coding_workflow tests
cd /Users/donbblu/codex/multiAgent
git diff HEAD --check
```

### 2.4 测试结果

| run | 类别 | 执行 | 通过 | 失败 | 错误 | 跳过 | 耗时 | 结果 |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `01B1-DIRECTED-FINAL` | regression/fault | 32 | 32 | 0 | 0 | 0 | 0.672s | PASS |
| `01B1-RUNTIME-FINAL` | subsystem | 96 | 96 | 0 | 0 | 0 | 0.658s | PASS |
| `01B1-FULL-FINAL` | full regression | 309 | 305 | 0 | 0 | 4 | 21.558s | PASS |
| `01B1-COMPILE` | static gate | N/A | exit 0 | 0 | 0 | N/A | 未记录 | PASS |
| `01B1-DIFF` | static gate | N/A | exit 0 | 0 | 0 | N/A | 未记录 | PASS |

4 个 skip 均为需要显式启用真实 Node/pnpm/Chromium 环境的 VisionForge E2E。历史原始 stdout 未单独保存；上述为 Plan/HANDOFF 中的结构化结果。

### 2.5 故障注入、崩溃与并发结果

| 风险/场景 | 注入或调度 | Oracle | 实际结果 | 回归测试 |
|---|---|---|---|---|
| migration 半提交 | commit 前抛错 | schema/metadata/legacy 数据 all-or-none | 零部分 schema；随后可重试 | [`test_migration_fault_before_commit_leaves_no_partial_schema`](../demo/tests/test_runtime_sqlite_uow.py#L484) |
| begin 后故障 | `UOW_AFTER_BEGIN` 抛错 | rollback、close、释放锁 | 下一 UoW 可提交 | [`test_fault_after_begin_releases_lock_for_next_uow`](../demo/tests/test_runtime_sqlite_uow.py#L776) |
| commit 前故障 | `UOW_BEFORE_COMMIT` 抛错 | 两张探针表均为 0 | 无部分写 | [`test_fault_before_commit_is_deterministic_and_atomic`](../demo/tests/test_runtime_sqlite_uow.py#L750) |
| commit 前进程退出 | 子进程 `os._exit(91)` | 重开后 none | `(0,0)`，integrity 通过 | [`test_process_exit_before_commit_is_none_after_reopen`](../demo/tests/test_runtime_sqlite_uow.py#L924) |
| commit 后进程退出 | 子进程 `os._exit(92)` | 重开后 all | `(1,1)`，integrity 通过 | [`test_process_exit_after_commit_is_all_after_reopen`](../demo/tests/test_runtime_sqlite_uow.py#L934) |
| writer lock | 另一连接持有写锁 | typed busy、零泄漏、可重试 | `RuntimeDatabaseBusyError`，释放后恢复 | [`test_busy_begin_has_typed_error_and_leaves_no_open_uow`](../demo/tests/test_runtime_sqlite_uow.py#L611) |
| 跨线程 UoW | 非 owner 调用 | typed 拒绝；owner 仍能 rollback | 通过 | [`test_uow_rejects_cross_thread_use_and_owner_can_still_rollback`](../demo/tests/test_runtime_sqlite_uow.py#L889) |
| SQLite 自动 rollback | `INSERT OR ROLLBACK` | 事务丢失后 fail-closed | UoW FAILED；新 UoW 可恢复 | [`test_conflict_rollback_cannot_escape_the_uow_state_machine`](../demo/tests/test_runtime_sqlite_uow.py#L850) |

这里没有完成真实 `kill -9`、断电、多 writer 压测、吞吐/p95、公平性或长时 soak；不得用本表外推这些能力。

### 2.6 实际发现的问题、修复与回归

| ID | 分类 | 实际问题 | 修复位置 | 回归测试 | 状态 |
|---|---|---|---|---|---|
| `DEF-01B1-001` | PRODUCT_DEFECT | 原始 connection 或 SQL `COMMIT` 可绕过显式 UoW | [`sqlite.py`](../demo/coding_workflow/runtime_persistence/sqlite.py) 的结果包装与 authorizer | [`test_sql_cannot_bypass_the_explicit_uow_commit_boundary`](../demo/tests/test_runtime_sqlite_uow.py#L839)、[`test_uow_and_sql_results_do_not_expose_a_raw_connection`](../demo/tests/test_runtime_sqlite_uow.py#L875) | FIXED |
| `DEF-01B1-002` | PRODUCT_DEFECT | ALTER/DDL authorizer 参数判断存在旁路 | [`sqlite.py`](../demo/coding_workflow/runtime_persistence/sqlite.py) 的 schema DDL action gate | [`test_uow_rejects_schema_ddl_and_mutable_pragmas`](../demo/tests/test_runtime_sqlite_uow.py#L575) | FIXED |
| `DEF-01B1-003` | PRODUCT_DEFECT | 结果迭代器泄露底层 cursor/connection | [`RuntimeSQLResult`](../demo/coding_workflow/runtime_persistence/sqlite.py#L285) 不再返回 cursor | [`test_uow_and_sql_results_do_not_expose_a_raw_connection`](../demo/tests/test_runtime_sqlite_uow.py#L875) | FIXED |
| `DEF-01B1-004` | PRODUCT_DEFECT | `INSERT OR ROLLBACK` 终止外层事务后 UoW 仍可能继续 | [`sqlite.py`](../demo/coding_workflow/runtime_persistence/sqlite.py) 每次 SQL 后检查 `in_transaction` | [`test_conflict_rollback_cannot_escape_the_uow_state_machine`](../demo/tests/test_runtime_sqlite_uow.py#L850) | FIXED |
| `DEF-01B1-005` | PRODUCT_DEFECT | rollback failure 会隐藏 body failure | [`sqlite.py`](../demo/coding_workflow/runtime_persistence/sqlite.py) 保留异常链并抛 typed rollback error | [`test_body_and_rollback_failures_are_both_observable`](../demo/tests/test_runtime_sqlite_uow.py#L689) | FIXED |
| `DEF-01B1-006` | PRODUCT_DEFECT | WAL/Schema 检查时序存在 TOCTOU 或拒绝前变更数据库 | [`sqlite.py`](../demo/coding_workflow/runtime_persistence/sqlite.py) 在写事务内重检并调整初始化顺序 | [`test_uow_rechecks_wal_inside_its_write_transaction`](../demo/tests/test_runtime_sqlite_uow.py#L546)、[`test_future_schema_rejection_does_not_change_journal_mode`](../demo/tests/test_runtime_sqlite_uow.py#L377) | FIXED |
| `DEF-01B1-007` | PRODUCT_DEFECT | REAL `1.5` migration version 被 `int()` 强转成合法版本 | [`sqlite.py`](../demo/coding_workflow/runtime_persistence/sqlite.py) 转换前严格类型校验 | [`test_non_integer_ledger_version_fails_closed_without_coercion`](../demo/tests/test_runtime_sqlite_uow.py#L447) | FIXED |

历史证据只保留了最终缺陷清单和回归，没有逐缺陷首次失败的原始命令、stdout、发现时间和修复前文件哈希。这是 `EVIDENCE_DEFECT`，不能事后补造。

### 2.7 未覆盖风险

- 领域 Repository、真实 State+Event mutation、Journal、Outbox、Budget、Acceptance。
- durable Invocation、lease/fencing/cancel/Reaper。
- Detector、Incident Ledger、Replay、Web、PostgreSQL。
- 磁盘满/损坏、真实 SIGKILL/断电、容量与 soak。
- 连接/路径建立失败的异常封装仍是非阻塞后续项。

### 2.8 独立 Review 与最终决策

| 字段 | 记录 |
|---|---|
| recorded recommendations | `APPROVE`、`APPROVE WITH NOTES` |
| reviewer principals | `UNKNOWN`（历史未保存） |
| raw ReviewArtifact | `MISSING` |
| Runtime Acceptance | `NOT_ISSUED` |
| final decision | `KEEP` |
| rollback trigger | 32 项冻结回归或兼容迁移门禁重新失败；出现不可修复数据损坏 |

`KEEP` 只保留 01B-1 的事务底座。Reviewer 原文缺失使“Review 过程可追溯性”保持 `INCONCLUSIVE`，但不推翻由冻结 commit、哈希、32 项专项与全量门禁支持的窄范围 `KEEP`。

---

## 3. PROD-01B-2：Thread current-state + RuntimeEvent 原子纵切

### 3.1 当前结论

| 字段 | 值 |
|---|---|
| invariant | `INV-PROD-01B-2-THREAD-EVENT-ATOMICITY-v1` |
| slice status | `COMPLETED` |
| evidence state | `HISTORICAL_VERIFICATION + FRESH_DIRECTED_RECHECK` |
| decision | `KEEP` |
| KEEP 范围 | concrete Thread current-state、append-only RuntimeEvent、原子 mutation、最小完整性读取 |
| 不代表 | 完整 State Store/Journal、Outbox、Incident、生产可靠性或 Runtime Acceptance |

### 3.2 版本与文件哈希

最终候选绑定 `HEAD=b864b20093f20077424fc81a564ecffecbf7ecb0` 的 dirty 工作区，不能只使用 HEAD，也不能使用已经叠加 3A 的第 1 节当前哈希冒充 01B-2 候选。当前 68 项重跑只能证明 3A 叠加后的兼容回归，不替换以下冻结 manifest：

| 文件 | 01B-2 冻结 SHA-256 |
|---|---|
| `demo/coding_workflow/runtime_persistence/sqlite.py` | `4e052962d0047b90d0872136044ca4c5d80dadaad3c7e854910ab1bd145b497d` |
| `demo/coding_workflow/runtime_persistence/state_event.py` | `ba1a6974b067666b6eb12b7f41431861c8ea672645e301dbbd3d1f5628c26a2c` |
| `demo/coding_workflow/runtime_persistence/__init__.py` | `41b0fc9d1e5de90206370452d0891588acbb36d9908f67bd60a797e2e8867f41` |
| `demo/coding_workflow/__init__.py` | `5a3ff4ff3358b5046aecb1a8cf90e92dd6b62ded314fe0d0c7851fe0eeb8180d` |
| `demo/tests/test_runtime_sqlite_uow.py` | `e1e07c5c47c33112f0f9a35ac73e188a8b6ad491f7390f37daa5327eca8416fd` |
| `demo/tests/test_runtime_thread_event_store.py` | `c1c2e700283b48e77c80ac15ab25da7a9d08bd4ae55eaa4c2f989f1bfc7b7f2c` |

### 3.3 测试环境与命令

历史与当前复核均为 Python `3.9.6`、SQLite `3.51.0`；测试在 `tempfile.TemporaryDirectory()` 中使用独立文件型 SQLite，不调用模型、网络或外部数据库。历史 OS/arch 未保存；当前复核为 macOS 26.5 arm64。

执行目录：`/Users/donbblu/codex/multiAgent/demo`

```bash
python3 -m unittest tests.test_runtime_sqlite_uow tests.test_runtime_thread_event_store -q
python3 -m unittest discover -s tests -p 'test_runtime_*.py' -q
python3 -m unittest discover -s tests -q
PYTHONPYCACHEPREFIX=/private/tmp/multiagent-pycache python3 -m compileall -q coding_workflow tests
cd /Users/donbblu/codex/multiAgent
git diff --check
```

### 3.4 测试结果

| run | 证据时间 | 执行 | 通过 | 失败 | 错误 | 跳过 | 耗时 | 结果 |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `01B2-DIRECTED-FINAL` | 历史收口 | 68 | 68 | 0 | 0 | 0 | 未保存 | PASS |
| `01B2-DIRECTED-RECHECK` | 2026-08-25 当前复核 | 68 | 68 | 0 | 0 | 0 | 1.628s | PASS |
| `01B2-RUNTIME-FINAL` | 历史收口 | 132 | 132 | 0 | 0 | 0 | 未保存 | PASS |
| `01B2-FULL-FINAL` | 历史收口 | 345 | 341 | 0 | 0 | 4 | 未保存 | PASS |
| `01B2-COMPILE/DIFF` | 历史与当前 | N/A | exit 0 | 0 | 0 | N/A | 未保存 | PASS |

01B-3 红卡阶段的当时全量曾因 5 项预期失败而为红；这不推翻绑定旧测试集合的 01B-2 final gate。该阶段已经结束，当前工作区的最终全量状态以 4.3 节的 372 项结果为准；“345 全绿”仅是 01B-2 历史收口证据。

### 3.5 故障注入、崩溃与并发结果

| 风险/场景 | Oracle | 实际结果 | 回归测试 |
|---|---|---|---|
| v2 migration commit 前故障 | 精确保持 v1，随后可升级 | 通过 | [`test_v2_migration_fault_leaves_released_v1_exactly_unchanged`](../demo/tests/test_runtime_sqlite_uow.py) |
| State 写后 / Event 写后故障 | Thread/Event 均为 none | 通过 | [`test_both_intra_apply_fault_windows_roll_back_state_and_event`](../demo/tests/test_runtime_thread_event_store.py) |
| commit 前故障 | Thread/Event 均为 none | 通过 | [`test_commit_fault_rolls_back_state_and_event`](../demo/tests/test_runtime_thread_event_store.py) |
| 子进程 State 后、Event 后、commit 后退出 | commit 前 none；commit 后 both | 通过 | [`test_process_exit_before_commit_recovers_none_and_after_commit_recovers_both`](../demo/tests/test_runtime_thread_event_store.py) |
| 两线程同 expected version 竞争 | 恰好一个 winner，另一个 typed conflict | 通过 | [`test_concurrent_same_expected_version_has_one_atomic_winner`](../demo/tests/test_runtime_thread_event_store.py) |
| raw Event rewrite/delete/replace | append-only，全部拒绝 | 通过 | [`test_raw_sql_cannot_rewrite_or_delete_an_appended_event`](../demo/tests/test_runtime_thread_event_store.py)、[`test_raw_insert_or_replace_cannot_rewrite_an_appended_event`](../demo/tests/test_runtime_thread_event_store.py) |
| JSON/digest/link 腐败 | 读取或 integrity scan fail-closed | 通过 | [`test_event_json_and_digest_corruption_fail_closed`](../demo/tests/test_runtime_thread_event_store.py)、[`test_tampered_thread_to_last_event_link_fails_closed`](../demo/tests/test_runtime_thread_event_store.py) |

限制：进程测试使用 `os._exit()`；并发是同机线程，不是多进程、多节点或 soak。

### 3.6 实际发现的问题、修复与回归

| ID | 分类 | 实际问题 | 修复位置 | 回归测试 | 状态 |
|---|---|---|---|---|---|
| `DEF-01B2-001` | PRODUCT_DEFECT | `INSERT OR REPLACE` 绕过 UPDATE/DELETE trigger 改写历史 Event | [`sqlite.py`](../demo/coding_workflow/runtime_persistence/sqlite.py) collision INSERT trigger | [`test_raw_insert_or_replace_cannot_rewrite_an_appended_event`](../demo/tests/test_runtime_thread_event_store.py) | FIXED |
| `DEF-01B2-002` | PRODUCT_DEFECT | 隐式 rowid collision 仍可让 REPLACE 改写 Event | [`sqlite.py`](../demo/coding_workflow/runtime_persistence/sqlite.py) 使用 `WITHOUT ROWID` | [`test_event_table_has_no_hidden_rowid_replace_channel`](../demo/tests/test_runtime_thread_event_store.py) | FIXED |
| `DEF-01B2-003` | PRODUCT_DEFECT | Store 可误用另一个数据库的 UoW，静默写错库 | [`state_event.py`](../demo/coding_workflow/runtime_persistence/state_event.py) 校验 database identity | [`test_store_rejects_uow_from_another_database_and_rolls_it_back`](../demo/tests/test_runtime_thread_event_store.py) | FIXED |
| `DEF-01B2-004` | PRODUCT_DEFECT | 跨线程 apply 失败后的 abort 覆盖原 typed error | [`sqlite.py`](../demo/coding_workflow/runtime_persistence/sqlite.py) 不让 foreign thread 关闭 owner connection | [`test_cross_thread_apply_preserves_typed_error_and_owner_rollback`](../demo/tests/test_runtime_thread_event_store.py) | FIXED |
| `DEF-01B2-005` | PRODUCT_DEFECT | 历史 Event 腐败时 exact retry 误报成功 | [`state_event.py`](../demo/coding_workflow/runtime_persistence/state_event.py) duplicate 路径先解码校验 Event | [`test_exact_retry_fails_closed_when_durable_event_is_corrupt`](../demo/tests/test_runtime_thread_event_store.py) | FIXED |
| `DEF-01B2-006` | PRODUCT_DEFECT | 当前 Thread head 缺失时历史 retry 误报成功 | [`state_event.py`](../demo/coding_workflow/runtime_persistence/state_event.py) 要求 current head 存在且版本不回退 | [`test_exact_retry_fails_closed_when_current_thread_head_is_missing`](../demo/tests/test_runtime_thread_event_store.py) | FIXED |
| `DEF-01B2-007` | PRODUCT_DEFECT | 最新 head Event 腐败时旧 retry 误报成功 | [`state_event.py`](../demo/coding_workflow/runtime_persistence/state_event.py) 解码 current last Event | [`test_old_retry_fails_when_current_head_event_is_corrupt`](../demo/tests/test_runtime_thread_event_store.py) | FIXED |
| `DEF-01B2-008` | PRODUCT_DEFECT | integrity scan 漏掉 orphan/领先于 Thread head 的 Event | [`state_event.py`](../demo/coding_workflow/runtime_persistence/state_event.py) 增加 Event→Thread 反向扫描 | [`test_exact_retry_fails_closed_when_current_thread_head_is_missing`](../demo/tests/test_runtime_thread_event_store.py) 及 integrity fixture | FIXED |

上述都是“实现已存在并声称应正确，随后被挑战击穿”的真实 pre-release 缺陷。01B-2 的最初 EXPECTED_RED 不在本表中。

### 3.7 未覆盖风险

- Outbox、publish retry、ACK、Consumer Inbox 去重。
- BudgetLedger、Acceptance writer、producer authorization。
- 其他 aggregate Repository、完整 Journal 查询、旧 Executor/Web 接线。
- Detector、Incident Ledger、Replay。
- 多进程/多节点并发、容量、长期 soak、真实 SIGKILL/断电/磁盘满或损坏。
- PostgreSQL 与分布式数据库。
- 拥有 SQLite 文件写权限的主体仍可删 trigger 或直接修改文件；本切片不是 DB RBAC 安全边界。
- 01B-2 仍未提交为 clean checkpoint；原始首次失败日志和两份完整 ReviewArtifact 缺失。

### 3.8 独立 Review 与最终决策

| 字段 | 记录 |
|---|---|
| recorded recommendations | `APPROVE`、`APPROVE` |
| reviewer principals | `UNKNOWN`（历史未写入仓库） |
| raw ReviewArtifact | `MISSING` |
| Runtime Acceptance | `NOT_ISSUED` |
| final decision | `KEEP` |
| rollback trigger | 68 项冻结回归、原子性、append-only、幂等或 corruption fail-closed 门禁重新失败 |

`KEEP` 只保留 Thread+RuntimeEvent 原子纵切，不代表完整 PROD-01B 或生产验收。

---

## 4. PROD-01B-3：Event + Outbox

### 4.1 当前结论

| 字段 | 值 |
|---|---|
| invariant | `INV-PROD-01B-3-EVENT-OUTBOX-ATOMICITY-v1` |
| slice status | `IN_PROGRESS`；`01B-3A` 与 `01B-3B-1` 已完成，`01B-3B-2` 待开始 |
| evidence state | `01B-3A=FRESH_VERIFICATION`；`01B-3B-1=FRESH_VERIFICATION` |
| lifecycle | `01B-3A=COMPLETED`；`01B-3B-1=COMPLETED`；父切片仍为 `IN_PROGRESS` |
| decision | `KEEP (01B-3A)` + `KEEP (3B-1 only)`；完整 `01B-3=INCONCLUSIVE` |
| Runtime Acceptance | `NOT_ISSUED` |

3A 只保留显式 `OutboxPolicy`、Schema v3、真实 v2→v3 cutover、Thread+Event+Outbox 同事务 enqueue、完整性与 exact retry。3B-1 只保留本地 SQLite claim/NACK/expiry-reclaim 所有权状态机。Transport、publish/ACK/Receipt 和 Consumer 仍未实现，两个 `KEEP` 都不能外推为可靠发布、完整 01B-3 或完整 PROD-01B。

### 4.2 3A 历史收口版本与测试设计

以下是 3A 收口当时绑定 dirty workspace 的历史候选，必须使用下列完整哈希而不是只引用 `HEAD=b864b20093f20077424fc81a564ecffecbf7ecb0`。当前 3B-1 候选以第 1 节和 4.8 的 manifest 为准：

| 文件 | SHA-256 |
|---|---|
| `runtime_persistence/sqlite.py` | `b6c8d36045bc0485e96d653da3856d384cc51d2b5eb735f8a32676a8b56cedb3` |
| `runtime_persistence/state_event.py` | `ee040c59a183532d00aa294d54a15f7109802e4e0cf55b9f7537cb3f28de8480` |
| `runtime_persistence/__init__.py` | `0a4d640662eb2cbf126edbbd37f6e854be17a223504b05b7ba06bd84fdd2817d` |
| `coding_workflow/__init__.py` | `b585c1c7c337dc097f66677cc0f94e14f86b1747a308b6187ea2f7dc68f95698` |
| `test_runtime_outbox.py` | `8452ba5f2add07c3cd30e75b5c3ce26ceb941984d58f15e2ab5d20f5e3ab948a` |
| `test_runtime_outbox_adversarial.py` | `aa0ce3a68eee6b667281eaf44ffb8d5461b8a085b9a927810c4cfc3b951b28cc` |
| `test_runtime_sqlite_uow.py` | `7897b88d7adaef96fa0940fe723cfc882274561935b27e97d38fc6bb7f6343ac` |
| `test_runtime_thread_event_store.py` | `45a04779bd92a81faeab640419cee7ab634c54c324ce1f877ac76d9382bc2d62` |

测试分两层：5 项基础 Oracle 先于实现冻结；22 项独立挑战在首个绿色候选之后逐轮加入，覆盖 migration、故障窗、硬退出、DML 旁路、腐败、Policy 漂移、跨 Scope、并发、WAL 截止时间和旧版本残留对象。只有后者击穿已声称正确的实现时才登记 `PRODUCT_DEFECT`。

### 4.3 测试环境与执行结果

当前复核环境见第 1 节；执行目录 `/Users/donbblu/codex/multiAgent/demo`。

```bash
python3 -m unittest tests.test_runtime_outbox_adversarial -q
python3 -m unittest tests.test_runtime_outbox tests.test_runtime_sqlite_uow tests.test_runtime_thread_event_store -q
python3 -m unittest discover -s tests -p 'test_runtime_*.py' -q
python3 -m unittest discover -s tests -q
PYTHONPYCACHEPREFIX=/private/tmp/multiagent-pycache python3 -m compileall -q coding_workflow tests
cd /Users/donbblu/codex/multiAgent
git diff --check
```

| run | 执行 | 通过 | 失败 | 错误 | 跳过 | 耗时 | exit | 解释 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `01B3-EXPECTED-RED-001` | 5 | 0 | 5 | 0 | 0 | 0.002s | 1 | `EXPECTED_CAPABILITY_ABSENT`：公开 `OutboxPolicy` 尚不存在 |
| `01B3-FIRST-GREEN-001` | 5 | 5 | 0 | 0 | 0 | 0.021s | 0 | 基础能力首次转绿；此时尚未接受 |
| `01B3-ADVERSARIAL-RED-001` | 18 | 14 个方法全绿 | 8 个 failure entry | 1 | 0 | 约 1.6s | 1 | 击穿 5 组产品缺陷；Subtest 数不能与方法通过数相加 |
| `01B3-FULL-CHALLENGE-001` | 368 | 363 | 1 | 0 | 4 | 24.759s | 1 | 全量门禁额外发现并发 initialize 竞态 |
| `01B3-FINAL-REVIEW-RED-001` | 3 | 0 | 2 | 1 | 0 | 未保存 | 1 | 独立 Review 击穿 orphan object、WAL deadline、lifecycle CHECK 三组问题 |
| `01B3-FINAL-REVIEW-RED-002` | 1 | 0 | 1 | 0 | 0 | 未保存 | 1 | 独立 Review 击穿 v2 库残留 v3 受管对象的 preflight |
| `01B3-ADVERSARIAL-FINAL` | 22 | 22 | 0 | 0 | 0 | 2.355s | 0 | 全部独立攻击关闭 |
| `01B3-DIRECTED-FINAL` | 73 | 73 | 0 | 0 | 0 | 1.889s | 0 | 基础 Outbox + 01B-1/2 对照 |
| `01B3-RUNTIME-FINAL` | 159 | 159 | 0 | 0 | 0 | 4.141s | 0 | Runtime 专项全绿 |
| `01B3-FULL-FINAL` | 372 | 368 | 0 | 0 | 4 | 24.803s | 0 | 4 个既有 VisionForge 真实浏览器 E2E 跳过 |
| `01B3-STRESS-RECHECK` | 10 | 10 | 0 | 0 | 0 | 5 轮各 0.724–0.793s | 0 | 每轮执行并发初始化与 WAL deadline 两项，连续 5 轮全绿 |
| `01B3-COMPILE/DIFF-FINAL` | N/A | exit 0 | 0 | 0 | N/A | <1s | 0 | compileall 与 diff-check 均通过 |

`01B3-STRESS-RECHECK` 的每轮精确命令如下，由父 Harness 顺序执行 5 次；没有在 shell 内加入 retry，也没有忽略非零退出：

```bash
python3 -m unittest \
  tests.test_runtime_outbox_adversarial.RuntimeOutboxAdversarialTests.test_concurrent_initialize_is_idempotent_or_rejects_policy_loser \
  tests.test_runtime_outbox_adversarial.RuntimeOutboxAdversarialTests.test_wal_bootstrap_honors_one_busy_timeout_deadline \
  -q
```

历史首轮结构红卡 `6d8684...`（3 项：1 failure + 2 errors）与显式 Policy 红卡 `8452ba...`（5 failures）继续保留为 EXPECTED_RED 证据，不得改写成产品缺陷或最终通过数。

### 4.4 故障注入、并发与崩溃矩阵

| 风险 | 注入/调度 | 结果 | 回归位置 |
|---|---|---|---|
| 真实 v2→v3 | 两个历史 Event 后降级为已发布 v2 fixture | 历史数据不变；逐 Event 建 `LEGACY_SUPPRESSED`；新 Event 才 PENDING | `test_real_v2_upgrade_backfills_exact_legacy_intents` |
| migration 中断 | v3 DDL/backfill 后、commit 前抛错 | schema/ledger/Thread/Event 精确恢复 v2 | `test_v2_migration_fault_restores_exact_schema_ledger_and_data` |
| 坏 v2 升级 | 篡改 Event digest 后升级 | v3 任何写入前 typed 拒绝 | `test_corrupt_v2_is_rejected_before_any_v3_backfill` |
| 三写与 commit 窗口 | state 后、event 后、outbox 后、commit 前抛错 | 每次重开均 `(0,0,0)` | `test_all_three_write_windows_and_commit_window_roll_back_everything` |
| 硬退出 | 上述三窗、commit 前、commit 后 `os._exit` | commit 前 none；commit 后 all | `test_hard_process_exit_recovers_none_or_all_across_write_windows` |
| SQL 旁路 | 公共 UoW/raw UPDATE/DELETE/REPLACE/rowid | 公共路径拒绝；identity/Policy/Receipt 不可重写 | 对应 public/raw DML tests |
| exact retry/腐败 | 删除或篡改 intent/head | 零 healing；typed fail-closed；完整 intent 不重置 | exact retry/head corruption tests |
| 并发 mutation | 同 expected version 与完全相同 mutation | 一个 typed loser；相同 mutation 为 APPLIED+ALREADY_COMMITTED；1 Event:1 Outbox | `test_concurrent_mutations_keep_exactly_one_intent_per_committed_event` |
| 并发 initialize | 50 个 fresh DB 双线程；独立 Review 另跑线程 200+200、跨进程 30+30 | 同 Policy 双成功；异 Policy 恰好一个成功、一个 typed 配置拒绝 | `test_concurrent_initialize_is_idempotent_or_rejects_policy_loser` |
| fresh/旧版本残留对象 | 无 metadata/ledger 的受管对象；合法 v2 ledger 加 v3 保留对象 | 在 WAL 发生任何变化前 typed 拒绝，原对象与 journal mode 不变 | `test_orphan_managed_object_is_rejected_before_wal_mutation`、`test_v2_with_reserved_v3_object_is_rejected_before_wal_mutation` |
| WAL deadline | 读锁阻挡 `PRAGMA journal_mode=WAL`，配置短 `busy_timeout` | 总 wall-clock 受同一个 monotonic deadline 限制 | `test_wal_bootstrap_honors_one_busy_timeout_deadline` |
| lifecycle CHECK | raw SQL 漂移初始 PENDING/LEGACY 的时间投影 | SQLite CHECK 在写入时直接拒绝非法组合 | `test_raw_sql_rejects_initial_lifecycle_timestamp_drift` |

### 4.5 实际发现的问题

当前实现后真实产品缺陷：`10` 组，均已修复并有回归：

| ID | 分类 | 实际问题 | 修复位置 | 回归位置 | 状态 |
|---|---|---|---|---|---|
| `DEF-01B3-001` | PRODUCT_DEFECT | 超 SQLite int64 的 TTL/batch 通过构造，初始化泄漏 `OverflowError` | [`OutboxPolicy`](../demo/coding_workflow/runtime_persistence/sqlite.py#L100) 的构造边界限制 int64；retry delay 同步限制 | `test_policy_rejects_ambiguous_or_unsafe_boundary_values` | FIXED |
| `DEF-01B3-002` | PRODUCT_DEFECT | lone surrogate Policy 文本在 digest 时泄漏 `UnicodeEncodeError` | [`_strict_nonempty_text`](../demo/coding_workflow/runtime_persistence/sqlite.py#L85) 验证 UTF-8 可编码性 | `test_policy_rejects_ambiguous_or_unsafe_boundary_values` | FIXED |
| `DEF-01B3-003` | PRODUCT_DEFECT | 错绑 delivery key 的持久 Outbox 使 enqueue 泄漏裸 `sqlite3.IntegrityError` | [`_enqueue_outbox`](../demo/coding_workflow/runtime_persistence/state_event.py#L661) 映射 typed corruption 并回滚 | `test_enqueue_collision_from_corrupt_identity_is_typed_and_atomic` | FIXED |
| `DEF-01B3-004` | PRODUCT_DEFECT | current-head Outbox 缺失时仍允许下一 mutation，形成 `Event=2/Outbox=1` | [`_decode_thread_row`](../demo/coding_workflow/runtime_persistence/state_event.py#L822) 写新版本前调用 Outbox 完整性复核 | `test_new_mutation_rejects_missing_current_head_outbox_before_writing` | FIXED |
| `DEF-01B3-005` | PRODUCT_DEFECT | 无 Policy 的 read 先创建空 DB，再抛错误类型不稳定的 WAL 错误 | [`_open_read_connection`](../demo/coding_workflow/runtime_persistence/state_event.py#L353) 在文件/连接创建前统一 Policy gate | `test_read_store_without_policy_fails_before_creating_database_file` | FIXED |
| `DEF-01B3-006` | PRODUCT_DEFECT | fresh DB 并发 initialize 偶发 `database is locked` 或 metadata/ledger 假半迁移 | [`initialize`](../demo/coding_workflow/runtime_persistence/sqlite.py#L697) 使用一致 preflight、限定 retry deadline，并在 `BEGIN EXCLUSIVE` 内重检 | `test_concurrent_initialize_is_idempotent_or_rejects_policy_loser` + 独立线程/进程压力 | FIXED |
| `DEF-01B3-007` | PRODUCT_DEFECT | 无 metadata/ledger 但已有保留受管表时被误判为 fresh，失败前先把 DELETE 改成 WAL | [`_inspect_schema`](../demo/coding_workflow/runtime_persistence/sqlite.py#L997) 枚举并拒绝 orphan managed objects | `test_orphan_managed_object_is_rejected_before_wal_mutation` | FIXED |
| `DEF-01B3-008` | PRODUCT_DEFECT | WAL bootstrap 每次 SQLite 调用各自等待，整体可越过 Harness 的 monotonic deadline | [`_ensure_wal`](../demo/coding_workflow/runtime_persistence/sqlite.py#L1188) 每次只使用剩余 deadline 的短 busy timeout并恢复配置 | `test_wal_bootstrap_honors_one_busy_timeout_deadline` | FIXED |
| `DEF-01B3-009` | PRODUCT_DEFECT | 初始 PENDING 与 LEGACY 的时间等式仅由应用校验，raw SQL 可写入 schema-valid 但语义非法组合 | [`sqlite.py` v3 DDL](../demo/coding_workflow/runtime_persistence/sqlite.py#L439) 用 CHECK 固化时间等式 | `test_raw_sql_rejects_initial_lifecycle_timestamp_drift` | FIXED |
| `DEF-01B3-010` | PRODUCT_DEFECT | 合法 v2 ledger 若残留 v3 受管对象，仍会先修改 WAL 再泛化报错 | [`_validate_schema`](../demo/coding_workflow/runtime_persistence/sqlite.py#L1070) 按已发布版本拒绝 future reserved objects | `test_v2_with_reserved_v3_object_is_rejected_before_wal_mutation` | FIXED |

测试自身另发现并修正 5 项 Oracle/证据问题：Policy 自证、隐藏默认 Policy、Policy-row 腐败错误类型过窄、并发 loser 类型过窄，以及用小于人工持锁时长的 busy timeout 强求成功。它们均按 `TEST_DESIGN_DEFECT` 处理，不计入上述 10 组产品缺陷。

### 4.6 未覆盖风险

- 3A 本身未覆盖 claim/expiry-reclaim/NACK；这些已由 4.8 的 3B-1 证据补齐。publish/ACK、Receipt projection、ACK-loss 重投与 Consumer Inbox 去重仍未实现/测试。
- 没有真实 Broker、网络、外部副作用、Detector、Incident Ledger 或 Replay。
- 未覆盖断电、磁盘满、超大 Journal migration 内存峰值、容量/p95/soak、PostgreSQL 和多节点。
- 持有 SQLite 文件 DDL 权限的可信本地进程仍可植入额外 trigger；当前威胁模型不把它当 DB RBAC 边界。
- 4 个真实浏览器 E2E 仍按环境门禁 skip；它们与 3A 无直接覆盖关系。

### 4.7 3A 收口当时的独立 Review 与决定

| 字段 | 记录 |
|---|---|
| reviewer principals | `/root/outbox_adversarial_tests`、`/root/outbox_schema_review`、`/root/outbox_api_review`、`/root/outbox_final_review` |
| independence | 所有 Reviewer 均独立于生产实现；`outbox_final_review`、`outbox_schema_review`、`outbox_api_review` 为只读审查，`outbox_adversarial_tests` 只新增独立测试且未修改生产代码 |
| subject hashes | `sqlite=b6c8d360...`、`state_event=ee040c59...`、`adversarial=aa0ce3a6...`，完整 manifest 见 4.2 |
| recommendation | 最终 `APPROVE`（advisory）；所有旧哈希候选的 approve 均被后续 Review 取代 |
| independent stress | 早期候选曾跑线程同/异 Policy 各 200 轮、跨进程同/异 Policy 各 30 轮；最终哈希另将并发初始化 + WAL deadline 连续复跑 5 轮 |
| blocking findings | 0；10 组产品缺陷均关闭 |
| final ReviewArtifact observations | 22/73/159/372、compileall/diff、4 条旧反例、5 轮组合压力均复跑；另验证 preflight 快照竞态在锁内二次检查时 typed 拒绝、异 Policy fresh initialize 50 轮均单赢家、非 busy/locked I/O 错误只尝试一次并保留原错误 |
| Runtime Acceptance | `NOT_ISSUED` |
| 3A closeout decision | `KEEP (01B-3A only)`；这是 3A 收口当时的决定，当前 3B-1 决定见 4.8；完整 `01B-3` 仍 `INCONCLUSIVE` |

### 4.8 PROD-01B-3B-1：claim / NACK / expiry-reclaim 收口

| 字段 | 记录 |
|---|---|
| invariant | `INV-PROD-01B-3B-1-CLAIM-LIFECYCLE-v1` |
| baseline HEAD | `99033147fa0583b6573b8bace58e75fbffda859f` |
| frozen contract hash | `Plan/Plan26.md=3be814497aeda592345823bb49a6f6cb95ec3d5bbc536e799c08e9c89628c6c5` |
| closure Plan hash | `Plan/Plan26.md=bc1d8b44d82dc94477abc56d990c54f6267fc0e374372fca4b5eda01b728a93f` |
| frozen / adversarial tests | `550561e149f423d6cb35828ac9fa51ec4a4275155140e3b1a9bea6e276697813` / `0dbff2e2b3173f4d64977b61bd7b7ef8033b14fd028e3cf3f992bf77cceb3323` |
| slice / evidence | `COMPLETED / FRESH_VERIFICATION` |
| lifecycle / decision | `COMPLETED / KEEP (3B-1 only)` |
| Runtime Acceptance | `NOT_ISSUED` |

测试设计在实现前固定为 7 个方法：公开 read/DTO 与 Scope 隔离、初始 PENDING claim、当前 owner NACK 与延迟重试、expiry 边界与旧 owner fencing、同 aggregate 顺序与跨 aggregate/Scope、并发单赢家、claim/NACK CAS 后故障回滚。它们使用 raw SQLite 全行快照、Fake Clock 和确定性 token factory 作为独立 Oracle，不用未来 `get()` 自证。首绿后新增 18 项独立攻击，覆盖时间/溢出、Policy 档位、token、持久腐败、跨 Scope、公共 UoW、线程/进程竞态、busy、`os._exit` 和 exact retry。

环境与第 1 节当前复核环境相同：macOS 26.5 arm64、Python 3.9.6、SQLite 3.51.0；文件型临时 SQLite；未使用模型、网络或外部数据库。执行目录为 `/Users/donbblu/codex/multiAgent/demo`，精确命令：

```bash
python3 -m unittest tests.test_runtime_outbox_claim_lifecycle tests.test_runtime_outbox_claim_lifecycle_adversarial -q
python3 -m unittest tests.test_runtime_outbox_claim_lifecycle tests.test_runtime_outbox_claim_lifecycle_adversarial tests.test_runtime_outbox tests.test_runtime_sqlite_uow tests.test_runtime_thread_event_store -q
python3 -m unittest discover -s tests -p 'test_runtime_*.py' -q
python3 -m unittest discover -s tests -q
PYTHONPYCACHEPREFIX=/private/tmp/multiagent-pycache python3 -m compileall -q coding_workflow tests
cd /Users/donbblu/codex/multiAgent
git diff --check
```

| run | 执行 | 通过 | 失败 | 错误 | 跳过 | 耗时 | exit | 解释 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `01B3B1-EXPECTED-RED-001` | 7 | 0 | 7 | 0 | 0 | 0.002s | 1 | `EXPECTED_CAPABILITY_ABSENT`：6 项首先缺少公开 `SQLiteOutboxLifecycleStore`，read/API 项首先缺少 `OutboxState`；无 import、fixture 或 collection error |
| `01B3B1-3A-CONTROL-001` | 73 | 73 | 0 | 0 | 0 | 1.866s | 0 | 已 KEEP 的 Outbox/UoW/Thread-Event 基线仍绿；红测没有制造 3A regression |
| `01B3B1-FIRST-GREEN-001` | 7 | 7 | 0 | 0 | 0 | 0.118s | 0 | 原冻结红卡未修改并首次转绿；尚未决定 KEEP |
| `01B3B1-DIRECTED-ORACLE-FIX` | 80 | 80 | 0 | 0 | 0 | 未保存 | 0 | 更新旧 fault-enum Oracle 后，7 项新能力与 73 项对照全绿 |
| `01B3B1-ADVERSARIAL-CROSSPROC` | 18 | 18 | 0 | 0 | 0 | 1.619s | 0 | 独立攻击含跨进程和 `os._exit` 矩阵全绿 |
| `01B3B1-NEW-FINAL` | 25 | 25 | 0 | 0 | 0 | 2.273s | 0 | 7 项冻结 + 18 项独立攻击 |
| `01B3B1-DIRECTED-FINAL` | 98 | 98 | 0 | 0 | 0 | 4.161s | 0 | 3B-1 + 3A/UoW/Thread-Event 对照 |
| `01B3B1-RUNTIME-FINAL` | 184 | 184 | 0 | 0 | 0 | 6.428s | 0 | Runtime 专项全绿 |
| `01B3B1-FULL-FINAL` | 397 | 393 | 0 | 0 | 4 | 28.514s | 0 | 4 个既有 VisionForge 真实浏览器 E2E 按门禁跳过 |
| `01B3B1-COMPILE/DIFF-FINAL` | N/A | exit 0 | 0 | 0 | N/A | <1s | 0 | compileall 与 `git diff --check` 通过 |

历史 7 项失败仍只表示“能力当时不存在”，不计产品缺陷。下面四项均发生在实现已经首绿、声称满足契约之后，由独立挑战击穿，故登记为真实 pre-release `PRODUCT_DEFECT`。

#### 故障、并发与恢复矩阵

| 风险 | 注入/调度 | 最终结果 | 回归位置 |
|---|---|---|---|
| claim/NACK 写后故障 | `OUTBOX_AFTER_CLAIM_UPDATE`、`OUTBOX_AFTER_NACK_UPDATE` 抛错 | 重开为完整旧状态，零半写 | `test_claim_and_nack_faults_after_cas_roll_back_exact_rows` |
| 事务 commit 前故障 | 复用 `UOW_BEFORE_COMMIT` | claim/NACK 均完整回滚 | 同上及 `test_process_exit_claim_and_nack_recover_at_transaction_boundaries` |
| 进程硬退出 | claim/NACK 在写后、commit 前、commit 返回后立即 `os._exit` | commit 前精确旧状态；commit 后精确完整新状态；重开 integrity 通过 | `test_process_exit_claim_and_nack_recover_at_transaction_boundaries` |
| 同进程并发 | 两线程同时 claim 同一行 | 一个 owner、一个空 loser；generation 只增一次 | `test_concurrent_claim_has_one_winner_and_one_empty_loser` |
| 跨进程竞态 | spawn + barrier 同时初领/过期重领；旧 owner NACK 与 reclaim 竞态 | 单赢家或可串行解释结果；generation/token/publisher 与 raw DB 一致 | `test_cross_process_claim_reclaim_and_nack_reclaim_are_serializable` |
| Clock/时间边界 | offset、±1µs、回拨、datetime overflow | typed fail-closed，零写入 | offset/clock/overflow adversarial tests |
| 批次原子性 | 第 N 个 token 失败/重复 | 整批回滚，不留下部分 owner | `test_batch_token_failure_and_duplicate_roll_back_every_candidate` |
| 持久腐败 | 同 aggregate 后序行先进入 CLAIMED、PUBLISHED predecessor、跨 Scope 腐败 | claim/NACK/verify 共享校验；本 Scope fail-closed，其他 Scope 不被过度阻塞 | cross-row/published/scope corruption tests |
| SQLite busy | claim 与 NACK 分别持锁 | deadline 内 typed busy；释放后可重试 | `test_claim_and_nack_busy_are_typed_bounded_and_retryable` |

#### 实际发现的问题

| ID | 分类 | 发现时版本与症状 | 根因与修复位置 | 持久回归 | 状态 |
|---|---|---|---|---|---|
| `DEF-01B3B1-001` | PRODUCT_DEFECT | `outbox.py=f4cd79c6df6f000254186858e982dde1f1e90fbd11ddd0d33772c7bebbcfc40c`；Clock 回拨检查在 eligibility 之后，未 eligible 的 lifecycle head 会静默返回空，其他 aggregate 还可能被部分领取 | 在 [`_select_candidates`](../demo/coding_workflow/runtime_persistence/outbox.py#L799) 识别 head 后、判断 eligibility 前检查 lifecycle Clock | `test_clock_rollback_is_typed_and_never_rewrites_current_claim` | FIXED |
| `DEF-01B3B1-002` | PRODUCT_DEFECT | `outbox.py=ed176cdec8d9dec9ab7e446be9b07081902c5160f0ffaf755f54d7d61ea18690`；Store B 可消费 Store A 返回的 ownership 执行 NACK | [`nack`](../demo/coding_workflow/runtime_persistence/outbox.py#L456) 在事务/Clock 前绑定 `ownership.publisher_id` 与 Store publisher | `test_foreign_publisher_store_cannot_consume_nack_ownership` | FIXED |
| `DEF-01B3B1-003` | PRODUCT_DEFECT | `outbox.py=34bcb5415d5915ed779fbabb5a53cf8f18a6d078ddc6869c2101adb13c5fc250`；同 aggregate seq1 PENDING、seq2 已 CLAIMED 时仍可领取 seq1，形成双 owner | [`validate_outbox_aggregate_history`](../demo/coding_workflow/runtime_persistence/_record_codec.py#L207) 拒绝后序非终态提前推进；claim/NACK 复用 | `test_later_claimed_sequence_corrupts_scope_and_integrity_scan` 的 claim/NACK Oracle | FIXED |
| `DEF-01B3B1-004` | PRODUCT_DEFECT | `outbox=185c28f756c4ccdf47f125f64c560f183c686100dd3ac755891aea8daab40e82`、`codec=c16b792a9c3c7948e3a0081f89ab04263b9df5d842cfabfe75de019d55825985`、`state_event=9ce7f9fe8f3d16a164df748954cdeea25056f263ee09ad9bfb960c4458523f17`、`sqlite=7d11ba4d80850fd0dd11d11672c5fee3269185527334896e93f6a7c3bb270d50`；首次失败测试 hash=`505405e6360bdf0000d2bf554eb9f941459a6f0729e13015ed898dacfaa27990`，`verify_integrity()` 对同一跨行腐败返回正常 | [`state_event._verify_connection`](../demo/coding_workflow/runtime_persistence/state_event.py#L289) 收集 Outbox 后调用共享 aggregate validator | 同一测试的 integrity Oracle；最终测试 hash=`0dbff2e2b3173f4d64977b61bd7b7ef8033b14fd028e3cf3f992bf77cceb3323` | FIXED |

实现后未发现 regression。另有一项不计入上述四项：

| ID | 分类 | 问题 | 修正 | 状态 |
|---|---|---|---|---|
| `TDEF-01B3B1-001` | TEST_DESIGN_DEFECT | 旧 `test_fault_point_enum_has_no_after_commit_hook` 精确冻结整个 enum，把新增的两个合法事务内 fault point 误判为产品回归 | `test_runtime_sqlite_uow.py` 加入 `outbox_after_claim_update/outbox_after_nack_update`，仍禁止任何 after-commit hook；hash 从 `7897b88d...` 变为 `d1fa39f9...` | FIXED |

#### 独立 Review、未覆盖风险与决定

| 字段 | 记录 |
|---|---|
| reviewer principals | `/root/outbox_claim_tests`（独立攻击测试）、`/root/claim_report_audit`（证据审计）、`/root/claim_final_review`（最终只读 Review） |
| independence | `/root/claim_final_review` 与 `/root/claim_report_audit` 均为独立只读 Reviewer，未参与实现或测试修改；`/root/outbox_claim_tests` 只负责独立攻击测试，未修改生产代码 |
| final subject | `_record_codec=c16b792a...`、`outbox=85469347...`、`state_event=9ce7f9fe...`、`sqlite=7d11ba4d...`、frozen test=`550561e1...`、adversarial=`0dbff2e2...`；完整值见第 1 节 |
| final review | `APPROVE`（advisory），0 blocking finding；Reviewer 独立复跑 18/25/98/184/397、compileall 与 diff-check |
| Runtime Acceptance | `NOT_ISSUED` |
| decision | `KEEP (3B-1 only)`；完整 `01B-3` 继续 `IN_PROGRESS/INCONCLUSIVE` |

未覆盖风险：本证据只覆盖单宿主文件型 SQLite，不外推到多节点数据库；没有容量、p95 或大表扫描承诺；生产 Composition Root 仍须提供至少 256-bit CSPRNG token factory、每进程唯一 publisher ID 与可信共享 wall clock。Transport、publish、ACK、Receipt、PUBLISHED 深度完整性、ACK-loss 重投、Consumer Inbox、at-least-once/effectively-once 和可靠发布均属于后续 `01B-3B-2` 或更后批次。

3B-1 收口时记录的历史下一动作是冻结并建立 `01B-3B-2` 的 Transport publish/ACK/Receipt 红卡：claim 必须已提交且 writer lock 已释放后才能调用 Transport；有效 ACK 在新事务中追加 immutable Receipt 并以当前 ownership CAS 到 PUBLISHED；stale/错误/exact-retry ACK、ACK-vs-reclaim、有效 ACK 后本地提交失败和 PUBLISHED/Receipt 正反向完整性必须形成可执行 Oracle。该动作现已后置；未来恢复时仍不得修改已发布 Schema v3/checksum，也不得从本切片冒领可靠发布。

---

## 5. 证据缺口与维护规则

### 5.1 已知证据缺口

1. 01B-1/2 没有保存每个真实缺陷第一次失败时的原始 stdout、时间戳与修复前文件哈希。
2. 01B-1/2 没有保存完整独立 `ReviewArtifact`、Reviewer principal、逐项 finding 和 disposition。
3. 01B-3 已在本报告保存 reviewer、subject hash、压力结果、finding 与 disposition，但 Agent 消息的原始 stdout/完整 `ReviewArtifact` 仍未另存为不可变仓库工件。
4. 01B-1 原始验证发生在 dirty workspace，相同内容随后由 `b864b20` 固化；01B-2/3A 已分别由后续 clean checkpoint 固化。当前尚无 clean commit checkpoint 的是 3B-1 候选，因此必须继续以第 1 节的完整文件哈希作为复现主体。
5. 历史环境没有 OS/arch、依赖锁摘要和完整环境变量；本报告只记录当前复核环境，不倒填历史。
6. 当前没有自动 Incident Ledger；本报告是开发 Verification，不是生产 Incident Store。

### 5.2 后续强制维护规则

每个后续切片必须在实施前创建或更新本类报告，并按时间追加，不能覆盖旧运行：

1. EXPECTED_RED 运行前写清测试设计、Oracle、受测 hash 和预期失败签名。
2. 每次运行记录 `run_id`、环境、cwd、env、精确命令、hash、执行/通过/失败/错误/跳过、exit code、耗时和解释。
3. 每个 unexpected failure 创建 `DEF-*`，保存复现命令、失败输出摘要、发现时 hash、根因、修复位置、修复后 hash和回归测试位置。
4. 故障注入、并发、崩溃、腐败与正常对照分别记录；未运行必须写 `NOT_RUN`。
5. 独立 Review 必须保存 principal、independence、subject hash、findings、recommendation 和 artifact 引用；`APPROVE` 不等于 Runtime Acceptance。
6. 只有 required 门禁全部通过且所有 blocking finding 关闭后才能决定 `KEEP`；否则必须 `ROLLBACK` 或 `INCONCLUSIVE`。
7. Plan 只保存契约，HANDOFF/Backlog 只保存状态和本报告链接；不得再次复制并维护平行的 hash/计数真相。

## 6. 变更时间线

| 日期 | 变更 |
|---|---|
| 2026-08-25 | 从 Plan/HANDOFF 重建 01B-1/2 历史 Verification；明确原始日志与 ReviewArtifact 缺失。 |
| 2026-08-25 | 新鲜复跑 01B-2 定向 68 项；记录当时 Outbox 5 项 EXPECTED_RED 与 350 项全量状态。 |
| 2026-08-25 | 将 EXPECTED_RED、真实产品缺陷与测试设计缺陷分开，01B-3 保持 `INCONCLUSIVE`。 |
| 2026-08-25 | 实现 01B-3A Policy/Schema v3/原子 enqueue；首轮 5 项转绿后逐轮加入 22 项独立挑战。 |
| 2026-08-25 | 独立挑战、全量门禁与最终 Review 共发现 10 组真实产品缺陷；逐项修复并绑定回归。 |
| 2026-08-25 | 最终 22/73/159/372 分层门禁全绿，并发初始化 + WAL deadline 连续复跑 5 轮；决定 `KEEP (3A only)`。 |
| 2026-08-25 | 冻结 3B-1 的 7 项 EXPECTED_RED；首跑 0/7，证明能力缺失且 3A 对照 73/73 未回归。 |
| 2026-08-25 | 3B-1 首绿后独立挑战发现并关闭 4 组产品缺陷和 1 项测试设计缺陷；补齐跨进程与 `os._exit` 证据。 |
| 2026-08-25 | 最终 25/98/184/397 分层门禁、compileall/diff-check 与独立 Review 全绿；决定 `KEEP (3B-1 only)`，下一步 3B-2。 |

## 7. Post-commit checkpoints

### 7.1 PROD-01B-3B-1 clean content checkpoint

| 字段 | 值 |
|---|---|
| checkpoint id | `01B3B1-CLEAN-COMMIT-F66E71E` |
| commit | `f66e71e02c206dd361f18f58f669824ae7de6cab` |
| tree | `b86e823c665f68a3a6968b21fad58d60c26c96e0` |
| parent | `99033147fa0583b6573b8bace58e75fbffda859f` |
| subject | `feat: add outbox claim lifecycle` |
| committed_at | `2026-08-25T20:55:05+08:00` |
| meaning | 4.8 最终 subject 的生产实现、公开导出、冻结/攻击测试与收口文档已进入可定位提交 |
| content check | 当前 3B-1 生产实现、公开导出与测试 subject 相对该 commit 无差异，对应第 1 节哈希匹配；`Plan26.md` 因本次 append-only Amendment 有意不再等于 3B-1 收口时的历史文档哈希 |
| directed recheck | `VR-01B3B1-POSTCOMMIT-F66E71E-20260825`：25/25 通过，0 failure/error，3.323 秒 |
| Runtime Acceptance | `NOT_ISSUED` |

定向复核在 `demo/` 执行，使用不含父进程秘密的显式环境：

```bash
/usr/bin/env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin LANG=C.UTF-8 LC_ALL=C.UTF-8 HOME=/private/tmp/multiagent-3b1-checkpoint-home TMPDIR=/private/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 /usr/bin/python3 -m unittest tests.test_runtime_outbox_claim_lifecycle tests.test_runtime_outbox_claim_lifecycle_adversarial -q
```

该 checkpoint 关闭 5.1 第 4 项中“3B-1 尚无 clean commit checkpoint”的当前内容定位缺口，但不修改其历史原因。第 1 节的 `HEAD=9903314... / dirty`、4.8 的原始命令、计数、耗时、哈希和 Review 仍是当时真实受测快照，不得倒填为在 `f66e71e` clean worktree 上重新执行。

4.8 第 502 行和时间线中的“下一步 3B-2”是 3B-1 收口时的历史 next action。后续经用户批准的 [`Plan Amendment PA-2026-08-25-SEC-EXEC-01-FIRST`](../Plan/Plan26.md) 只在调度上插入 `SEC-EXEC-01`，不改变 3B-2 技术契约、3B-1 决定或上述证据。
