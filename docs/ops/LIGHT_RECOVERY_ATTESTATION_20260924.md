# Installed Light recovery attestation — 2026-09-24

The stopped upgrade succeeded in run35927483214 at installed revision
`5eb0e1bb2c2932bd8d02ff187b9cf24f6bc09c7c`, immutable bundle
`3f7736a4fa7a6796eb9fa7be10e1d01e6cd04cb803d426991c942a91f7eb1c07`.
The task remains FAILED_CLOSED. A later recovery requires fresh host evidence;
the old initial rollout and stopped upgrade cannot be used as recurring probes.

The desktop connector is currently offline. The new read-only Actions workflow
uses the existing approved SSH identity/fingerprint. It reconstructs the exact
installed bundle from its immutable Git commit, including the five public broker
pins and original HOLD backup; checks the known bundle digest, all root-owned
files, loaded service configuration, ordered EnvironmentFiles, PID0/HOLD, and
Neon epoch0 under the permanent route lock. The installed restricted-UID probe
checks broker health/pins, dedicated DB ACL, failed task, zero active capacity,
manifest receipt and the previously retired0355 digest. It does not stage files,
reload/start systemd, change the task or write to the database.

After review and current-head CI, the exact administrative command is:

```sh
gh workflow run oracle-light-recovery-attest.yml \
 --repo olegmed1-art/bridge-video-free --ref main \
 -f expected_main_sha="$REVIEWED_CURRENT_MAIN_SHA"
```

The admin script revision may move independently of the fixed installed release.
The workflow and host both reject a current-main mismatch. Drift or failed SSH
leaves the host state unconfirmed and forbids recovery. A PASS is a read-only
observation, not a transferable permission to send, nor a production SQL recovery.
The rehearsal branch guard remains intact. No rollback is needed for this probe
because it makes no configuration or database changes.
