# Offline diagnostic delta after the single authorized preflight

No additional production connection, workflow dispatch/rerun or backup is
authorized or performed by this change. Published review code is not live proof.

## Evidence retained from run 36871379403

- SHA `3b536b6f3996cb623d33b22a4d517432b1475675`, attempt 1, exact reviewed branch.
- Job `110399733964`: 2026-10-01 13:48:06–13:48:21 UTC, 15 seconds, failure.
- Observer receipt: elapsed 10,131 ms, phase `auth_identity_acl_rls_readonly`, FAIL.
- All database gates remain NOT_PROVEN; dump/upload/restore/production_writes false.
- Ubuntu 24.04 runner image `20260927.320.1`; checkout and Python observer ran.
- Parent independently reconciled Neon mapping at 13:45:59 UTC. That does not prove
  the SQL server identity, authentication, TLS or permissions of this invocation.

The receipt's phase proves context/checkout/current-main and URI-policy checks
passed. A Python interpreter/import failure before observer, initial URI rejection,
the 45-second client wall timeout, 100-second outer watchdog and 120-second job
timeout cannot explain this specific normal 10.131-second receipt. A 10-second
connect or SQL timeout remains possible, but total elapsed time also includes
Docker image acquisition and execution. It is not a measured connection duration.

Authentication, TLS, networking, Docker/image/CA-mount, SQL or catalog admission
failure remain unseparated possibilities. The original client stderr was captured
and discarded, never persisted. It cannot be reconstructed from the old receipt.
Diagnostic collapse is confirmed; local integration also reproduced a concrete
SQL defect described below. Neither establishes a credential/ACL defect. Do not
relabel that historical receipt using the new classifier.

## Reproduced SQL defect and correction

On isolated PostgreSQL 18.6 with an ordinary table and a sequence, the original
`c.relkind='S' AND NOT has_sequence_privilege(c.oid,'SELECT')` predicate fails with
SQLSTATE 42809 (ordinary table is not a sequence). PostgreSQL may reorder AND
terms; the type filter does not guarantee it runs before the sequence-only
function. This is a runtime error, which a syntax parser and mocked JSON tests
cannot detect. It is a concrete plausible explanation for the previous live
failure, but cannot be conclusively assigned as its cause without retained stderr.

The fix wraps relation-specific functions in CASE WHEN relkind matches THEN
function ELSE false END, covering sequence, table and RLS checks. Integration
includes the legacy failing predicate followed by the corrected full catalog
query, so a future optimizer reorder must not reintroduce the wrong-object error.
No object is skipped from the intended privilege/RLS scope.

## Diagnostic correction

The client inspects captured diagnostics only in memory and raises a typed error
whose code must belong to a fixed allowlist. No raw exception, stderr, stdout,
SQLSTATE, connection string, password, object name or server-returned string is
included in a failure receipt. Unknown or malformed errors safely fall back to
TOOL_FAILED/OUTPUT_INVALID. `LC_ALL=C` stabilizes client wording; server messages
may still be localized, in which case generic SQL_FAILED is preferable to guessing.

Codes distinguish authentication, TLS, network, connect/statement/lock/client
timeouts, SQL, ACL, RLS, identity, read-only, malformed output and tool failure.
Docker exit 125/126/127 is tool failure. A failed or timed-out forced container
cleanup receives CLIENT_TIMEOUT_CLEANUP_FAILED rather than implying cleanup worked.

psql verbose SQLSTATE is captured privately. Structured SQLSTATE takes precedence
over incidental text in identifiers/query excerpts. Query-canceled (57014) and
lock-not-available (55P03) are not automatically called timeouts: only explicit
timeout primary-message wording permits that classification. Classifier codes
describe observed client diagnostics; they do not prove an underlying root cause.

The catalog query now counts ACL denials and active RLS separately and protects
type-specific functions with CASE. Its scope and production destination are
unchanged. Receipt v2 records
each gate only after its validation succeeds, with fixed failure_code; unevaluated
gates remain NOT_PROVEN. A wrong identity still refuses admission. No retry,
fallback credential or permission change has been introduced.

## Validation and local integration boundary

The offline regression suite covers realistic psql/libpq/Docker errors, conflicting
error text, user cancellation versus timeout, all catalog gates, malformed JSON,
missing executable, client timeout and cleanup failure. Sanitization is exercised
through the actual observer and CLI entrypoint with synthetic sensitive markers.
The backup workflow's secret-free contract job includes the diagnostic regressions.

`tests/run_neon_backup_local_pg18.py` provides explicit synthetic integration using
portable PostgreSQL 18. Its only connection host is literal 127.0.0.1, it clears
ambient PG settings and production credential variables, creates a fresh test
cluster, and stops it in finally. It uses no production credential or data, no
dump and no artifact upload. Local test roles/grants exist solely in that cluster.
It checks the exact catalog SQL, schema/table/sequence/large-object ACL denials,
RLS/FORCE RLS/BYPASSRLS, and real SQL/TLS/statement-timeout/read-only diagnostics.
Vanilla PostgreSQL has no trusted Neon tags and intentionally cannot establish
Neon identity. Docker, Neon TLS chain/routing and live secret validity remain
outside this integration test.

The first Windows fixture exposed inherited background-process pipe handles in
the test runner's pg_ctl capture. The synthetic cluster was explicitly stopped;
the test runner now redirects pg_ctl to a regular local log. This is a test-harness
issue, unrelated to the Ubuntu production preflight failure.

Observed final validation: 17 offline tests and the backup contract PASS;
PostgreSQL 18.6 integration returned
`LOCAL_PG18_CATALOG_ACL_RLS_SQL_TIMEOUT_TLS_READONLY_PASS` and its cluster stop
succeeded. The test reproduced the old wrong-object error before testing the fix.
Independent different-model I2 review passed the classifier and CASE correction.
Portable binaries came from EDB's official PostgreSQL binaries page; no Windows
service was installed and no production connection was attempted.

## Execution remains closed

The one approved live attempt is consumed. A future preflight needs new explicit
approval at a freshly reconciled SHA/mapping. Do not run it to obtain a better
error message under the old authorization. Backup viability and restore success
remain unproven. The temporary dispatch harness must not be merged into main.

GitHub's environment deployment label records environment-job tracking; no
application/infrastructure deployment steps exist here. No environment settings
or secrets were changed to alter that label.
