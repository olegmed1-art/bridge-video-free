# 0060 rehearsal pin versus PR #2168

Read-only source comparison on 2026-10-16, before any integration of PR #2168:

| Source | Exact source ref and path | SHA-256 |
| --- | --- | --- |
| Current `main` | `f8cddcf1c3c90765fdda10b7ee816e98a9179436:database/migrations/0060_fixed_subset_admission.sql` | `27932f2b5f22f383d9f84341c0ae6e88dc346c7940ec92490edc0a3d37358dfe` |
| PR #2168 | `223a066c3f078080d320f8c4c596b83d908e4cc7:database/candidates/0060_fixed_subset_admission.sql` | `cfd5bf6088761dcc1d38a4d0febb6fb24f3d4f396fd67ca08568ff44994c44bc` |

The candidate differs from current main by three added SQL lines after the
`video_subset_owner` grant. They revoke default `PUBLIC` execution of future
functions owned by `video_subset_owner`:

```sql
ALTER DEFAULT PRIVILEGES FOR ROLE video_subset_owner
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
```

The isolated PG18 rehearsal in PR #2175 pins and successfully ran the current
main bytes (`27932f…`) at `c87c721e970a8213de1d1b6ece071ee4991d4985`.
It has **not** tested the PR #2168 candidate bytes (`cfd5bf…`). The pin is
intentionally unchanged. When #2168 is integrated or its candidate is chosen
for this flow, compare the exact resulting SQL and role/ACL behavior, update
the approved rehearsal inventory through review, and run the full synthetic
PG18 scenario again on the new exact head. A mismatch must fail closed; this
record does not authorize production installation or privilege changes.
