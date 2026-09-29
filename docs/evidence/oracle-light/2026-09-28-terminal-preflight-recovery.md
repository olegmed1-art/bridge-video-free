# Native lane preparation: missing terminal policy

Owner run 36485843804 at main edca03ac9d4c63ab4d4da210f15378a5f2216a39 failed before task intake. The Oracle private owner root exists; the execution root does not. Existing native PID415582/invocatione1dd0cc5eb804f68ad3e35731a910112 and legacy PID410394/invocationb9e107c5bae84f4f9982a0dffacf7a4b remain unchanged, NRestarts0.

Fresh Neon owner read-only observations at 2026-09-28T21:32:01.358Z: active tasks0, nonterminal native receipts0, matching work_key native-lane-first-coverage-1994-20260928 count0, native enabledfalse and AUTOPILOT.can_repairtrue. Migration0371 exists; migration0372 and autopilot.migration_0372_function_backup do not. The controller calls verify_terminal_policy before intake.prepare; its SELECT raises undefined-table. Independent I2 reproduced missing migration and source guards. The failed exclusive window is ended early at that observation. Preserve partial root records and never retry this plan or reuse its helper source.

This change checks existing privileges and terminal policy read-only before retaining preparation state, while keeping the second check immediately before intake. A missing backup table produces a fixed refusal code. The SSH runner forwards only a strict allowlist of refusal codes; arbitrary exception text, remote stderr and unknown JSON fields remain suppressed. A nonzero remote result remains a failed workflow.

Recovery prerequisite: apply separately reviewed existing migration0372 only after exact live-source, owner/ACL and idle-state checks, with source backup and drift-fenced rollback. Do not skip terminal policy verification. Any later intake needs a new accepted plan, new current controller package and fresh exclusive window. This evidence does not claim useful task completion or production readiness.

Validation: controller/run-guard tests26 passed plus9 subtests, including actual isolated bootstrap refusal, secret suppression, absent-table handling and no preparation records before a policy refusal.
