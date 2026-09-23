# Light broker mailbox 1703 release gate

> Update: broker deployment is now READY and verified in [handoff 5803536604](https://github.com/olegmed1-art/bridge-video-free/pull/1769#issuecomment-5803536604). The new URL and digests are pinned in this PR. The compatible stopped-HOLD workflow and rollback are now implemented in [LIGHT_STOPPED_HOLD_UPGRADE_20260923.md](LIGHT_STOPPED_HOLD_UPGRADE_20260923.md). Historical blockers and design notes below describe the preceding preparation stage; use the new runbook for the next administrative operation. The implementation uses a release-local non-secret EnvironmentFile and one atomic drop-in, preserving the original secret environment file.

The worker is HOLD. The single dispatch `322dd440-30b9-49d2-8e1a-f5ecc1d2b99b`
already created draft PR #1867, but the worker rejected the broker's legacy 1150
mailbox response. The task and outbox are FAILED_CLOSED. Do not run the ordinary
claim or replay path, or the prepublication 0355 refence, for this task.

## Broker bundle and administrator command

The reviewed broker code was merged as `1c5ba7074ab73a861d43503043563c6b7be5e716`
(PR #1869). Its release metadata is embedded by this follow-up change. With the
exact reviewed release commit checked out, an administrator with access to the
existing Vercel project and its Preview environment can run:

```sh
export VERCEL_ORG_ID=team_qXr2smag8blW1WWeS10CDRXb
export VERCEL_PROJECT_ID=prj_KvQo3rPnwNs488hyDiMZ9hMU9d5R
vercel pull --yes --environment=preview --cwd autopilot_token_broker_service --token "$VERCEL_TOKEN"
vercel build --cwd autopilot_token_broker_service --token "$VERCEL_TOKEN"
vercel deploy --prebuilt --cwd autopilot_token_broker_service --token "$VERCEL_TOKEN"
```

No production target or alias promotion is authorized by these commands. The
deployment must be READY and its authenticated `/healthz` must show
`role_dispatch_mailbox_pr=1703`, `source_revision=1c5ba7074ab73a861d43503043563c6b7be5e716`,
`artifact_sha256=2b4b74a1d5d02138b12f862e4e77e4dc59e550fcab84d5f6dd58855025241d9a`,
`policy_sha256=46aa85fb04b8221edbd2341ce26d30eb1f1648d11e8726cd18eb4067eb981aaa`,
and `provenance_sha256=441e25b065f31159d1f9b1f33e50fd7545933102917109b4cf94df1789e9998b`.
Keep the access token and protection bypass value out of logs.

Capture authenticated health from the new deployment into a local JSON file,
then generate a candidate manifest with the executable gate:

```sh
python ops/verify_light_broker_1703_release.py --health-json /secure/broker-health.json --broker-url "$NEW_BROKER_URL" > /secure/broker-release.candidate.json
```

The verifier performs no network requests or mutations. It rejects missing
fields, legacy mailboxes, wrong source/digests, unsafe capabilities and untrusted
URL shapes. The administrator must independently verify the captured response
belongs to that exact READY deployment. A generated candidate is not evidence
of deployment or task completion. Keep this PR open until the verified new URL
can be pinned atomically with `release.py`; changing only `release.py` on main
would make the existing rollout manifest fail its source equality check.

After the readback, update `ops/autopilot/broker-release.json` to the new
deployment URL and these four digests, and pass the rollout contract CI. Deploy
that release and the guarded worker through a reviewed administrative procedure
that supports the stopped HOLD baseline (see below). Confirm the installed revision, all health
fields, database route, HOLD and PID 0 before contemplating recovery.

## Existing dispatch recovery and rollback

### Administrative blockers confirmed after browser authorization

The Vercel browser and CLI device authorization succeeded. The official CLI
59.26.0 process was then stopped by the execution environment with
`Network access to "https://api.vercel.com:443" was blocked by policy.`
The connector deployment tool separately returned `Tool deploy_to_vercel not found`.
No new broker deployment was created. Execute the Preview commands above only
in an approved administrative environment with access to Vercel API; do not
change proxy/network controls to bypass this restriction. A browser upload is
explicitly labelled Production in this project's dashboard and does not meet
the authorized Preview procedure.

Broker deployment alone does not make the old Light workflow applicable:

| Existing component | Required baseline in code | Current mismatch |
| --- | --- | --- |
| `oracle-light-runtime-hold-rollout.yml` / `oracle_light_runtime_hold_install.py` | Running revision `962903f3`, no HOLD admission, no drop-in directory | Installed revision `3244f4d4`, stopped, HOLD drop-in already exists |
| `oracle_light_held_runtime_preflight.py` | Active/running process and original 0355 fence digest | Service stopped; narrow fence retirement already happened |
| `oracle_autopilot/light_runtime_probe.py` | Canary READY, attempts 0, one active worker, 0355 marker present | Canary FAILED_CLOSED, attempts 1; outbox attempts 5; marker retired |

These components must fail closed in the present state. Do not invoke them as
the post-broker upgrade path or relax their historical assertions. A separate
reviewed stopped-HOLD upgrade path is still required: validate the installed
immutable release, preserve the route lock and existing secrets, attest the exact
new broker health, and stage the new release. The worker reads its broker URL
and four expected digests from five `AUTOPILOT_TOKEN_BROKER_*` environment keys;
updating the JSON manifest alone is insufficient. The administrative update
must back up and compare the current environment file, replace only those five
non-secret values, update the HOLD WorkingDirectory drop-in, and verify both
files as one stopped-service operation with rollback on any partial failure.
Reload systemd and independently verify HOLD/PID 0. Its rollback must restore
the previous verified environment file and drop-in while remaining
stopped. It must never reuse the old installer's automatic restart rollback.
Do not activate until that procedure and the production recovery wrapper have
been implemented and tested against this exact baseline.

### Recovery guard hardening

The rehearsal-only candidate now checks explicit identities, mailbox 1703,
delivery version 3, READ_ONLY mode, target revision, fingerprint, all publication,
send/ACK/deadline fields, claim ownership, repair lineage, step terminal state
and work-item ownership. Missing keys and wrong JSON types fail closed.
APPLY also rejects another active dispatch for target PR 1769. The read-only
preflight now includes native CLI receipts, matching the recovery receipt guard.

`python ops/light_dispatch_1867_guard_regression.py` emits one SELECT query.
Executing it through an authorized read-only database path on the original
production FAILED_CLOSED baseline returned **167 cases, passed=true, failures=[]**.
It evaluates the actual candidate predicate against JSON copies; it installs
no function and changes no task, receipt, journal or queue row. This is evidence
for the strengthened predicate, not a new end-to-end recovery execution. The
earlier durable APPLY/ROLLBACK rehearsal remains evidence for the preceding
candidate version. The changed candidate has not been installed in production.

Review the exact database row and PR #1867 together. A recovery must preserve
the original dispatch ID, task's pinned target PR/head, previous five failed
attempts, and one PR. It needs an explicit guarded transition supported by the
database contract and a one-send receipt path. The normal FAILED_CLOSED claim
does not implement that transition. Do not activate Light until this recovery
is implemented, tested on an isolated copy, and checked against live state.

If the broker deployment or worker readback fails, leave Light HOLD/PID 0 and
retain the current broker URL and release pins. If a new held worker has been
installed, restore its previous held release using the administrative rollback
path. Do not run the old 1150 broker with the new worker or silently reset the
FAILED_CLOSED rows. A final success requires the linked send, ACK and terminal
response in the same dispatch lineage, verified by independent readback.
