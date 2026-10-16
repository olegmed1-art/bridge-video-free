# Oracle Light: dormant service CLI staging

Status: ASSURED / I2 review required. This is a standalone operator tool, not a deployment or scheduler hook. Merging it does not install anything. Light stays on HOLD; no queue tasks, authentication, service restart, environment changes or native worker activation are authorized by this procedure.

## Scope and prerequisite

PR #1953 added the dormant `light` native profile. The ubuntu CLI installation and ubuntu account authorization are separate evidence and do not establish readiness for the `school-autopilot` account. The live worker remains the reviewed `worker_v17` release; service CLI staging alone cannot make a READ_ONLY pilot operational.

Root application is currently blocked by the available remote command channel's privileged-command restriction. Do not disguise sudo, route the same blocked action through another mechanism, or change that channel's policy. An explicitly authorized privileged operator/channel must be available before host execution. The existing root HOLD attestor must also be runnable there. No production workflow dispatch is provided.

The operator must exclude concurrent privileged maintenance for the complete transaction (the directory lock is advisory), verify current GitHub main immediately before execution, and deliver both `ops/oracle_light_service_cli_stage.py` and `ops/oracle_light_active_hold_attest.py` from that exact reviewed main into a root-owned, non-writable-by-others directory. Use Python 3.11 or later with bytecode writes disabled. A main change requires renewed reconciliation; the argument is not an override for unknown code.

## Artifact and operation

Download the pinned archive before the root transaction, without running its contents:

- URL: `https://registry.npmjs.org/@openai/codex/-/codex-0.157.0-linux-arm64.tgz`
- SHA-512 (base64): `67Y2HL4s+DEKtWr1oFz86iN+wLDRsdm6fuq8rhaxlt8QeIK79aO7Eg82Wfucou4QSisq/SSWYkwa9q7hWi8fLA==`
- Fixed destination: `/opt/bridge-school/school-autopilot-production-light/runtime-bin`

In the authorized root session only, invoke the reviewed script with `--archive` pointing to the regular, singly linked downloaded file and `--expected-main` containing the freshly verified full main SHA. Keep Python import inputs trusted; invoke with `PYTHONPATH` unset and `PYTHONDONTWRITEBYTECODE=1`. Do not execute from a user-writable checkout. Archive paths are inputs; production destination, platform, version, package source and digest have no command-line overrides.

The tool checks hostname/architecture/root identity, exact current main, active HOLD process identity, live/disk DSN consistency, read-only database login and zero nonterminal tasks. It repeats the full attestation after extraction and after promotion, requiring the same service invocation. Parent directories must be root-owned without group/world write or ACLs; the target must be absent. Disk headroom must be at least 2 GiB.

It copies and SHA-512-verifies the archive before parsing, rejects unsafe tar members and resource excess, retains the complete vendor tree, and installs a fixed absolute wrapper. Directories and allowlisted executables are `0555`; other resources are `0444`, root:root, without ACLs. It never executes package code as root. Promotion uses held-parent `renameat2(RENAME_NOREPLACE)` and fsync. The original Node/npm guard remains unchanged; the service profile has no dependency on the ubuntu Node tree or home directory.

## Failure and rollback

Before promotion, a failed check leaves the target absent. After promotion, rollback is allowed only if the entire tree still matches the captured inode/owner/mode/hash inventory and the parent chain is unchanged. The exact tree is atomically renamed back into the private staging directory, retained as quarantine, and synced; HOLD is reattested separately. No promoted tree is recursively deleted during rollback.

`TARGET_ABSENT_VERIFIED` describes filesystem rollback only. `post_rollback_hold=UNVERIFIED`, `rollback=UNCERTAIN`, or cleanup uncertainty requires inspection in the authorized privileged contour while HOLD remains. Never automatically retry, delete a changed target, loosen ownership checks, or infer queue zero from a failed attestation. Retained quarantines are operator evidence, not runnable installations.

## Separate completion gates

`STAGED_VERIFIED` means the reviewed file tree is installed with the same HOLD process and an empty queue at that check. Authentication remains `NOT_CHECKED`; no archive binary was executed by the installer.

Next, separately use the reviewed readiness probe with the explicit `light` profile to check CLI presence/version and login status as `school-autopilot` in the existing sandbox. Authentication needs its own owner-assisted step and private service credential directory; never copy `/home/ubuntu/.codex` or any token into service storage. Do not weaken `ProtectHome`, `ProtectSystem`, or writable-path restrictions.

A working READ_ONLY pilot still requires service authorization, independently reviewed native-worker wiring and actual database-grant checks, fresh full HOLD/queue attestation, and a separate decision to launch the pilot or release HOLD. This staging tool supplies none of those decisions.

## Verification

Offline fixture tests cover traversal, duplicate/reserved names, links/special files, size limits, absent helpers, source links, unsafe parent permissions, no-overwrite promotion, repeated preflight, postflight rollback/quarantine and drift refusal. They run with root ownership in a disposable GitHub Linux runner and use synthetic non-executable archive contents. The CI job has no production credentials or dispatch operation.

The official pinned archive was also verified and unpacked offline during development: 142,965,067 compressed bytes and 60 installed tree entries. Its ARM64 binaries were not executed. Local managed-root filesystem semantics prevented cross-parent movement of a `0555` tree, so transaction success must be established by the Linux CI job, not inferred from that local limitation.
