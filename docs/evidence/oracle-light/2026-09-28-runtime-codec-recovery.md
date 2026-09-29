# First-lane prepare refusal: historical runtime JSON codec

The first prepare on merged main bdb26414ebcd18a40f61a30dd6d42ec2ba068710
(run36484680280/job109138754721, 2026-09-28T21:14:36Z) returned REFUSED.
It is not a completed intake or a task execution.

Primary Oracle readback found both /var/lib/bridge-light-native-lane-owner and
/var/lib/bridge-light-native-lane-execution absent, both transient units
not-found, and the original legacy/native PIDs and invocations unchanged.
The verified prepare code creates its owner root before any intake SQL. The
workflow is completed, so a later effect cannot pass its live run guard.
The exclusive21:12:22–21:42:22 window was ended early; do not reuse it or rerun
the failed workflow. No task was submitted by this attempt.

Local reproduction used the exact accepted historical runtime bytes (SHA256
a5e2c6557576280caa9fc8106779c1465f3f9cfed88442848fd7fa35ee0ebdd4):
release.encoded uses ensure_ascii=True, whereas execution.parse verifies
ensure_ascii=False. The package is896043bytes; re-encoding with the lane codec
produces896001bytes. phase rejected with LANE_EXEC_RECORD before loading the
driver or creating the owner root.

The repair validates the retained package with its original release codec and
still requires the independently accepted exact byte hash, source and runtime
manifest. It does not rewrite the package or relax lane record canonicalization.
Phase integration fixtures now include escaped Unicode, so real orchestration
paths exercise this distinction. A new accepted window is required after
source CI and fresh primary checks.
