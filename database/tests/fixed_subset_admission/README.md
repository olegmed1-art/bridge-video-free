# Disposable PostgreSQL admission tests

Status at implementation: **NOT_RUN**. Only Python compilation and offline
collection have been performed. This runner is not PostgreSQL acceptance evidence.
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

The unpublished GitHub workflow also runs the two POSIX-only cases from
`test_universal_video_metadata_inventory.py` in the same Ubuntu job, before SQL,
and requires exactly two passed results with no skips. Its only trigger is the
first owner push creating `codex-atomic-subset-postgres-20261013-fix1` with the workflow
file changed. No PR is included in that proposed approval: opening a PR triggers
additional existing CI. Publication and the first test run are one combined
external action, still unauthorized here; reruns and branch recreation are not
implicit retries. The CLI below is an alternative explicitly approved invocation,
not an instruction to execute it during local preparation.

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

Execution is sequential across cases, with at most eight genuine authenticated
LOGIN sessions and two concurrent test statements. There is no `SET ROLE`
impersonation for successful runtime operations. Negative `SET ROLE` probes use
actual unprivileged LOGIN credentials. Setup applies repository SQL directly via
psycopg's simple-query path, expands only bounded repository `\ir` includes, and
rejects other psql commands. Between successful cases it drops only disposable
test/video_queue schemas and the five migration-created capability roles, then
reapplies the schema. A failing case stops the run; its SQL logs and remaining
database state are preserved instead of resetting over the failure.

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
only 29 actual passing cases can produce overall `PASS`. Until a separately
authorized execution produces that evidence, real PostgreSQL validation remains
**NOT_RUN**.
