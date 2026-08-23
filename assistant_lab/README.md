# Assistant Lab v1

Status: **activated / production queue, capability dispatch, and resident Oracle worker proven**.

`assistant_lab` is an isolated compute-and-evaluation contour for improving the assistant's project workflows. It is not a second school canon, not a student-profile writer, and not a mechanism for silently changing bridge methodology.

## Current evidence — 2026-08-23

Production state activated and verified:

- Neon production project `misty-poetry-18012774`, protected branch `br-wispy-lab-b1rq54of`, contains the isolated `assistant_lab` schema;
- pre-activation recovery branch: `br-long-feather-b1ofwy72`;
- schema registry key: `2026-08-22-assistant-lab-v1`;
- canonical `assistant_lab/schema.sql` SHA-256: `dfd28f43fc87485cd3730d841ad8c9e344b74f3ee7d36ce4bb6d1a43355808b4`;
- Vercel production health: `/healthz` and `/dds3/readyz` return HTTP 200;
- routed DDS3 readiness reports `engine=DDS3`, `position_solver=ready`, `transport=remote_https`, `authenticated_compute=ready`, and `fallback_used=false`;
- Vercel capability canaries: NOOP and the exact DDS3 golden deal both **PASS**;
- Oracle instance: `bridge-school-dds3-frankfurt`, public IP `158.180.47.161`;
- resident worker `oracle-assistant-lab-1` is installed as `assistant-lab.service`, enabled, and active on the existing Oracle VM;
- installed worker code revision: `1ae856251c74839f675ab0abca8c2185587f9057`;
- resident NOOP acceptance job `4fe0ce2d-f2c7-4c0a-bd90-920fd1a8d12c`: completed, one attempt, 1495 ms;
- resident DDS3 acceptance job `1f5f4d46-10c5-4882-bbb9-61d5d9e69452`: completed, one attempt, DDS3, no fallback, 1921 ms;
- post-service-restart NOOP job `45f1072f-4ad9-4ac6-9a6c-eb51cd523336`: completed, one attempt, 416 ms;
- idempotency replay returned one existing job and did not increase attempts;
- experiment `SI-001` is recorded as **PROVEN**;
- activation issue #295 is closed as completed.

Relevant merged changes:

- PR #290 / `6d53fc0...` — initial queue, worker, capability dispatch, Oracle scaffold;
- PR #292 / `9c170db...` — Vercel runtime packaging regression fix;
- PR #293 / `74ea58a...` — provenance normalization, dedicated Unix identity, hardened Oracle installer;
- PR #303 / `740019f...` — production OIDC fix;
- PR #304 / `1ae8562...` — hourly external DDS3 production health monitor;
- PR #305 / `cc9ad14...` — bootstrap claim transaction commit and regression coverage.

Repository state is not used as proof by itself: the claims above are backed by production health checks, persisted job rows, and host-side systemd evidence.

## Goal

Reduce end-to-end latency and improve reliability for bounded compute that materially helps interactive answers, while preserving evidence and regression history.

Preferred and proven resident path:

`ChatGPT -> Neon assistant_lab queue -> Oracle worker -> hot localhost DDS3 -> Neon result -> ChatGPT`

Capability path:

`ChatGPT -> pre-created Neon job -> one-job capability -> Vercel -> authenticated Oracle DDS3 -> result -> Neon -> ChatGPT`

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

The capability route can execute exactly one pre-created bounded job. The database stores only the SHA-256 of the capability nonce. The Vercel application role has `SELECT + UPDATE` on `assistant_lab.job`, but no `INSERT` permission.

## DDS3 quality boundary

The resident Oracle worker calls the existing hot DDS3 service on `127.0.0.1:8080`. Results are accepted only when:

- `engine == DDS3`;
- `fallback_used == false`;
- returned operation matches the requested operation.

The same provenance gate is applied by the capability dispatcher. A DB-side trigger normalizes `execution_path` from immutable job kind, so a failed `DDS3_COMPUTE` cannot be mislabeled as a NOOP path.

## Persistence

Neon stores queue state, experiment metadata, regression cases, and bounded JSON results. Oracle local disk remains cache/work space only. Drive remains the human-readable laboratory record.

Persistent experiments:

- `SI-001` — interactive compute latency, **PROVEN**;
- `SI-002` — assistant error regression.

Seed regression classes include stale infrastructure state, Oracle/IONOS target confusion, Vercel runtime packaging, remote-readiness vs repository-state confusion, execution-path provenance, and bootstrap claim transaction handling.

## Safety boundaries

Assistant Lab may create and evaluate experimental artifacts, but v1 must not:

- write or modify the school canon;
- alter L1/tournament rule semantics;
- write person-specific student/profile skill state;
- execute arbitrary shell or repository code from a queue payload;
- automatically run mass DDS stages;
- run BEN, video processing, or bulk workloads without a separate explicit activation decision;
- silently promote an experiment into school methodology.

All lab output is `EXPERIMENTAL` unless separately reviewed and promoted through the appropriate domain gate.

## Database activation

`assistant_lab` is active in production Neon. Before activation it was staged on a temporary branch, exercised for enqueue/idempotency/privilege behavior, and a recovery branch was created.

Production hardening includes:

- PUBLIC access revoked from lab tables/functions;
- `bridge_school_app` receives only schema usage plus `SELECT + UPDATE` on `assistant_lab.job`, no `INSERT`;
- `assistant_lab_worker` is a NOLOGIN role with only schema usage plus `SELECT + UPDATE` on `assistant_lab.job`, no `INSERT`;
- the Oracle VM uses a dedicated login principal delivered through a short-lived, one-time bootstrap ticket;
- the used bootstrap ticket was revoked after acceptance;
- the bootstrap endpoint commits the atomic claim before returning credentials, covered by regression tests.

The schema checksum is emitted by CI and recorded in `public.schema_migration`.

## Oracle resident worker activation

`ops/oracle_assistant_lab_install.sh` is the idempotent installer for the existing Oracle DDS3 VM. It:

1. refuses to run without a protected dedicated Neon DSN;
2. verifies real hot localhost DDS3 and `fallback_used=false`;
3. creates the isolated Unix user `assistant-lab`;
4. builds a bounded Python runtime;
5. verifies the DB principal has only required Assistant Lab access and no job `INSERT` privilege;
6. writes the secret environment root-only without printing credentials;
7. installs a hardened systemd unit;
8. activates only when `ASSISTANT_LAB_ACTIVATE=1` and all preflights pass.

The checked-in unit uses `NoNewPrivileges`, `ProtectSystem=strict`, `PrivateDevices`, kernel/control-group protections, and a dedicated Unix identity. Host-side evidence confirms the unit is enabled and active.

## Operational gates still open

The compute path is live. The following OCI account/host protections remain required before expanding workload:

- create a boot-volume backup and assign a recurring backup policy;
- confirm the account budget and alert thresholds;
- record actual RAM and disk capacity;
- perform one planned full host reboot after backup, then repeat external and resident canaries;
- confirm the first scheduled hourly external monitor run.

Until these gates are recorded, keep BEN, video processing, bulk DDS, and mass background stages disabled.

## First experiments

### SI-001 — interactive compute latency

Result: **PROVEN**. Both capability and resident paths completed bounded canaries with DDS3 provenance and no fallback. The resident worker recovered after a service restart, preserved one-attempt execution, and respected idempotency.

### SI-002 — assistant error regression

Each confirmed assistant/tooling failure becomes a persistent machine-checkable regression case. The goal is not merely to fix a single reply but to make recurrence of the same failure class detectable.
