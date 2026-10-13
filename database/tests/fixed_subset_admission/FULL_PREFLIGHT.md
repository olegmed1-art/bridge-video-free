# Disposable PG18 full preflight candidate

This adds the remaining 042/043 behavior fixtures after 0060 and runs every SQL
file in the same `database/tests/*.sql` inventory/order as the production
preflight (116 files at preparation). No production database is contacted.
`managed_owner_install.py --execute --full-preflight` is restricted to the fixed
synthetic localhost database. The default command remains offline.

Four actual LOGIN sessions receive one capability each: approver, collector,
admitter and worker. Each is NOSUPERUSER/NOCREATEROLE/NOCREATEDB/NOBYPASSRLS,
cannot SET the capability role, cannot become the definer, and cannot directly
read or mutate queue tables. The migration owner creates/drops the synthetic
roles, reads original queue rows and advances retry clocks as in the old 042.
No runtime subject uses SET ROLE or the migration owner's connection.
Five fixture connections plus the parent psql connection fit the eight-session
service limit. The runner closes clients, revokes approvals and preserves the
immutable synthetic history until unconditional service/storage destruction.

Both legacy SQL branches remain available before 0060. The shared 043 ACL check
requires the new admission/finish APIs after 0060, rejects the old enqueue/finish
grants and direct worker DML, and continues to reject application claim access.
Three rollback-isolated negative ACL injections exercise the changed check.
The existing 041 exact capability matrix and its two negative ACL cases remain.

042 covers canonical JSON, empty idle snapshot, repeated exact admission,
canary-first claim, 22 invalid terminal payloads, no automatic canary release,
old/wrong fencing tokens, retry/reclaim/exhaustion and incomplete evidence.
043 covers three-file admission, conflicting request digest, canary selection,
wrong finish fence and six PDF/AI size cases. New API rejection messages use
`SUBSET_LEASE_LOST` / `SUBSET_DIGEST_MISMATCH_HOLD`; payload/constraint errors
retain their original VIDEO_QUEUE diagnostics. Accepted size boundaries roll
back deliberately, rejected sizes require SQLSTATE 23514 and the exact constraint.
Every size attempt compares all job/batch/event rows after rollback. The shared
0059 rollback/reapply tail is unchanged and still checks history preservation.

The SQL entrypoints propagate Python failure through psql's
[`SHELL_ERROR`](https://www.postgresql.org/docs/16/app-psql.html#APP-PSQL-VARIABLES)
and ON_ERROR_STOP. Ordinary test failures are recorded and remaining files still
run in separate sessions; they cannot produce overall PASS. Timeouts stop the
process group and require service destruction. A complete PASS requires 275
offline tests, all 116 SQL files, both runtime fixture reports (42/20 checks),
all installer/ACL negatives, checksum reconciliation and verified cleanup.

The new branch permits one initial push/one job attempt, standard public Ubuntu,
20 minutes maximum, the same pinned PG18 image and bounded log evidence.
No production secrets, paid artifact storage, deployment or merge is involved.
This candidate does not provision real runtime subjects; production M/R/ACL and
target equivalence remain UNKNOWN. The production workflow has not been adapted
to provision these synthetic subjects and must not be dispatched for this test.

Existing backup evidence is retained as an independent prerequisite, without a
new backup run: [issue #526 evidence](https://github.com/olegmed1-art/bridge-video-free/issues/526#issuecomment-6142467976),
[successful run 38375799572](https://github.com/olegmed1-art/bridge-video-free/actions/runs/38375799572).
It records 329/329 tables and 9/9 schemas restored in isolated PG18, encrypted
backup checksum `e0d1d5c631fab069a4186a134581f0ca267a2f44a77c81c1f333997d4d37c40e`.
It does not prove complete row/role restoration or production target equivalence.

This document supersedes only the full-suite NOT_RUN fixture limitation in
MANAGED_OWNER.md when an exact candidate CI produces terminal evidence; it does
not promote the candidate or authorize production execution.
