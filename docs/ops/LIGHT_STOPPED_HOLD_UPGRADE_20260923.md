# Stopped Light HOLD upgrade to verified mailbox 1703 broker

The administrative broker blocker is resolved by
https://github.com/olegmed1-art/bridge-video-free/pull/1769#issuecomment-5803536604.
Deployment `dpl_9ubEToghB8atSFKdCVq7UDJE2vHm` is Preview READY, no aliases,
source CLI, release commit `b264cd06958ef741560809feef6eaaf481ccb886`.
The recorded health response passed the unchanged verifier. The new URL and
source/artifact/policy/provenance pins are in `ops/autopilot/broker-release.json`.

## Administrative entry point

Use only `.github/workflows/oracle-light-stopped-hold-upgrade.yml` after its PR
checks pass and the exact reviewed revision is merged to current main:

```sh
gh workflow run oracle-light-stopped-hold-upgrade.yml \
  --repo olegmed1-art/bridge-video-free --ref main \
  -f expected_main_sha="$REVIEWED_CURRENT_MAIN_SHA" -f operation=upgrade
```

This workflow uses the existing GitHub Actions SSH administrative identity and
the owner-approved host fingerprint. The remote desktop session has
`no_new_privileges`, so sudo from that session is unavailable and must not be
worked around. The previous running-worker rollout is not used.

The new installer requires the exact installed immutable `3244f4d4` release,
root-owned files, original base-unit digest, original broker pins, inactive/dead
service with PID 0, loaded HOLD, unchanged permanent route-lock inode and
Neon epoch 0. It acquires the route lock exclusively and checks current main
again before the switch. Any drift prevents the update.

The staged release includes an immutable `ops/autopilot/broker-hold.env` with
only the five non-secret broker pins and a copy of the original HOLD drop-in.
The new drop-in changes WorkingDirectory and appends that EnvironmentFile
after the original service EnvironmentFile. Systemd's last-file-wins ordering
binds code and pins through **one atomic drop-in replacement**. The original
environment file and credentials remain byte-for-byte unchanged.

Before and after that switch, a separate bounded probe runs under the existing
service UID with HOLD and read-only PostgreSQL transactions. It checks the real
restricted DB identity/ACL, original FAILED_CLOSED canary, zero active capacity,
retired-fence digest, manifest receipt and authenticated broker health using
the new pins. It never starts the worker, claims work, posts to GitHub, or writes
to the database. Final attestation requires PID 0, HOLD, unchanged invocation
and restart count, loaded code/EnvironmentFiles and verified immutable inventory.

## Rollback

Failures during the switch restore only the original, exactly compared HOLD
drop-in and reload systemd. They never start either release. Unexpected foreign
file changes are not overwritten; the operation fails closed for administrator
review. The staged immutable release is retained as evidence.

For an explicitly requested rollback after a successful upgrade, while the
same reviewed revision is still current main:

```sh
gh workflow run oracle-light-stopped-hold-upgrade.yml \
  --repo olegmed1-art/bridge-video-free --ref main \
  -f expected_main_sha="$REVIEWED_CURRENT_MAIN_SHA" -f operation=rollback
```

This verifies the installed new immutable release and stopped state, restores
the saved original HOLD configuration and remains stopped. A main, route,
service, environment or file drift requires fresh review; there is no override.

## Verification and limits

The compatibility probe has tests for FAILED_CLOSED identity, attempts/lease,
competing work and cost drift. The switch has injected reload, loaded-config,
restart-counter, health-probe, fsync and before/after-switch failures, plus a
foreign-drop test. None of these paths invokes systemctl start/restart.

Passing this workflow confirms a compatible installed **stopped** release.
It does not authorize or perform dispatch recovery, ACTIVE admission, send,
ACK or terminal receipt. Preserve task `f05c605f-f664-4ff7-9927-a039f000a929`,
dispatch `322dd440-30b9-49d2-8e1a-f5ecc1d2b99b`, PR #1867 and pinned target
SHA `2586929313ab40326d64353b513ff86e5ae3350c`. The separate production recovery
wrapper and controlled one-send observation remain subsequent gates.
