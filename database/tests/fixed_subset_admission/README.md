# Disposable PostgreSQL admission tests

Status of the local harness repair: **PostgreSQL NOT_RUN**. Compilation,
collection and synthetic control-flow tests are offline evidence only. Earlier
SQL runs tested earlier bytes and cannot validate this candidate.
It never starts a server, downloads dependencies, publishes anything, or contacts
Drive, a video service, IBM, Neon, or a production system.

`run.py` implements the exact 29 case IDs, in order, from
`../plans/fixed_subset_admission_v1.json`. That frozen JSON remains the earlier
design specification; its `future API absent` prerequisites and `NOT_RUN` flags
are historical design status, not dynamically rewritten execution evidence. The
runner binds those scenarios to migration 0060's real function signatures and
uses the real 0056, 0057, 0058 (both included SQL files), and 0059 gates.

Offline commands (no driver import, environment lookup, socket, or DB connection):

```text
python database/tests/fixed_subset_admission/run.py --list-cases
python database/tests/fixed_subset_admission/run.py --collect-only
python -m py_compile database/tests/fixed_subset_admission/run.py
```

The file is deliberately named `run.py`, has no import-time execution, and is not
a pytest test module. Bare pytest never connects to PostgreSQL through this file.
All 29 collected statuses are `NOT_RUN` with `sql_executed=false`. Collection fails
if the matrix IDs or executable mapping drift; there are no pending placeholder
implementations that silently pass.

Future execution requires separate authorization and one fresh disposable PG16
service with psycopg 3 installed. The only accepted CLI endpoint is literal
`127.0.0.1`, port 5432 (or omitted default), database `fixedsubset_test`, login
`postgres`, password `fixedsubset-local-only`. Query options, service names,
alternate credentials, hostnames and database names are rejected before importing
the driver. `PG*`/`DATABASE_URL` environment variables must be absent at execution.
These are synthetic, disposable credentials, never production secrets.

The existing GitHub workflow also runs the two POSIX-only cases from
`test_universal_video_metadata_inventory.py` in the same Ubuntu job, before SQL,
and requires exactly two passed results with no skips. Its only trigger is the
first owner push creating `codex-atomic-subset-postgres-20261013-fix1` with the workflow
file changed. That prior branch/run authorization is exhausted. The local repair
does not change this workflow or its parent-commit gate; it is not a new launch
package. Publication, CI, PostgreSQL, reruns and branch recreation require a
separate decision. The CLI below documents the supported invocation syntax and
does not authorize execution during local preparation.

```text
python database/tests/fixed_subset_admission/run.py --execute --dsn postgresql://postgres:fixedsubset-local-only@127.0.0.1:5432/fixedsubset_test --evidence-dir artifacts/fixed-subset-postgres
```

The client `hostaddr` is pinned to loopback. A Docker service may report its
container address in `inet_server_addr()`; that server address is recorded rather
than incorrectly required to be loopback. The runner checks PG major version 16,
database name, superuser setup identity, and absence of `video_queue` before any
fixture schema reset. Existing project schemas are never adopted as disposable.
Existing conflicting fixture roles cause setup failure. Use a fresh service for
each invocation; capability roles live at cluster scope.

Execution is sequential across cases, with at most eight harness-owned authenticated
LOGIN sessions and two concurrent test statements. The administrative, temporary
cutover and relay-backed connections all use the same tracked connection method.
Before opening a replacement, the existing admin checks that fewer than eight
backends remain in this disposable database. This is an owned-session limit,
not a claim to control independent health checks or external connections.
There is no `SET ROLE`
impersonation for successful runtime operations. Negative `SET ROLE` probes use
actual unprivileged LOGIN credentials. Setup applies repository SQL directly via
psycopg's simple-query path, expands only bounded repository `\ir` includes, and
rejects other psql commands. Between successful cases it drops only disposable
test/video_queue schemas and the five migration-created capability roles, then
reapplies the schema. A failing case stops the run; its SQL logs and remaining
database state are preserved instead of resetting over the failure.

The [coverage map](coverage.md) binds 179 named variants in G01/G02/G04/G05/S08/W02
to concrete guards and invariants. L02 adds independent COMMIT/ROLLBACK schedules;
W01 adds a locked eligible row experiment, for 182 mandatory named variants across
eight existing cases. Missing, duplicate, failed or extra variant evidence fails
its case. Negative probes compare all seven persisted tables. The remaining
21 cases retain their existing assertions and logs, including T01's 87 malformed
terminal variants. L02 can start from a fresh harness without an inherited
`admit2` connection. W01 validates actual `SKIP LOCKED` selection and progress;
function-definition text is retained only as provenance, with no alias assertion.

Concurrency barriers observe actual `pg_blocking_pids` relationships. The admin
installs `helpers.sql` only in the disposable database: scoped triggers can pause
or raise after reservation/enqueue and before binding finalization. They key on a
specific backend PID and row predicate, are inaccessible to runtime roles, and
introduce no production GUC or server-function backdoor. Expiry tests wait for
the server clock after a verified lock wait. Privileged synthetic corruption and
lease-expiry fixtures disable table triggers only inside explicit test setup
transactions; positive API acceptance always uses enabled production guards.

F04 uses a local transport relay to drop the real server's
`CommandComplete(COMMIT)` frame before delivery. It requires a client transport
error, then reconnects and checks the immutable binding and unchanged observation
consumption. It does not manufacture a commit result or simulate queue state.
The relay is constructed only inside this executing scenario and forwards to the
same fixed loopback service.

The worker cases cover old credentials, real selector eligibility and progress
past older held/historical rows; current tokens, three attempts, stale tokens,
null tokens, counter reset, raw retry max=4, expired third leases and exhausted
queued jobs. Source evidence binds native revision even at unchanged size with a
null checksum, before the first attempt and between attempts. Approval revocation
is tested in both lock orders for admission and attempts. S10 also lets approval
expire while the worker waits for a still-fresh observation row.

Freshness means a server clock check after all lock waits at the final locked
authorization point. It is not a promise of absolute freshness at every COMMIT.
S09 explicitly forces deferred constraints immediate, waits for observation
expiry, and commits; structural validation still holds and a fresh attempt
observation is required. Runtime processing remains held by default until the
trusted approver enables it.

The terminal case consumes distinct pre-work and fresh post-work observations,
evaluates all 84 malformed SQL0058 payload variants and all three SQL0059 size
variants, and checks exact rejection messages/constraints. Semantic variants
recompute manifest and receipt hashes using the actual PostgreSQL canonicalizer.
Each rejected finish preserves the complete persisted snapshot, including
observation consumption. Successful one-source and two-source canaries retain
`CANARY_REVIEW`, `released_jobs=0`, immutable binding, and permanent reservations.
G03 also executes the actual rollback file against empty state and proves
nonempty rollback refusal; empty deactivation retains guards/history and never
restores legacy grants.

Evidence contains `results.json` with all 29 IDs, pass/fail/not-run status, server
version and clock, source/migration hashes, and per-case SQL logs and successful
state snapshots. Credentials and parameter values are omitted from SQL logs;
synthetic LOGIN provisioning SQL is redacted. A setup failure retains its log and
reports whether any SQL was dispatched. Any failure or unrun case exits nonzero;
only 29 actual passing cases plus successful cleanup can produce overall `PASS`.
On failure, the original error is recorded before a best-effort five-second state,
activity and lock capture. Secondary capture, file-write and cleanup errors cannot
replace it. Cleanup has a shared 45-second deadline: cancel pending statements,
stop the relay, close every tracked client, and reuse the admin slot to verify no
other database backends, owned advisory locks or prepared transactions remain.
All statement and diagnostic threads are tracked; incomplete cleanup fails the
run even if all cases passed. It never resets the failing database state.
Until a separately
authorized execution produces that evidence, real PostgreSQL validation remains
**NOT_RUN**.
