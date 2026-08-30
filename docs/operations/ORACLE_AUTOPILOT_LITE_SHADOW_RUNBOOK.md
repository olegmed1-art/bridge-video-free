# Oracle Autopilot Lite — shadow runbook

Статус: **IMPLEMENTATION / NOT ACTIVATED**

Tracker: #782

Runtime dependency: #881 and #627

## Purpose

This contour proves continuous task-to-task dispatch on the existing Oracle VM
without Vercel Workflows in the scheduling path. It is shadow-only:

- no model calls;
- no arbitrary shell;
- no GitHub, Drive, media, canon or production mutation;
- no new fixed subscription;
- service installation is staged by default and leaves the service inactive and
  disabled;
- the Autopilot source is copied into an immutable, root-owned release under
  `/opt/bridge-school/school-autopilot/releases/`; the shared checkout used by
  DDS3, BEN, Assistant Lab and video services is not switched to the draft PR.

## Source files

- `database/migrations/0300_autopilot_oracle_shadow.sql` — canonical Neon state and RPCs;
- `database/tests/300_autopilot_oracle_shadow.sql` — state-machine, dedupe, budget and ACL proof;
- `oracle_autopilot/worker.py` — resident direct-Neon dispatcher;
- `deploy/oracle-autopilot/school-autopilot-shadow.service` — bounded systemd unit;
- `ops/oracle_autopilot_shadow_install.sh` — fail-closed staging/activation script.

## Connection requirement

The worker uses a dedicated direct Neon endpoint. A hostname containing
`-pooler` is rejected because transaction pooling cannot preserve
`LISTEN/NOTIFY` session state. TLS and channel binding are mandatory.

The checked-in migration creates only NOLOGIN capability/principal roles. A
separate LOGIN principal (expected name: `autopilot_runtime_login`) is
provisioned outside Git and receives only membership in `autopilot_runtime`.
Credential provisioning happens only after the temporary-branch and review
gates. The migration owner credential must never be used by the worker.

## State algorithm

```text
READY
 -> atomic claim / RUNNING / lease_epoch + 1
 -> allow-listed deterministic transition
 -> DONE | WAITING_EXTERNAL | OWNER_REQUIRED | FAILED_CLOSED | BUDGET_STOP
 -> immediately claim another READY task
```

An accepted external event is deduplicated by provider/event ID, correlated to
one active wait, and returns the task to `READY`. It never transitions directly
from `WAITING_EXTERNAL` to `DONE`.

## Verification order

1. Apply migration `0300` to a temporary Neon branch derived from the current branch.
2. Run `database/tests/300_autopilot_oracle_shadow.sql` there.
3. Run Python contract tests and inspect the service unit.
4. Run the independent bounded state-model checker.
5. Prove one smoke task, one duplicate-event wait/resume task, stale-lease
   recovery, external-wait expiry, budget stop and one owner boundary.
6. Record task-to-task dispatch latency; target p95 is at most five seconds.
7. Delete or retain the temporary branch according to the evidence plan.
8. Stage the Oracle unit with `AUTOPILOT_ACTIVATE=0`.

The staging transport pins all three recorded Oracle SSH host keys. The
temporary Neon DSN is encrypted with RSA-OAEP/SHA-256 to the Oracle RSA host
key before it enters GitHub. Only ciphertext is retained in the bounded request;
plaintext exists in transit only inside the protected runner process and is
written on Oracle as the root-owned mode-0600 environment file.

## Activation boundary

Activation requires all of the following:

- temporary Neon branch SQL evidence is PASS;
- independent assurance reaches I2;
- #881 provides the required production canary proof;
- #627 proves that the idle-stop guard cannot stop active/unknown Autopilot work;
- the director accepts the measured incremental Neon compute cost of the
  persistent fast-wake mode;
- the director authorizes the bounded shadow activation scope.

Even then the exact activation input must include:

```text
AUTOPILOT_ACTIVATE=1
AUTOPILOT_ACTIVATION_SCOPE=SHADOW_ONLY
```

No setting in this v1.1 implementation enables production mutation.

## Recovery

- `systemd` restarts a failed process after two seconds;
- a lost notification is covered by 30-second polling;
- an expired lease is requeued within the attempt budget;
- stale tasks that exhaust retries become `FAILED_CLOSED`;
- canonical state remains in Neon, not on Oracle local disk;
- stopping/disabling this service does not stop Assistant Lab, DDS3, BEN or video services.

Rollback for Phase 1 is to disable the staged service and remove the temporary
Neon branch. GitHub command workflows and the existing Vercel compatibility
spike remain available as non-primary recovery paths.
