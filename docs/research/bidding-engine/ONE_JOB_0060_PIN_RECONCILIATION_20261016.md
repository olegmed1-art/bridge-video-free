# 0060 rehearsal pin versus PR #2168

Read-only source comparison on 2026-10-16, before any integration of PR #2168:

| Source | Exact source ref and path | SHA-256 |
| --- | --- | --- |
| Current `main` | `f8cddcf1c3c90765fdda10b7ee816e98a9179436:database/migrations/0060_fixed_subset_admission.sql` | `27932f2b5f22f383d9f84341c0ae6e88dc346c7940ec92490edc0a3d37358dfe` |
| PR #2168 | `223a066c3f078080d320f8c4c596b83d908e4cc7:database/candidates/0060_fixed_subset_admission.sql` | `cfd5bf6088761dcc1d38a4d0febb6fb24f3d4f396fd67ca08568ff44994c44bc` |

The candidate differs from current main by a comment and a two-line SQL
statement after the `video_subset_owner` grant. It revokes default `PUBLIC`
execution of future functions owned by `video_subset_owner`:

```sql
ALTER DEFAULT PRIVILEGES FOR ROLE video_subset_owner
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
```

## Contract review for 0061/0062

This is an ACL change, not just a path or formatting change. PostgreSQL 18
applies default privileges only when an object is created, using the current
role's defaults; changing ownership later does not reapply them. The 0060 SQL
creates its functions as the installer and transfers ownership later. Neither
0060 nor the 0061/0062 proposals switch to `video_subset_owner` while creating
functions. Both proposals explicitly revoke `PUBLIC` access to their new
functions (`0061` lines 684-691; `0062` lines 294-299). Thus the added 0060
statement does not conflict with their function ACL contract in the reviewed
SQL. It hardens functions created **in the future as** `video_subset_owner`.
This is a static compatibility conclusion, not a PG18 execution result for the
PR #2168 bytes. PostgreSQL reference:
<https://www.postgresql.org/docs/18/sql-alterdefaultprivileges.html>.

The isolated PG18 rehearsal in PR #2175 pins and successfully ran the current
main bytes (`27932f…`) at `c87c721e970a8213de1d1b6ece071ee4991d4985`.
It has **not** tested the PR #2168 candidate bytes (`cfd5bf…`). The pin is
intentionally unchanged. When #2168 is integrated or its candidate is chosen
for this flow, compare the exact resulting SQL and role/ACL behavior, update
the approved rehearsal inventory through review, and run the full synthetic
PG18 scenario again on the new exact head. A mismatch must fail closed; this
record does not authorize production installation or privilege changes.

## Minimal integration when the candidate is selected

Keep the existing `candidate_0060.sha256` pin at
`27932f2b5f22f383d9f84341c0ae6e88dc346c7940ec92490edc0a3d37358dfe`
while the approved
rehearsal still reads the current `database/migrations/0060_fixed_subset_admission.sql`.
The inventory loader already accepts exactly one 0060 file in `migrations/` or
`candidates/`; no loader change is needed for the move. On a reviewed combined
head that selects the PR #2168 candidate, verify the exact 0060 bytes again,
then change only `candidate_0060.sha256` in
`database/fixtures/one_job_pg18_approved_inventory.json` to
`cfd5bf6088761dcc1d38a4d0febb6fb24f3d4f396fd67ca08568ff44994c44bc`, subject
to that verification. Do not accept a third hash implicitly.

The targeted PG18 regression on that **exact combined head** should establish
that (1) a function newly created while `SET ROLE video_subset_owner` is active
has no `PUBLIC EXECUTE`, (2) the 0060 and 0061/0062 functions retain their
intended effective ACLs, and (3) the existing 0061/0062 synthetic flow still
passes. Inspect `pg_default_acl`, `pg_proc` and `has_function_privilege` with
representative unauthorized and intended caller roles. Run it only when the
current external preflight block is resolved. No new local test is added now:
the current pinned 0060 intentionally has no default ACL change, so such an
assertion would test a source that this branch does not select.
