# Broker security release candidate — not deployed

Coordination: PR #1876. Dependency source: merged PR #1884,
`15db0396223a72f8beda5b50e2920d1c0626ebc3`.
This candidate changes only the bundled source pin and this preparation record.
It must remain a draft until the deployment and binding gates below are met.
The existing deployment, production release manifest and Light binding are unchanged.

## Expected candidate identity

Locally computed from the candidate, not a runtime attestation:

| Field | Expected value |
| --- | --- |
| source_sha | `15db0396223a72f8beda5b50e2920d1c0626ebc3` |
| artifact_sha256 | `a539af29dc7ba2e1e9bc0581b4c32dfc74f72a26c7b77a9d05f3c7fe3893e133` |
| policy_sha256 | `46aa85fb04b8221edbd2341ce26d30eb1f1648d11e8726cd18eb4067eb981aaa` |
| provenance_sha256 | `69f6bb26d9ccdafd1e494b19619ff3458926ac1ef8a90a22ec7883d00495118a` |
| policy_version | `physical-no-merge-v2` |

Validation: Python 3.12.14, `uv sync --locked --offline`; 39 broker
tests PASS with `PYTHONPATH=..:.`; independent static policy contract PASS.
An initial test invocation omitted the repository parent from PYTHONPATH and
failed to import autopilot_phase3b; the corrected full run passed.

## Administrative Preview gate

The Vercel project is `prj_KvQo3rPnwNs488hyDiMZ9hMU9d5R`, team
`team_qXr2smag8blW1WWeS10CDRXb`. It is not Git-connected.
Use an authorized environment with Vercel API access and the exact reviewed
candidate commit. Do not deploy main instead, reuse the old build output, add
an alias, select Production, or expose credentials in logs.

The existing approved CLI sequence is:

```sh
vercel pull --yes --environment=preview --cwd autopilot_token_broker_service
vercel build --cwd autopilot_token_broker_service
vercel deploy --prebuilt --cwd autopilot_token_broker_service
```

Before executing, verify CLI project/team linkage against the identifiers above.
Authentication must use the administrator's existing authorized credentials.
The current connector returns `Tool deploy_to_vercel not found`; no deployment
has been performed by this preparation.

Capture the immutable deployment ID, exact candidate commit metadata, READY
state, Preview target, no aliases, and authenticated `/healthz` from that exact
deployment. Compare every expected identity above and require mailbox 1703,
preview_only/source_attested/artifact_attested true, all required bounded
capabilities true, and production_mutations/raw_token/merge/ref-update-delete/
actions/deployments capabilities false. Independently recompute artifact and
provenance before accepting the response. No task dispatch is a health test.

## Binding and completion gate

Only after real readback may a reviewed follow-up update verifier constants,
the exact broker-release manifest and their tests together. The old
verify_light_broker_1703_release.py deliberately rejects this candidate.
Do not weaken it or replace its expected values with arbitrary health output.

The historical stopped-HOLD updater assumes the old installed release and
broker URL. It is not compatible evidence for today's baseline. Prepare and
independently review an updater against fresh host/database/service state,
preserving secrets and route locks, staging code and pins atomically with a
verified rollback to the previous immutable release while stopped in HOLD.
No resume, old P0 replay, pinned-task change or retry-budget reset is included.

Administrative tracking cannot use register_universal_work_item yet: it inserts
READY work that can be claimed before OWNER_GATED hold. Implement and test
atomic held intake before registering such work. A PR comment is not a native
dispatcher ACK. Keep rollout and dispatcher handoff open until their actual
readbacks are recorded in #1876.
