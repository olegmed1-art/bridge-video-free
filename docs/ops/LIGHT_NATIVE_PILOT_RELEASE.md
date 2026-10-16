# Stage the native pilot while preserving HOLD

This is a preparation step for issue #1946. It is not permission to launch a task,
change native database grants, restart the service, or release HOLD.

The manual `light-native-pilot-stage.yml` workflow takes exact reviewed main and
an independently accepted package SHA256. Compute that digest from the committed
reviewed tree with `python -m ops.light_native_pilot_release digest SOURCE`.
The package covers both installer helpers and runtime code. Dirty working-tree
bytes are excluded. The fixed runner checks current main, verifies the existing
owner-accepted SSH host fingerprint, and sends an isolated root Python bootstrap.
The host checks main again before and after staging.

The host first attests the existing live HOLD, empty active queue, fixed Neon
binding, and the same service invocation. It retains the old unit/drop-in and
private continuity evidence as create-only root files; a private restore/readback
compares the saved bytes. This does not rehearse a service restart or claim a
complete operational rollback. The environment file and credentials stay in
place and are not copied into the evidence record.

Runtime files are staged as an immutable root-owned release with a complete
inventory check. A subprocess under the service UID verifies the restricted
runtime database identity using a read-only connection, and checks the existing
Light Codex login. It neither submits nor collects a Cloud task. Successful login
alone does not prove access to the chosen Cloud environment. Service config,
NoNewPrivileges, native permissions, shared task state and pilot admission remain
unchanged. Any failure leaves the existing service in place. Retained partial
release/evidence conflicts require reconciliation rather than overwrite.

Activation remains a separate work item: a reviewed controller must verify the
actual open PR, exact head and Cloud environment access, prepare one genuine
shared dispatch, validate the scoped no-write/no-main-push window, run native
permission maintenance, and retain a tested HOLD rollback. The current module
has no activation or rollback command. The candidate deliberately omits the
non-Git broker-hold.env; a future reviewed service override must retain the
existing absolute environment-file path and broker pins. This release cannot
be passed directly to the existing HOLD upgrade installer. A successful stage marker is not a
production pilot completion marker.
