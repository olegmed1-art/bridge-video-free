# Local candidate: managed-owner installation of 0060

Status at preparation: **SQL NOT_RUN / CI NOT_RUN / production HOLD**. One new
isolated PG18 CI is authorized on a standard public GitHub-hosted Ubuntu runner,
with a maximum 20-minute job and disposable synthetic database only. This is an unapplied candidate
change; if 0060 is already registered on a target, stop and reconcile its checksum.
Never edit an applied migration in place.

The previous run [38361470902](https://github.com/olegmed1-art/bridge-video-free/actions/runs/38361470902)
passed 29 SQL cases / 182 variants on **PostgreSQL 16.15 as superuser**.
It does not establish installation under PG18 `bridge_ci_owner`, nor does it
validate this changed migration.

## Decision and privilege boundary

`M` is the actual migration login; `O` is `video_subset_owner`. The intended
preflight M is `bridge_ci_owner`, NOSUPERUSER + CREATEROLE, actual owner of the
schema and existing queue objects. O stays NOLOGIN/NOSUPERUSER/NOCREATEROLE.

| Option | Consequence |
| --- | --- |
| Create new objects directly as O using `SET ROLE` | O still needs schema CREATE. It cannot create capability roles or update M's migration registry. More role switches and changes to the installation order are needed. |
| Keep all new objects owned by M | Less transfer machinery, but SECURITY DEFINER functions acquire M's broad schema/CREATEROLE authority. Not equivalent privilege isolation. |
| **Temporary grants in the existing transaction** | Four permission statements preserve the separate definer owner and existing ACL/comment order. Chosen. |

After creating O, M grants itself O membership with ADMIN false, INHERIT true and
SET true, explicitly `GRANTED BY CURRENT_USER`. M also grants O schema CREATE.
SET enables `ALTER ... OWNER`; INHERIT allows M to finish the following
REVOKE/GRANT/COMMENT statements after transfer. Before the migration marker and
COMMIT, M revokes its own schema CREATE grant and its own membership grant,
using `GRANTED BY CURRENT_USER RESTRICT`.

The SQL rejects changed `current_user`/`session_user`, nonempty
`createrole_self_grant`, and retained CREATE/self-grant at the end. The stricter
PG18/non-superuser/exact schema and object ownership prerequisites are checked
by the regression runner; the migration itself does not assert that the
production installer has the literal name `bridge_ci_owner`.

PostgreSQL's separate bootstrap ADMIN-only grants from `postgres` to M remain
after COMMIT: ADMIN true, INHERIT false, SET false. This does not make M powerless:
CREATEROLE/ADMIN can grant future access. The promise is removal of temporary
effective access, not removal of M's administrative authority.

On a pre-COMMIT failure, new roles, objects, grants, marker and changes to legacy
definitions/owners/ACL/rows must roll back. On COMMIT, the intended 0060 runtime
cutover persists; only the temporary installation privileges disappear. It would
be incorrect to require all legacy runtime grants to survive this migration.

The checksum update is a separate step in `migrate.sh` after 0060 COMMIT.
An unknown commit/checksum outcome requires reconciliation, never a blind raw
0060 retry. Idempotence means rerunning the actual migrator and observing skips.

References: PostgreSQL 18
[ownership transfer](https://www.postgresql.org/docs/18/sql-altertable.html),
[role creation and automatic membership](https://www.postgresql.org/docs/18/role-attributes.html),
[GRANT](https://www.postgresql.org/docs/18/sql-grant.html) and
[REVOKE](https://www.postgresql.org/docs/18/sql-revoke.html).

## Regression delivered

`managed_owner_install.py` defaults to an offline `NOT_RUN` case listing.
`--execute` is a future mutating test entrypoint restricted to the fixed
synthetic localhost database; it accepts no DSN or production credentials.
It bootstraps the three extensions and `bridge_ci_owner` as in
`.github/workflows/database-production.yml`. Only this bootstrap and catalog
reads use postgres. All numbered migrations and negative cases use M's login.

It uses `database/scripts/migrate.sh` for complete history through 0059, including
0058's composite parts. Seven cases then check exact error/rollback behavior:
the byte-exact original 0060, missing CREATE, missing SET, missing INHERIT,
an injected error after transfers, nonempty self-grant configuration, and a
complete candidate transaction ending in deliberate ROLLBACK.

Each case compares the before/after logical rows, registry, roles, memberships,
schemas, owners, ACLs, columns, constraints, triggers and function definitions.
Global OID allocation, sequence gaps and statistics are not rollback invariants.
These are fresh-install fixtures, not seeded production history.

The success path runs the **entire staged migration inventory**, including
pending files after 0060. It records every SQL file hash. It checks the O owners,
all five role attributes and bootstrap grants, absent temporary grants, exact
11 new API capabilities plus the retained legacy `precanary_idle_snapshot()`
read grant from 0058, and no direct table/column access for runtime capabilities.
It then reruns the real migrator and checks unchanged catalog/rows/checksum.

`database/tests/041_universal_video_queue.sql` branches on the durable 0060 marker.
Before 0060 its original scenario is preserved byte-for-byte. After 0060 it checks
the exact target grants, including the retained read capability, and rejects
legacy write grants, direct table access, unsafe roles and definer membership.
The runner executes both branches as M, then proves that an extra legacy write
grant and a missing required read grant each fail and roll back completely.
No production permission is restored to satisfy the old assertion.

This is installer + 041 acceptance only. The full production suite remains
NOT_RUN: 042 still calls legacy enqueue; 043 still requires old EXECUTE grants.
Those separate behavior tests must be adapted under their own reviewed scope.

Process timeout/interruption signals the complete POSIX process group with
bounded TERM/KILL waits; it records unknown DB outcome. This is not proof of
database cleanup. The isolated service must always be destroyed by the outer job.
Evidence-write errors must not hide the original failure.

## One authorized isolated CI

1. Reconcile fresh main, freeze this candidate SHA/tree and migration inventory,
   and confirm the existing authorization for this single run. Pin the PG18 image
   digest and record the actual server version; use a fresh disposable Ubuntu
   job, no protected environment or production secrets.
2. Start one PostgreSQL 18 service with the fixed synthetic database/user/password,
   bound only to 127.0.0.1:5432, `max_connections=8`, explicit memory/CPU limits.
   No other client sessions, existing project roles, schemas, default ACLs or
   prepared transactions may be present. Set a maximum 20-minute job limit.
3. Run the offline tests, then exactly once:
   `python database/tests/fixed_subset_admission/managed_owner_install.py --execute --evidence managed-owner-evidence`.
   Do not dispatch `database-production.yml` or any deploy job.
4. Preserve source hashes, actual PG18/M attributes, seven negative results,
   raw SQLSTATE diagnostics, before/after catalogs, checksum and idempotence
   evidence. An installer + 041 PASS does not imply full production suite PASS.
5. In an unconditional cleanup step destroy the disposable database container
   and network plus its anonymous volumes, verify their absence and save
   bounded compressed evidence in job logs (no artifact/cache storage). No automatic
   retry. Any timeout, cancellation, unexpected SQL failure or evidence gap
   makes the run FAIL/UNKNOWN and requires reconciliation.

The separate full production invariant/canary acceptance decision remains open;
the old PG16 superuser run must not substitute for it.
