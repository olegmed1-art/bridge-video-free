# Direct video staging handoff — local implementation record

Date: 2026-10-14. Status: local code only; not a production activation.

The completed video worker now passes the actual return value from
`persist_video_result` through `persist_completed_drive_job` to the v4.2 quality
postprocess in the same job. The quality writer receives that object separately
from generated artifacts. It checks the exact database lineage and writes
candidate rows, a replay receipt, and its analysis-run output link in its
existing idempotent transaction. No event consumer is introduced. The
`BridgeVideoResultRecorded` publication decision remains blocked.

The direct handoff requires database persistence, an output folder, and a lesson
number. A missing full video replay cannot be acknowledged as staged. Ordinary
quality observations with no requested replay return `BLOCKED_NO_REPLAY` and
make no candidate database writes. Invalid or empty quality fails closed.
The durable replay receipt is the staging checkpoint. A retry after a failure
before or after that commit repeats the idempotent writer and must obtain the
same receipt before acknowledging staging. A later artifact upload failure
also fails the job and is retried through the same path.

Verification completed locally: 8 offline handoff tests, Python syntax for
changed modules, source-hash preflight for all 13 pinned files, and `git diff
--check`. The existing disposable PostgreSQL suite now includes a direct
handoff retry and wrong-lineage test; it requires PostgreSQL and `psycopg`,
which are unavailable on this Windows host. The v4.2 regression suite also
requires `fitz`, unavailable here. Neither suite was claimed as passing.

Promotion requires the repository's independent assurance and disposable
PostgreSQL CI pass, followed by a separate production decision. Rollback is to
revert this commit; candidate staging is idempotent and this change adds no
schema migration or publication authorization. No production secret, database,
worker, student delivery, or school Canon state was changed in this local pass.
