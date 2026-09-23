# Light broker mailbox 1703 release gate

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

After the readback, update `ops/autopilot/broker-release.json` to the new
deployment URL and these four digests, and pass the rollout contract CI. Deploy
that release and the guarded worker to the held Light service through the
existing administrative workflow. Confirm the installed revision, all health
fields, database route, HOLD and PID 0 before contemplating recovery.

## Existing dispatch recovery and rollback

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
