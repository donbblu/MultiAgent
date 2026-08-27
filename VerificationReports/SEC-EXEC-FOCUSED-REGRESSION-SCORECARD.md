# SEC-EXEC focused regression scorecard

Status: **FOCUSED_PASS**.

| Group | What it covers | Run | Pass | Skip | Fail/Error | Tool wall |
|---|---|---:|---:|---:|---:|---:|
| A | Behavior + structural Oracles | 25 | 25 | 0 | 0 / 0 | 28.373 s |
| B | Approval + Supervisor | 48 | 48 | 0 | 0 / 0 | 0.529 s |
| C | POSIX safety + checked-in runner pure cards | 71 | 71 | 0 | 0 / 0 | 0.520 s |
| D | CLI, VisionForge, and default-off smoke modules | 24 | 19 | 5 | 0 / 0 | 0.228 s |
| **Total** |  | **168** | **163** | **5** | **0 / 0** | **29.650 s** |

The five skips are the deliberately disabled real smoke methods. No opt-in selector was present:

- `tests.test_local_execution_posix_smoke.LocalExecutionPosixSmokeTests.test_watchdog_only`
- `tests.test_local_execution_posix_smoke.LocalExecutionPosixSmokeTests.test_arm_disarm`
- `tests.test_local_execution_posix_target_smoke.LocalExecutionPosixTargetSmokeTests.test_stdout_short_real_fixture`
- `tests.test_project_workspace_production_smoke.ProjectWorkspaceProductionSmokeTests.test_real_python_version`
- `tests.test_local_execution_timeout_cleanup_smoke.LocalExecutionTimeoutCleanupSmokeTests.test_guarded_real_timeout_cleanup`

This regression batch started no new real target, model, browser, or network operation.

Progress in the current five-batch sequence:

1. CLI visible report: 7/7 pure tests.
2. Runtime timeout: one real guarded run passed; pure quarantine fence passed.
3. VisionForge Composition: initial 2/2 red, then 7/7 pure green.
4. Current focused regression: 168 run, 163 pass, 5 explicit skips.
5. Pending: scope-isolated content-hash checkpoint.

This is intentionally not full discovery. It excludes the complete `tests.test_command_validators` module, Browser E2E, Playwright, models, network, additional POSIX targets, `KEEP`, and Runtime Acceptance.
