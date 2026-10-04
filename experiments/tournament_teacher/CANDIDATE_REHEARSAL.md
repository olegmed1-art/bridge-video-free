# Candidate import and disposable PostgreSQL rehearsal

This package is executable only against a disposable local test database. It is
not a production importer, activation request or proof of a deployed teacher.
No private school/project/branch identities, secrets or production connection
strings are included. The existing test branch is the only push trigger.

Run `python -m experiments.tournament_teacher.candidate_package` to emit the
26-rule candidate package. Original source strings, rule/decision IDs, source
statuses and limitations remain intact. Every database rule is a candidate,
every knowledge version is unreviewed research_candidate, and every package
entry says runtime_eligible=false. Source ACTIVE does not imply database
approval. DRAFT and source-conflict statuses are never upgraded. Points remain
unresolved and questions 13/14 are not completed by inference. The package
preserves executable test semantics; it does not claim completed production
formalization or resolve priorities/forcing/alert ambiguities.

The workflow uses the repository's established official `postgres:18` image,
one disposable service bound to loopback port 55432, no stored credentials,
read-only GitHub permissions and a 10-minute job limit. Bootstrap uses only the
local Unix Docker socket and applies unchanged repository migrations as a
non-superuser owner, including the later video-canon guards. Migration 0200's
composite checksum must equal
`1737eedba6110440220b814c2d104c06e5ced1050f558fd9fcdab256bde70557`,
the reported applied production checksum. Evidence lists the other repository
migration checksums; their application here is not independent proof that every
one is installed in production. No production database copy is used.
The disposable owner receives SET-only membership of the existing worker role
solely for the negative capability test; no worker table/function grants change.
Migration ordering uses the C locale, including numeric suffix migrations.

The rehearsal has no DSN, school, host or activation CLI arguments. It clears
ambient libpq variables and pins both host and hostaddr to loopback, a fixed
disposable database and a non-superuser test owner. Production IDs are never
accepted or emitted. All identities are allocated inside this database.

Execution order:

1. Verify exact snapshot and migration checksum; require empty rule/activation
   tables and empty video source policy in the disposable database.
2. Inject failure after ten candidate inserts and prove transaction rollback
   restores all tracked counts, including import journal entries.
3. Commit the 26 candidates and append-only import events. Repeat the identical
   package with no writes; reject changed package content.
4. Read all rules back from JSONB and compare them to the approved test snapshot.
   Preserve the snapshot's exact text in the immutable import journal, verify
   equality to JSONB, and pass that DB readback through the existing teacher
   HTTP API. Persist the actual test outcomes as rule_test/rule_test_run records.
   Auth success is stubbed only inside the offline HTTP harness; no API DB writer
   or external network call runs. This establishes candidate roundtrip semantics,
   not an active production SQL-backed decision service.
5. Prove the candidates fail activation gates even after successful tests; test
   wrong-school binding, append-only evidence and worker activation permissions.
6. Retire the candidate batch and append retirement events. Retain rules,
   knowledge versions, sources and all test evidence. Canon/runtime activation
   rows for these candidates remain absent throughout.
7. Separately exercise a visibly synthetic public-count control predicate through
   the genuine SQL gates: missing review/tests, inactive source, latest failed
   evidence, an open synthetic conflict, wrong authority lane and wrong scope
   must fail. The invalidated synthetic conflict remains recorded. Activate only that
   control, check the real active view, reject active-rule mutation, then revoke
   runtime/canon activation and retain both rows and all test history. This is
   neither approval nor activation of any tournament rule.

CI retains `tournament-candidate-package.json` and
`tournament-postgres-evidence.json` beside the existing API/decision artifacts.
The latter records counts, migration identity, HTTP case count, gate refusals and
rollback outcomes without database UUIDs. Workflow failure is a failed rehearsal,
not a partially approved import. The service is destroyed with the hosted job.

Production import still requires reviewed semantic scope, established caller,
real private identity mapping, applied-schema reconciliation and a separately
approved bounded operation. BEN_DEFAULT is not a school profile, historical BEN
outputs are not versioned canon bindings, and an empty video source policy does
not authorize inventing one. L1 remains unchanged.
