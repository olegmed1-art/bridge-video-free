# Backup-only maintenance credential candidate — 2026-10-01

Status: review branch only; ASSURED. Not activation, merge, deployment, or live-run
approval. Base main: `1440920191e1778fb9a9ba24e6701937a1a7459c`.

## Scope and evidence

The incident handoff reports seven backup failures September 25–October 1 at
the initial owner authentication query, before dump/encryption/upload/restore.
This change selects the existing `database-production` environment secret
`LIGHT_MAINTENANCE_DATABASE_URL` only for backup. The shared `NEON_DATABASE_URL`,
all writers, `BRIDGE_HEALTH_DATABASE_URL` and `NEON_BACKUP_PASSPHRASE` are unchanged.
No secret value was retrieved; no production SQL or dump was executed in review.

Read-only Neon control-plane metadata on October 1 confirms PostgreSQL 18 and:

- project `misty-poetry-18012774`;
- protected/default branch `br-aged-mud-b1i64914`, production, restored from
  the September 24 15:55:14 UTC point, restore finalized;
- direct endpoint `ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech` currently
  bound to that branch;
- `neondb_owner` exists with password authentication. No password was requested.

Existing maintenance code expects `neondb_owner`; registry credential validation
only covers two registry tables. Control-plane role existence does NOT prove
current credential authentication, SQL ACL coverage or RLS bypass. Neon documents
the default owner's broad access, but this is supporting context, not live proof:
https://neon.com/docs/manage/roles and https://neon.com/docs/manage/database-access.

## Fail-closed behavior

`ops/neon_backup_source.py` uses only the Python standard library and the existing
PostgreSQL 18 container. It validates the URI, rejects routing/role/service query
overrides and ambient `PG*` settings, then reconstructs fixed direct host, port,
database, owner, verify-full TLS and required channel binding. A pooled URI is
accepted only for the exact same endpoint and reconstructed as direct.
The runner CA bundle is bind-mounted read-only; no CA bundle is assumed inside
the PostgreSQL image. Credentials travel via environment, never argv. Client output and exception
details are captured; failure emits only `BACKUP_SOURCE_REFUSED`.

Before statistics and again before dump, a bounded read-only preflight checks
database/current_user/session_user, PostgreSQL 18, transaction_read_only, all
three Neon pg_settings identity tags including immutable context/configuration
source/reset value/no pending restart, schema USAGE, table/partition/materialized
view SELECT, sequence SELECT, large-object SELECT, and active RLS. It does not
grant privileges or use `--enable-row-security` to silently accept a partial dump.
PostgreSQL references: https://www.postgresql.org/docs/18/functions-info.html and
https://www.postgresql.org/docs/18/app-pgdump.html.

Both statistics and dump use the reconstructed read-only settings. Preflight has
a 10-second statement timeout; statistics have 10 minutes per statement and
5-second lock timeout (with a tighter client wall budget). pg_dump resets session
timeouts, so it receives `--lock-wait-timeout=5s` explicitly. Client wall budgets
are 45 seconds for preflight/stats and 660 seconds for dump. On timeout, force-remove
only the uniquely named container, with a further 15-second cleanup budget.
No new production role, grants or secrets are assumed.

Checkout uses the event SHA and requires it to equal current main before DB work.
Old reruns and the legacy recovery push refuse unless their SHA is current main.
In particular the old trigger-file commit on a recovery branch now refuses;
use the existing owner issue-comment command after separately approved activation.
The operational trigger surface, encryption format, passphrase, upload and
isolated restore are unchanged. Test/review branch pushes do not trigger backup.

## Remaining acceptance gates

Review evidence on October 1: eight stdlib unittest cases and the workflow
contract pass; PyYAML 6.0.3 parses the workflow, Git Bash `bash -n` parses every
run block, and pglast 7.14 parses preflight/statistics SQL. `git diff --check`
passes. SQL parsing is not PostgreSQL execution. Independent I1 review and a
different-model I2 Red Team (GPT-6 Astra) identified CA-bundle and Docker/dump
timeout issues; both were fixed and re-reviewed with no remaining review-code
blockers. There was no live PostgreSQL/Docker integration test. Publishing this
branch is not evidence of credential viability or restore success.

1. Offline contract/unit checks, YAML/Bash/SQL syntax validation and independent
   I2 review of the exact candidate revision. Mocked tests cannot prove live ACLs.
2. Fresh main SHA and Neon mapping check immediately before activation; reconcile
   maintenance activity. No endpoint/branch reassignment or restore may overlap.
   Preflight is an observation across connections, not a control-plane lock.
3. Separately authorized, bounded read-only candidate preflight using only the
   existing maintenance secret in its environment. Run `preflight` mode only;
   no counts, dump, encryption/upload, restore, grants, or secret changes. Stop on
   any refusal. If new privileges/role/secret are needed, return a proposal.
4. Explicit approval to activate the reviewed SHA, including its daily schedule.
   Merging would enable subsequent scheduled use; review publication does not.
5. Separately approved one-shot backup/encrypted upload/isolated restore against
   freshly reconciled production, with retained hash, size, retention and verified
   restore evidence. Do not claim backup recovery proven until this succeeds.

Existing limitation: source counts and dump use different snapshots. Concurrent
writes or DDL can cause restore-count mismatch. Source information_schema counts
are privilege-filtered. Do not disable equality checks or stop writers without
authorization; if this blocks acceptance, propose synchronized snapshot/count
work separately. Catalog preflight is necessary, not proof that every extension,
foreign-data dependency or isolated restore will succeed.

## October 90-day generation — proposal only

The existing gate is `date -u +%d == 01` at manifest creation, not run scheduling.
A run started October 1 but reaching that step October 2, or a late rerun, will
only upload the 35-day daily generation. No October 1 artifact is presumed to
exist after the reported failure. Keeping this gate unchanged avoids expanding
operational triggers during credential remediation.

Before the separately approved acceptance run, review a tiny October-only
catch-up selector bound to owner, issue #526, exact current main SHA and explicit
target month `2026-10`. It should create one additional encrypted 90-day artifact
from the successfully generated acceptance backup, retain the actual UTC creation
time, mark `retention_month=2026-10` and `generation_reason=approved_catch_up`, and
require successful isolated restore evidence. A late copy is recovery coverage
for October, not reconstruction of October 1 data; never backdate it. The selector
must expire after its approved run/attempt and must not turn every daily upload
into a monthly one. If remediation occurs after October, approve October's
coverage designation explicitly rather than infer a historical snapshot.

Alternative: once a daily artifact has passed restore, separately authorize
downloading and reuploading exactly that encrypted payload and manifest for
90 days with checksum verification and catch-up provenance. This needs no new
production read or plaintext decryption, but requires its own reviewed artifact
operation and approval. No artifact retention change was performed here.

## Proposed bounded live approval (not requested/executed by this review)

Stage A: one read-only `preflight` attempt, at most 2 minutes, exact reviewed SHA,
existing maintenance secret only, exact identity above, no automatic retry, no
other workflow steps. Capture only sanitized PASS/REFUSED. Control-plane mapping
must remain stable. This stage does not authorize merge or schedule activation.

After Stage A PASS: separately approve activation and one backup/restore acceptance
run with a 20-minute overall monitoring/cancellation budget, no production writes,
existing encryption key unchanged, restore only to ephemeral PostgreSQL 18,
encrypted artifacts only, and explicit October catch-up selection as reviewed.
The existing workflow has 25-minute per-job limits; a 20-minute total bound must
be implemented/reviewed or actively enforced before using this proposed approval.
Failure requires stop and diagnosis, never new grants or credential rotation.

Rollback: revert this backup route/helper integration. This restores the known
authentication failure, not working backups. Retain verified encrypted artifacts.
