# Routed callback drain rehearsal

Preparation for #1946 following #1970. Disposable CI only; no production drain
command, server installation, new route implementation or maintenance approval.

The fixture runs the actual oracle_light_route_lease and
github_autopilot_db_route.execute_under_lease programs. It substitutes local
transport for SSH, a root-owned temporary route directory, a fixture CA hash and
a bounded SQL consumer for the production callback. It does not patch flock or
the supervisor's liveness/cleanup loop. Root in ephemeral CI is required by the
unchanged lease ownership checks. Database access is limited to the exact
localhost bridge_school_ci fixture and an actual loopback connection.

The only persistent test object is public.native_route_drain_probe, refused if
already present and dropped in cleanup. No native function or production
callback is invoked. Real PostgreSQL transactions represent consumer writes;
these tests do not validate callback SQL semantics or live credential routing.

Cases:

- Active shared lease refuses exclusive acquisition. Exclusive acquisition
  makes a new lease return busy. Paused route returns without CA/active lease.
- Normal consumer commits and exits; the actual supervisor then closes its
  lease. Exclusive acquisition and a fresh backend check confirm completion.
- With a consumer transaction still open, suspend the supervisor and kill its
  local lease transport. Exclusive flock becomes available while PostgreSQL
  still reports the consumer idle in transaction. Resume the supervisor: its
  real lease-loss handler terminates the consumer; backend disappears and the
  uncommitted row does not survive.
- Commit before killing the transport. The supervisor reports failure after
  detecting lost lease, but the committed row remains. A lost lease does not
  prove rollback, and acquiring flock does not by itself reconcile DB outcome.

SIGSTOP is acknowledged through Linux process state before killing transport,
making the race deterministic instead of relying on scheduler timing. This
fault injection represents delayed supervisor reaction, not a live SSH/network
failure or proof of every transport failure mode. The SQL consumer and CA are
explicit substitutes. All route directories are fenced under a private
/tmp/native-route-drain-* directory; internal fixture modes reject other roots.

Run the full fixture only inside disposable database CI as shown in
.github/workflows/database-ci.yml. A root-owned local protocol-only smoke can
run without PostgreSQL:

```sh
PYTHONPATH=. python database/fixtures/native_route_drain_rehearsal.py --protocol-only
```

Successful full evidence must include all three scenario markers and:

```
NATIVE_ROUTE_FLOCK_ALONE_NOT_TRANSACTION_DRAIN_CONFIRMED
NATIVE_ROUTE_LOST_LEASE_DOES_NOT_PROVE_ROLLBACK_CONFIRMED
```

These markers confirm a limitation, not a production drain PASS. A future
coordinator must prevent new admission through the existing lease boundary and
independently establish completion of all previously admitted consumers and DB
transactions while reconciling committed outcomes. Exact backend/consumer
attribution and coverage of direct/unrouted writers remain required. Do not
equate callback native-RPC denials, an empty task queue or an acquired file lock
with that proof. The permission engine's default maintenance guard stays closed.
