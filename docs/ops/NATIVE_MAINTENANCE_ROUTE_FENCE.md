# Exclusive route fence for native permission maintenance

2026-09-26. ASSURED implementation; not wired to production apply.

`ops/native_permission_route_fence.py` owns the existing root-owned route.lock
with nonblocking exclusive flock. Active shared route-v1 clients cause refusal;
new clients receive the existing busy response while exclusivity is held. The
module never edits the route, stops a service or terminates a client.

It validates directory/file ownership and modes, path/descriptor inode equality,
the existing lock-identity record and exact caller-supplied route. Each assertion
also checks same process, a monotonic 1–60 second deadline and the actual WRITE
flock reported for its descriptor by Linux procfs. It does not silently reacquire
a lost lock. Procfs PID and getpid may differ in nested namespaces: lock ownership
uses the PID reported by the same procfs mount; process continuity uses getpid.

The caller must own this context exclusively and must not close, duplicate or
transfer its descriptors. Expiry causes refusal at the next assertion; it is not
a background watchdog or an atomic cross-system stop. Closing the context releases
the lock, including on exceptions. Privileged root modification is a trust boundary.

## Evidence

The disposable root/Linux rehearsal invokes unchanged real route-v1 clients.
It checks active shared-lease refusal, new-client busy response, a competing
maintenance holder, explicit lock loss without reacquisition, deadline expiry,
route drift, file/directory replacement, FIFO refusal without a blocking open,
and release after a body exception. The future runner must enforce its own
bounded process lifetime and cleanup; this resource has no background watchdog.
Expected marker: `NATIVE_MAINTENANCE_ROUTE_FENCE_PASS`.

Tests use a temporary root-owned route directory; they contact no host or database.
The existing database CI runs them alongside its separate route/drain rehearsal.

## Remaining composition

This is one actual coordinator resource, not a complete MaintenanceGuard. It does
not prove database transaction drain, block direct owner SQL or prevent catalog
changes. The separate DB fence and coordinated critical-writer window remain
necessary across grant COMMIT and fresh inspection. Production integration must
bind reviewed source, target/route, workflow exclusions, manifest and live HOLD
checks, and handle lost/unknown outcomes. The default engine guard stays refusing.

No deployment or production acquisition is performed by adding this module.
Rollback is a code revert; no production data or route restoration is needed.
