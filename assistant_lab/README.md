# Assistant Lab v1

Status: **experimental / non-canonical / not deployed**.

`assistant_lab` is an isolated compute-and-evaluation contour for improving the assistant's project workflows. It is not a second school canon, not a student-profile writer, and not a mechanism for silently changing production behavior.

## Goal

Reduce end-to-end latency and improve reliability for bounded compute that materially helps interactive answers, while preserving evidence and regression history.

Target path:

`ChatGPT -> Neon assistant_lab queue -> Oracle worker -> hot localhost DDS3 -> Neon result -> ChatGPT`

The first v1 implementation deliberately supports only:

- `DDS3_COMPUTE` for bounded `dd_table`, `position_all_moves`, and `position_trajectory` calls;
- `NOOP` for queue/worker acceptance tests.

There is no arbitrary shell/code executor.

## Priority contract

Lower number means higher priority:

- `0` — INTERACTIVE: current user-facing bounded compute;
- `10` — REGRESSION: targeted regression after a discovered failure;
- `20` — EXPERIMENT: explicit lab experiment;
- `30` — BACKGROUND: non-urgent lab maintenance.

The worker always claims `priority ASC`, then earliest deadline, then oldest job.

## Low-latency wakeup

The queue trigger emits PostgreSQL `NOTIFY assistant_lab_jobs`. The Oracle worker keeps a dedicated `LISTEN` connection and claims immediately when notified. A short timeout remains as recovery polling so a dropped notification cannot strand work.

## DDS3 quality boundary

The Oracle worker calls the existing hot DDS3 service on `127.0.0.1:8080`. Results are accepted only when:

- `engine == DDS3`;
- `fallback_used == false`;
- returned operation matches the requested operation.

This preserves the existing DDS3 provenance boundary and reuses the live `SolverContext`/TT for position analysis.

## Persistence

Neon stores queue state, experiment metadata, regression cases, and bounded JSON results. Oracle local disk remains cache/work space only. Drive remains the human-readable laboratory record.

## Safety boundaries

Assistant Lab may create and evaluate experimental artifacts, but v1 must not:

- write or modify the school canon;
- alter L1/tournament rule semantics;
- write person-specific student/profile skill state;
- execute arbitrary shell or repository code from a queue payload;
- automatically run mass DDS stages;
- apply database schema changes to production automatically;
- promote an experiment to production without an explicit deployment/review step.

All lab output is `EXPERIMENTAL` unless separately reviewed and promoted.

## Database activation

`schema.sql` is intentionally stored as code only. CI verifies it but never applies it. Production activation is a separate migration/review step. The migration creates only the `assistant_lab` schema and objects inside it; DB principal creation/secret provisioning is intentionally outside the file.

## Oracle worker activation

Expected host state:

1. existing Oracle Frankfurt DDS3 VM is healthy;
2. hot `bridge-school-dds3-runtime` remains localhost-only;
3. an `assistant_lab` DB principal with least-privilege grants is provisioned out-of-band;
4. `/opt/bridge-school/assistant-lab/assistant-lab.env` contains the DB DSN and worker settings;
5. existing `/opt/bridge-school/dds3-runtime.env` supplies the static local DDS3 token;
6. the systemd service is enabled only after a NOOP acceptance job and one bounded DDS3 canary pass.

## First experiments

### SI-001 — interactive compute latency

Compare current GitHub Actions path with Neon queue + worker wakeup. Record queue-to-claim, claim-to-result, and end-to-end p50/p95. Accuracy/provenance may not regress.

### SI-002 — assistant error regression

Convert recurring project mistakes into explicit regression cases. Initial classes include stale-state assertions and provider/target confusion. A regression case is evidence for improving process, not authority to modify school methodology.
