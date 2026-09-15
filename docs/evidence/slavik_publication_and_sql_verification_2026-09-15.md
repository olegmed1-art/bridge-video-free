# Slavik publication and isolated SQL verification — 2026-09-15

Status: **DRAFT_REPAIR_VERIFIED; AUTONOMOUS_PUBLICATION_NOT_PROVEN**.

## Binding and scope

- Repository: `olegmed1-art/bridge-video-free`.
- Existing target: draft PR #1059, `autopilot/uv-drive-terminal-evidence`.
- Parent head: `655af81bf8f802a1feb79a4d88f041cfde10f26c`.
- Observed main: `c13a0851` (PR #1523).
- No merge, main push, production/Neon migration, real media, credential transfer,
  server operation, or new automation was performed for this verification.

The parent was published using the native Codex **Update branch** control and
independently read back from GitHub. The newly created personal token was not
used. This demonstrates an operator-triggered publication, not an unattended
completion loop. The target PR remains non-mergeable and must be reconciled with
its upstream branch before promotion; main already contains a terminal-v2 gate.

## Existing dispatch command corrected

The active receiver on main rejects `@codex fix the following task` with
`CODEX_COMMAND_BODY_INVALID`. It accepts `@codex execute this task`, including
when the authenticated envelope's mode is REPAIR. The existing event automation
`6aa39eca7d0c81919deb732796c0335b` was updated to use that accepted prefix for all
modes; mode/scope/can_repair still determine authority. Its repository, Oracle
bot author, exact dispatch-title filter and event-only scheduling were retained.
The saved prompt was read back. No delivery or terminal receipt was fabricated.

Receiver verification: 28 tests passed in the existing callback and callback
workflow test suites. Explicit fixture reproduction demonstrated rejection of
the old repair prefix and acceptance of the replacement with mode=REPAIR.

## SQL defects reproduced before correction

1. The old SQL regression expected QUEUED_CANARY after claim_job had already
   changed the batch to RUNNING. Compare the complete before/after job and batch
   rows and the event count instead of asserting an incorrect earlier state.
2. SQL comparisons using `<>` could evaluate to NULL when a required JSON field
   was absent/null. A valid receipt with a missing top-level result_mode was
   accepted. Reject missing/null scalar identities, MIME/parent fields, hashes,
   envelope markers and fencing tokens explicitly. Check container types before
   enumerating objects or arrays.

## Executed verification

- Python: 41 passed across test_universal_video_database_terminal_gate,
  test_universal_video_terminal_evidence and test_universal_video_neon_queue.
- PostgreSQL 18.3 via PGlite 0.5.8, in-memory, pgcrypto enabled: migrations 0056
  and the draft 0057 applied and SQL regression 041 passed as bridge_ci_owner
  with rolsuper=false. No network database connection was used.
- SQL regression covers 54 missing/null field variants, rehashing dependent
  evidence where appropriate so stale hashes cannot hide missing structural
  validation; it also rejects a null lease token, rejects contradictory MIME,
  checks unchanged queue state after failure, and exercises the valid release
  and final REVIEW paths.
- git diff --check passed.

To reproduce with ordinary PostgreSQL, use a disposable database with pgcrypto,
the bridge_school_reader/app/worker roles and public.schema_migration, then run
0056, draft 0057 and database/tests/041_universal_video_queue.sql with psql
ON_ERROR_STOP=1. Do not point this procedure at production. The existing
database-ci workflow provides the broader ephemeral PostgreSQL test environment.

## Limits and next gate

The in-memory test is not a concurrency/load test or a Neon production proof.
The complete database-ci suite and an independent release review remain needed.
No unattended publication mechanism has been activated. The current GitHub
callback has contents:read, not contents:write, and consumes terminal receipts,
not unpublished source patches. Merely granting write permission is insufficient:
the final design must bind a bounded patch to an authenticated, still-authorized
dispatch, reject stale heads and unsafe paths, publish without executing patch
content, and verify the resulting GitHub head before terminal acknowledgement.
Do not work around missing runtime credentials by exposing a PAT to the model.

Recovery: this is an unmerged draft change; revert its repair commit on the
same branch if necessary. No database rollback is implied or needed because no
deployment/migration was performed.
