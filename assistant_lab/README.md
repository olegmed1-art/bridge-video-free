# Assistant Lab v1

Status: **partially activated / DB + capability dispatch live / resident Oracle worker blocked by host control**.

`assistant_lab` is an isolated compute-and-evaluation contour for improving the assistant's project workflows. It is not a second school canon, not a student-profile writer, and not a mechanism for silently changing bridge methodology.

## Current evidence — 2026-08-22

Production state already activated and verified:

- Neon production branch `br-wispy-lab-b1rq54of` contains the isolated `assistant_lab` schema;
- pre-activation recovery branch: `br-long-feather-b1ofwy72`;
- schema registry key: `2026-08-22-assistant-lab-v1`;
- canonical `assistant_lab/schema.sql` SHA-256: `dfd28f43fc87485cd3730d841ad8c9e344b74f3ee7d36ce4bb6d1a43355808b4`;
- production capability NOOP canary: **PASS** end-to-end;
- production DDS3 golden canary: dispatcher reached, then **fail-closed** on `DDS3_REMOTE_TIMEOUT`;
- Oracle endpoint in the production configuration: `158.180.47.161`;
- resident Oracle worker: **not installed yet**, because no safe OCI/SSH control path is available from the current assistant tool boundary.

Relevant merged changes:

- PR #290 / `6d53fc0...` — initial queue, worker, capability dispatch, Oracle scaffold;
- PR #292 / `9c170db...` — Vercel runtime packaging regression fix;
- PR #293 / `74ea58a...` — provenance normalization, dedicated Unix identity, hardened Oracle installer.

The current physical blocker is therefore the real OCI host/network path, not the Neon queue or Vercel capability dispatcher. Repository state is not treated as proof that the VM has received a host-side repair.

## Goal

Reduce end-to-end latency and improve reliability for bounded compute that materially helps interactive answers, while preserving evidence and regression history.

Preferred resident path:

`ChatGPT -> Neon assistant_lab queue -> Oracle worker -> hot localhost DDS3 -> Neon result -> ChatGPT`

Interim interactive path, already proven for NOOP:

`ChatGPT -> pre-created Neon job -> one-job capability -> Vercel -> result -> Neon -> ChatGPT`

For DDS3 the interim path additionally uses Vercel OIDC to the Oracle HTTPS runtime; it remains fail-closed until OCI transport is actually reachable.

The v1 implementation deliberately supports only:

- `DDS3_COMPUTE` for bounded `dd_table`, `position_all_moves`, and `position_trajectory` calls;
- `NOOP` for queue/worker acceptance tests.

There is no arbitrary shell/code executor.

## Priority contract

Lower number means higher priority:

- `0` — INTERACTIVE: current user-facing bounded compute;
- `10` — REGRESSION: targeted regression after a discovered failure;
- `20` — EXPERIMENT: explicit lab experiment;
- `30` — BACKGROUND: non-urgent lab maintenance.

The resident worker always claims `priority ASC`, then earliest deadline, then oldest job.

## Low-latency wakeup

The queue trigger emits PostgreSQL `NOTIFY assistant_lab_jobs`. The Oracle worker keeps a dedicated `LISTEN` connection and claims immediately when notified. A short timeout remains as recovery polling so a dropped notification cannot strand work.

Until the resident worker is installed, the capability route can execute exactly one pre-created bounded job. The database stores only the SHA-256 of the capability nonce. The Vercel application role has `SELECT + UPDATE` on `assistant_lab.job`, but no `INSERT` permission.

## DDS3 quality boundary

The resident Oracle worker calls the existing hot DDS3 service on `127.0.0.1:8080`. Results are accepted only when:

- `engine == DDS3`;
- `fallback_used == false`;
- returned operation matches the requested operation.

The same provenance gate is applied by the capability dispatcher. A DB-side trigger normalizes `execution_path` from immutable job kind, so a failed `DDS3_COMPUTE` cannot be mislabeled as a NOOP path.

## Persistence

Neon stores queue state, experiment metadata, regression cases, and bounded JSON results. Oracle local disk remains cache/work space only. Drive remains the human-readable laboratory record.

Initial persistent experiments:

- `SI-001` — interactive compute latency;
- `SI-002` — assistant error regression.

Seed regression classes include stale infrastructure state, Oracle/IONOS target confusion, Vercel runtime packaging, remote-readiness vs repository-state confusion, and execution-path provenance.

## Safety boundaries

Assistant Lab may create and evaluate experimental artifacts, but v1 must not:

- write or modify the school canon;
- alter L1/tournament rule semantics;
- write person-specific student/profile skill state;
- execute arbitrary shell or repository code from a queue payload;
- automatically run mass DDS stages;
- silently promote an experiment into school methodology.

All lab output is `EXPERIMENTAL` unless separately reviewed and promoted through the appropriate domain gate.

## Database activation

`assistant_lab` is active in production Neon. Before activation it was staged on a temporary branch, exercised for enqueue/idempotency/privilege behavior, and a recovery branch was created.

Production hardening includes:

- PUBLIC access revoked from lab tables/functions;
- `bridge_school_app` receives only schema usage plus `SELECT + UPDATE` on `assistant_lab.job`, no `INSERT`;
- `assistant_lab_worker` is a NOLOGIN role with only schema usage plus `SELECT + UPDATE` on `assistant_lab.job`, no `INSERT`;
- no dedicated password/login principal is created until there is a secure way to deliver that credential to the Oracle VM.

The schema checksum is emitted by CI and recorded in `public.schema_migration`.

## Oracle resident worker activation

`ops/oracle_assistant_lab_install.sh` is the idempotent installer for the already-existing Oracle DDS3 VM. It:

1. refuses to run without a protected dedicated Neon DSN;
2. verifies real hot localhost DDS3 and `fallback_used=false`;
3. creates the isolated Unix user `assistant-lab`;
4. builds a bounded Python runtime;
5. verifies the DB principal has only required Assistant Lab access and no job `INSERT` privilege;
6. writes the secret environment root-only without printing credentials;
7. installs a hardened systemd unit;
8. activates only when `ASSISTANT_LAB_ACTIVATE=1` and all preflights pass.

The checked-in unit uses `NoNewPrivileges`, `ProtectSystem=strict`, `PrivateDevices`, kernel/control-group protections and a dedicated Unix identity.

Actual installation is blocked only by absence of a safe control channel to the real Oracle VM. Do not infer host CPU/OCPU/RAM or claim the installer ran until host-preflight evidence exists.

## First experiments

### SI-001 — interactive compute latency

Baseline: GitHub Actions/manual compute. Candidates: capability dispatch and resident Neon `LISTEN/NOTIFY` worker. Record submit-to-claim, claim-to-result, end-to-end p50/p95, retry/recovery, and CPU/RAM overhead. Accuracy/provenance may not regress.

Current result: the capability NOOP path is live; DDS3 candidate is blocked by real OCI HTTPS reachability; resident worker awaits host control.

### SI-002 — assistant error regression

Each confirmed assistant/tooling failure becomes a persistent machine-checkable regression case. The goal is not merely to fix a single reply but to make recurrence of the same failure class detectable.
