# IBM school compute: supervised on-demand fixed workload

## Scope and status

This path runs one fixed synthetic bridge WORLD_GENERATE job on the existing `bridge-school-compute-ibm` instance. It is a compute-verification and training-input service. It is not a general task runner, a finished lesson, a pedagogical assessment, an autonomous queue worker or an unattended production controller.

The reviewed implementation produced and independently validated 256 deals in [run 38130099405](https://github.com/olegmed1-art/bridge-video-free/actions/runs/38130099405), source [f0aeba3c80f05874445cf455fda346b832051dbb](https://github.com/olegmed1-art/bridge-video-free/commit/f0aeba3c80f05874445cf455fda346b832051dbb). Three provider `stopped`/`startable=true` observations completed at 2026-10-11 09:57:18 UTC. This is historical evidence for the sealed workload, not a fresh instance-state observation or proof of the new main-specific entrypoint. After merge, one exact-main-SHA acceptance run and its contained lifecycle artifact are still required.

## Entrypoint and existing access

Use `.github/workflows/ibm-school-compute.yml` on **main** through the existing repository owner account. The only input is `mode`: `status`, `school_worlds` or `stop`. The default is read-only status. No PR or push event can start the server; those events run offline contracts only.

The school job requires owner and triggering-owner identity, `workflow_dispatch`, exact main and workflow identity, attempt 1, and an exact checked-out source SHA. A rerun of the same workflow attempt is rejected. Use a new dispatch only after diagnosing a failed run and confirming containment.

Authentication reuses the existing GitHub-held IBM API credential and existing Oracle SSH route. The IBM target, backup image/source-volume identity, host-key pin, SSH user, source hashes and original snapshot binding remain fixed. Do not copy credentials into a chat, artifact or new account; no new key, resource, subscription, IAM permission or network change is part of this promotion.

## Supervision and Stop

Before dispatching `school_worlds`, keep the independent `stop` action available and remain responsible for confirming stopped state. The production job shares the existing `ibm-vpc-power-controller` concurrency group with review work, so two starts cannot overlap. Stop uses the separate existing `ibm-vpc-independent-stop` lane, has no dependency on the workload contract job and needs no Oracle SSH credential.

The controller submits at most one Start, anchors deadlines before that submission, executes one bounded workload, and enters ordinary Stop after success or failure. Limits remain: workload host 60 seconds (including at most 25 seconds of verified gated-boot settling), SSH 255 seconds, admission minimum 265 seconds, transport envelope 378 seconds, Stop submission by 420 seconds from before Start, and total observation envelope 600 seconds. A late boot never restarts these clocks. Stop success requires three consecutive fresh provider observations of `stopped` with `startable=true`; an accepted Stop request alone is insufficient.

**Do not use GitHub Cancel as a substitute for Stop.** GitHub can force-kill a canceled step before the controller's containment completes. For cancellation, timeout, lost runner/heartbeat, unconfirmed Stop or missing lifecycle evidence, immediately use the independent `stop` action on main and verify its three stopped/startable observations. Do not start another workload while outcome is unknown. The IBM control plane remains the owner's last-resort Stop route if GitHub fails.

There is no independently running watchdog in this change. Operator availability is an obligation, not an established guarantee. Consequently this service must not be described as an autonomous production contour. Next: assess a separately owned deadline/Stop watchdog using existing Oracle/IBM capabilities and existing authorization only. Any implementation needs its own reviewed failure/ownership model; do not silently add a daemon, credential placement, new access or resources.

## Fixed input and output contract

The existing `bridge_school_api.ai_worlds.generate_worlds` algorithm receives:

- North hand `AKQJ.T98.765.432`
- South exactly five hearts and 12–14 HCP
- 256 worlds, seed `20261011`, maximum 50,000 attempts

The independent validator checks 52 unique cards, 13 per seat, the exact North hand, South constraints, eight-card North–South heart fit, 256 distinct worlds, PBN/JSON correspondence and two byte-identical generation results. The current expected result accepts 256 worlds in 8,836 attempts.

Expected uncompressed outputs:

- `input.json`: SHA-256 `6cc2c61c49fe3ade94baccf22d8f8e979ba4a13dd4b2836a0496b667789d98de`
- `worlds.json`: 72,174 bytes, SHA-256 `9dc92edb3d3f5b8816f06349a85ccc1e60fb4c6472dff6a9b6bfe95a8e6e8121`
- `worlds.pbn`: 23,976 bytes, SHA-256 `a362d3ee21996dcfce872f04e29d8a99a615c67af992d51bfae98d5fc6f75420`
- Generator source: SHA-256 `c43b595ded92ee74da5ac884737585b0bfcc5b97a3c689afe126079ca04f884d`

Download the exact run's `ibm-school-worlds-<run_id>` artifact within its seven-day retention. A generated pack includes the three files above plus `receipt.json`, `manifest.json` and validated `lifecycle.json`. Validate each manifest size/hash, source commit, run ID and attempt, then run the independent validator. A guest `WORLD_GENERATED` receipt alone is not operational success: require a lifecycle result that also proves successful controller/log capture and subsequent provider containment for the same run. Incomplete, canceled, blocked or Stop-failed runs are never successful deliveries. Raw controller logs and credentials are not uploaded.

## Retained isolation and recovery

All five owned gates stay in place. All six guarded units must remain inactive/dead, with no PID/job and empty cgroups before generation. The sole settling exception observes already verified, gated, inactive start jobs until a freshly failed owned condition proves a skip; it never starts, stops, cancels or kills a service. Exact ephemeral Job disappearance can cause one fresh verification epoch only after prior complete start/waiting provenance. Unknown or permission errors, changed jobs, revival, drift and missing proof remain blocked.

The original snapshot is `38123951485-e599b3b39325`, SHA-256 `1bd1490b28c4b184729bc31b7650f83dd372af1fe545e434c4695971f0626c92`. Original archive/journal/configuration bytes are retained. Each run creates only fresh scoped proof/output files; it does not overwrite or remove an earlier result. Maintenance/isolation/release modes are not exposed by this workflow. Never repeat initial isolation over retained gates or replay historical incident operations.

On code regression, use a normal reviewed revert PR on fresh main. Do not force-reset main, change the snapshot binding, remove a gate or restart old services to make a check pass. If the snapshot/gates/configuration drift, preserve the evidence, contain the instance and review that exact cause. Output accumulation is intentional and bounded per run; no automated deletion is introduced.

## Promotion gates

The production PR must contain only the explicit IBM dependency/test closure, new manual workflow, lifecycle verifier and this runbook. Existing main files, API/generator, databases, Vercel configuration and read-only IBM workflow stay unchanged. Required protected-branch evidence includes the current strict `committed-secret-gate`; all triggered contracts must also pass on the exact up-to-date PR SHA. Preserve source-pin checks, cancellation/unknown-Start/Stop-failure tests, branch/owner/attempt rejection, workload validation and independent focused review. Merge readiness is not proof of main runtime acceptance.
