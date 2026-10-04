# Independent review: disposable candidate-import rehearsal

Reviewed on 2026-10-04 by a separate agent under the repository's ASSURED
review requirement. This is an I1 code review with local, non-connecting probes.
The reviewer did not run Docker, connect to PostgreSQL, trigger a workflow,
contact production, or change the implementation. This document is the only
file added by the reviewer for this stage.

## Conclusion

No further blocking issue was identified after the fixes below. The reviewed
workflow confines execution to one official PostgreSQL 18 service on loopback
port 55432, with a ten-minute job timeout and read-only repository permissions.
It does not request production credentials or change existing migrations.
Publication to the authorized test branch may proceed to isolated CI; this
review does not substitute for the actual exact-commit CI result.

The 26 imported tournament rules remain `candidate`, `research_candidate`,
and `unreviewed`. Their formalization is explicitly pending; no tournament
activation is created. The positive activation/revocation control uses a
separate synthetic school and rule, with no bridge-canon meaning. That control
must not be reported as activation of the tournament rules.

## Findings resolved before publication

1. **JSONB ordering broke snapshot identity.** The initial implementation
   serialized JSONB objects back to indented JSON. PostgreSQL does not retain
   object-key order, so equal rule objects could fail the API's exact text hash.
   A local reordered-object probe reproduced this mismatch. The import now
   retains the original serialization as text in the immutable ingestion-run
   metadata. Readback compares all 26 JSONB source-rule objects with that text
   and the package; the existing HTTP adapter still checks the pinned hash.
   The reviewer confirmed the retained serialization produces the pinned hash.

2. **Ambient libpq settings could override the connection destination.** An
   explicit `host` alone did not exclude `PGHOSTADDR` or service configuration.
   The rehearsal now removes ambient `PG*` variables without printing values
   and explicitly supplies both host and hostaddr as `127.0.0.1`. A mocked
   connection probe with synthetic PGHOSTADDR, PGSERVICE and PGPASSWORD values
   verified removal and the fixed port before any real connection could occur.

3. **Ambient Docker configuration could select a remote daemon.** Every
   bootstrap Docker invocation now explicitly selects
   `unix:///var/run/docker.sock`. A fully mocked bootstrap with a synthetic
   remote DOCKER_HOST recorded five Docker commands, all with the explicit
   local socket. No subprocess was executed during this probe.

## Checks performed and code boundaries

- Built the candidate package locally: 26 records, null production bindings,
  no activation request. The composite 0200 migration checksum matched
  `1737eedba6110440220b814c2d104c06e5ced1050f558fd9fcdab256bde70557`.
- Compiled the four new Python modules without executing database operations.
- Reviewed transaction rollback after injected partial import, exact-package
  comparison, duplicate-import comparison, ingestion history, and retirement
  rather than deletion. Counts and retained historical rows are asserted by
  the rehearsal, but were not independently observed in a running database.
- Reviewed candidate activation denial, school-scope mismatch, append-only
  evidence protection, worker activation privilege denial, and empty active
  catalog checks against the unchanged SQL gates.
- Reviewed the distinct synthetic control for missing review/test evidence,
  inactive source, latest failing test result, authority lane, canon scope,
  active-rule immutability, activation, and subsequent revocation.
- Verified that HTTP evaluation consumes database-readback snapshot text through
  the existing test-only adapter. This remains a rehearsal adapter, not the
  production SQL runtime catalog or a production candidate importer.

## Remaining assurance limits

The real migration bootstrap, database constraints, transaction behavior,
privileges, HTTP readback, and activation/revocation control still require
successful execution in the authorized disposable CI service. Static inspection
and connection mocks cannot establish those results. The control exercises
selected gates; it is not an exhaustive proof of all SQL gates, concurrency
behavior, conflicts, or recovery scenarios.

No live canonical-source re-verification or deployed teacher binding was
performed by this reviewer. Passing this rehearsal would demonstrate isolated
candidate import and gate behavior only; it would not resolve the point-counting
method, authorize canonical activation, establish production UUID mappings,
or justify deployment, production replay, or changes to production permissions.
