# SEC-EXEC batch 2: timeout cleanup

Status: **PASS_LIMITED**. This is a development checkpoint, not `KEEP` or Runtime Acceptance.

The one real opt-in run used the existing `ExternalProcessGuard` and `hang_ignore_term` fixture. Runtime called its `_spawn` boundary once; that target created one same-process-group grandchild, so the observed target topology was two PIDs. Both ignored TERM. The run timed out, escalated to KILL, reaped the direct child, verified the process group absent, closed both streams and the private environment, then asked the independent watchdog to close. The watchdog reported `clean=true` with terminal reason `cleanup_control`, and the batch root was removed.

| Evidence | Result |
|---|---:|
| Real unittest | 1 passed, 0 failed/errors/skips |
| Unittest duration | 3.957 s |
| Tool wall time | 4.192818042 s |
| Runtime `_spawn` calls | 1 |
| Target PIDs | 2 (leader + grandchild) |
| TERM / KILL | attempted / attempted |
| Direct child / PGID | reaped / absent |
| Streams / private environment | closed / closed |
| Watchdog | clean, joined, `cleanup_control` |
| Retained batch roots | 0 |

Quarantine was tested separately and only with pure mocks. A scripted timeout plus an ownership-probe failure produced `CLEANUP_FAILED`, a nonempty quarantine ID and positive generation. A fresh confirmation for the same workspace was rejected before the process boundary: first spawn count `1`, second spawn count `0`.

The default-off module ran as one pass plus one skip and constructed no real target. A post-run `ps` diagnostic was blocked by the local sandbox, so it is not claimed as evidence. The Guard’s PID/PGID checks and the empty batch-root search are the retained absence evidence.
