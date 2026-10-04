# Dormant fixed owner adapter and bounded controller

Stacked on PR2108 exact baseline 5c5dd778a2c6b40b899981378b911f89845d76ff.
Qualified main remains f513afc4a1a4cae325b7baeeef5f4848dd29a7a5.
No active owner workflow, application route, L1, schema, credentials or deployment
setting changes. No validation build, activation or production SQL was requested.

The executable FixedAdapter dispatches ONLY baseline, initial, revoke, reactivate
and independent emergency SQL compiled by the original pilot compiler. The new
structured 42-record inventory does not change rendered SQL, deterministic IDs,
original code SHA C, payload hashes, or row budgets. Golden tests compare all
original outputs with the immutable baseline compiler.

Read-only inspection uses READ COMMITTED with the same transaction advisory lock,
so waiting behind a committed recovery cannot return a pre-lock snapshot.
Under the SAME advisory transaction lock, every declared deterministic ID and
its typed content, foreign keys, literal source excerpts, original approval C,
stage history, activation status and original 24-hour expiry are checked.
Natural-key collisions and extra related versions/source links/tests/test runs/
events/activations fail closed. Normal flow has 2/34/35/40 rows; emergency adds at
most two, including after baseline; repeat recovery creates no further rows.
Partial or foreign states cannot be overwritten by recovery; their revoke is
explicitly UNPROVEN. This protects foreign data but cannot promise recovery after
an external owner corrupts pilot ownership.

Normal stages reject repeats and wrong order without writes. They recheck finite
window and phase permit AFTER acquiring the lock. Independent recovery ignores
normal admission/stage cutoff, controller process budget and application alias;
it retains current reviewed owner source/transport/identity and ownership guards.
Absent no-op is allowed only when ALL42 IDs and natural/related keys are absent.
Recovery verifies committed persisted state in a separate read-only transaction.

owner_stage is a concrete dormant CLI:
`python -m tools.canon_auth.owner_stage --stage inspect --contract public-contract.json`
or one fixed normal stage with `--permit public-permit.json`; emergency takes no
normal phase permit. It accepts no SQL, school, target, role or DSN argument.
Public named context, manual first-attempt main/owner binding, exact program hash,
externally reviewed contract/permit digests and current source checks run before
the existing resident credential is read. Strict transport and resident inventory
are reused unchanged, then school is resolved uniquely by the existing
EXPECTED_SCHOOL. Recovery has its own invocation and dedicated connection.

BoundedController.run is executable, bounded to one attempt, <=40 HTTP requests,
420 seconds and the explicit launch window, with a POSIX main-thread alarm that
interrupts blocking normal calls and a separate 60-second recovery alarm. It requires separate normal/recovery
channels, a durable single-build claim supplied by the authorized dispatcher,
fresh independently retained read-only recovery readiness, real application LOGIN
behavior, health, school overview and initial position404. Every poll, positive
3H and negative3S receipt needs deployment/request correlation before the next
stage. Stage receipts, final correlation and a fresh independent read-only database
readback (40 rows, active4, original expiry, zero owned output/search/final rows)
are checked. Any failure after a
possibly committed dispatch calls the independent recovery channel. Lost receipt
or connection is uncertain; no rollback assertion and no automatic replay.

Launch.parse is the exact no-default contract: original C, current owner R,
original target deployment D/S, ONE distinct validation build A, controller run N,
compiled plan P, reviewed program hash, UTC open/admission/stage cutoffs.
Admission is <=5 minutes and normal stages <=15 minutes. No implicit rollover or
expired legacy validator changes. The previously proposed midnight window is not
admission; missing prerequisites at the separate readiness cutoff cancels it.

The unchanged source-scope trigger requires public table-name resolution during
writes. The wrapper checks public CREATE ACL against only schema/current owner,
rejects any existing temp schema, and pins pg_catalog,public,pg_temp. Read-only
inventory retains pg_catalog only. No trigger/schema/privilege is altered by the
operational adapter.

## Evidence and limits

The isolated ten-minute Actions job uses a localhost PostgreSQL18 service, no
secrets and unchanged migrations. It runs previous teacher/API/L1 regressions,
the original staged SQL rehearsal, real committed fixed adapter lifecycle,
concurrent stage serialization, all42 foreign-content collisions across twelve
table classes, natural-key/partial/extra-test-run rejection, source-after-commit
uncertainty, expired normal flow with independent recovery, and controller fault
paths. Fixture-only superuser TRUNCATE/trigger fault injection is explicitly
confined to fixed loopback tournament_rehearsal; it is absent from owner_stage.

Teacher calls in controller tests use the real existing authenticated HTTP route,
app-role SQL catalog and decision mechanism. A separate behavior check uses the
actual application LOGIN. Health/overview and provider receipts/control-plane
observations are labeled synthetic fixtures; this is NOT Vercel or live proof.
3H is SUPPORTED and 3S CONTRADICTED for the fixed 3-1-4-5 hand, with literal source
bindings TDEC-20261003-002 and no point threshold inference. Revoked/ineligible
binding gives ABSTAIN. No teacher output, search run or final decision is created.

Live wiring remains a separate review: the active owner workflow belongs to the
IBM task and currently exposes inventory only. No normal stage dispatcher,
independently reachable installed recovery command, durable authoritative build
claim or authenticated Vercel/GitHub observer is installed by this PR.
Caller-created JSON/hash/fixture readiness does not authenticate these channels.
The module hash pins adapter files; exact current-main review must also cover its
unchanged transitive resident/parser/package/teacher/schema dependencies. Future
owner wiring must supply existing app import dependencies; no imports install them.
Do not install or execute until that concrete wiring and exact source review are
complete. Successful unit/PG tests do not confer production readiness.


## Parent-review correction: remote cutoff and process death

Each normal owner invocation now has its OWN POSIX <=60-second supervisor bounded
by stage_until. PostgreSQL17+ transaction_timeout bounds the entire transaction;
unsupported server versions fail before DML. Client and server clocks are checked
before each SQL and immediately before commit, including after post-inventory and
source recheck. A late commit acknowledgement cannot produce STAGE_COMMITTED;
it is uncertain and requires reconciliation/independent revoke. SQL COMMIT and
receipt transport cannot be made atomically simultaneous with an external clock.

Real delayed PostgreSQL tests sleep inside writes and after post-inventory beyond
cutoff; the transaction is terminated and independent readback proves zero committed
pilot rows. Separate tests advance the clock before commit (rollback) and after
real commit (uncertain; independent readback/revoke).

watchdog.watch and owner_stage --stage watchdog are a separate-process channel.
It must be armed BEFORE baseline against absent owned IDs, then UNCONDITIONALLY
revokes at the bounded acceptance stage cutoff; there is no cancel/disarm command.
This ends the acceptance experiment even if its controller succeeds; it is NOT a
24-hour active-pilot host. Qualification launches TWO actual OS processes, commits
initial stages in one, kills that controller, and proves the surviving watchdog
uses its own PostgreSQL connection to commit/read back owned revoke. That fixture
proof is not live GitHub survivability or reachability.

Live admission requires an independently authenticated WATCHDOG_ARMED run receipt
from an uncancelled separately supervised run, verified before baseline; distinct
strings/JSON alone are insufficient. No live watcher workflow or secret binding
is installed here. Active owner workflow remains IBM-owned and unchanged.

## Main continuity and complete source review

Default normal/watchdog/recovery still require exact current main R. Do not activate
while main may advance without a proven emergency source transition. Without an
installed qualified transition, HOLD MAIN through the entire recovery period
(including any proposed24-hour pilot), until persisted revoke is confirmed. This
would block unrelated main merges/deployments; a five-minute source hold is
insufficient for a24-hour pilot. No24-hour pilot admission is requested.

recovery_transition provides an explicit <=15-minute externally reviewed transition
for OWNED EMERGENCY REVOKE ONLY. A protected manual owner invocation pins the reviewed
manifest digest, original contract/C/P/R/module tuple, immutable original checkout
commit AND entire Git tree, and exact separately reviewed new current-main SHA.
Authenticated source checks read that exact current main before/after; only pinned
original recovery code executes. Normal writes/watchdog cannot use this transition,
and it cannot change IDs/plan/TTL or infer compatibility from a hash alone. A fresh
transition must be reviewed for each new main; no broad current-main bypass exists.

The full pinned checkout tree covers transitive repository source identity; hashes
do not prove review. Before live installation, review the actual complete import/
schema/compiler/package closure and pinned third-party dependencies, trusted runner
and checkout/import policy. Authenticated GitHub/Vercel observers and authoritative
durable one-build/stage claims remain uninstalled blockers. No fixture or proposal
JSON is live production proof.

Transaction budget is the minimum of client AND server remaining time; server
normal-window lower bound and permit freshness are checked as well. Actual SQL
fixtures use current UTC finite windows. Clock-skew tests cannot admit before the
server opens the window or hold a delayed normal transaction past server cutoff.

The server timer is explicitly reset to zero before arming, then the server
remaining budget is calculated in the SAME statement as set_config. Qualification
injects real pg_sleep before configuration and an inherited60-second transaction
timer; neither can extend the normal transaction through the absolute cutoff.

A counterfactual test executes immutable before-fix40ee adapter source ONLY against
the loopback disposable fixture: delayed PostgreSQL baseline commits after cutoff.
New adapter delayed-write/post-inventory tests prove zero committed rows instead;
the counterfactual is followed by independent owned revoke and has no live runner.
