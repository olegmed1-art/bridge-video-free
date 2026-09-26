# Independent lifetime for read-only maintenance

Change 2026-09-26, ASSURED preparation. The existing verified-source HOLD
transport now executes inside a transient systemd service. PID1 owns its cgroup
and lifetime independently of the Python supervisor and SSH wrapper.

Each unit name binds the source prefix and GitHub run/attempt plus random suffix.
An existing name is refused. Fixed properties use Type=exec, ExitType=main,
KillMode=control-group, SendSIGKILL=yes, Restart=no, TimeoutStopSec=2s and
RuntimeMaxSec=100s. PrivateTmp=yes keeps extraction inside the service's private
temporary directory; ProtectControlGroups=yes and NoNewPrivileges=yes are also
set. The inner process verifies actual unit properties, InvocationID, MainPID,
ControlGroup, cgroup population and its own unit membership before running the
unchanged read-only transport entrypoint.

This creates temporary systemd units/cgroups and private temporary directories.
It does not edit existing service configuration, restart Light, deliver an owner
DSN, grant permissions or admit tasks. The source package is extracted inside
the service, so PrivateTmp does not break its paths.

## Evidence

CI and the pinned Oracle SSH workflow run three harmless fixed probes before the
live HOLD audit. Each starts a parent with a TERM-ignoring child that calls
setsid. The observer records actual unit identity and cgroup inode, verifies the
child's separate session and same cgroup, then signals only that unit's main:

- SIGKILL main: require failed Result=signal and main status 9.
- SIGSTOP main: the separate probe-only 3-second RuntimeMax expires; require
  failed Result=timeout. No Python heartbeat or cleanup can run while stopped.
- Kill the external systemd-run launcher after stopping main: PID1 must still
  enforce that same runtime limit and clean up the service's descendants.

All require the previously observed cgroup to report populated 0 or to have
been removed, then clean up only their own unit. Failure results are read before
reset-failed. A real-systemd CI test additionally exercises the entire wrapped
transport's cancellation and successful fixture-audit paths. Local environments
without PID1 systemd skip that integration test; the dedicated CI probe command
requires systemd/cgroup2 and fails if unavailable.

## Boundary

Systemd kills descendants even if they leave the original process group. The
guarantee assumes functioning PID1/kernel and trusted code that does not migrate
itself into a different cgroup or ask another privileged service to execute on
its behalf. Host/kernel failure is not synchronous cleanup evidence.

Loss of the outer SSH wrapper does not mean immediate service death. A surviving
service remains subject to its independent runtime limit; the existing EOF/
heartbeat checks normally finish earlier. The limit starts when the service is
active; stopping can add the configured grace period and scheduling delay.
Runtime limits and cgroup cleanup do not prove database rollback, backend drain,
GitHub concurrency ownership or operator coordination. UNKNOWN commit results
still require reconciliation; no production mutation entrypoint is enabled.

The remaining production gates are actual run/group and scoped-writer agreement,
approved manifest/HOLD identity, trusted owner-capable runtime, and bounded pilot
admission. This change closes the tested supervisor-death/process-group-escape
gap of the preceding read-only transport, not those separate gates.

Implementation semantics checked against systemd v255 primary manuals:
[systemd-run](https://github.com/systemd/systemd/blob/v255/man/systemd-run.xml),
[systemd.kill](https://github.com/systemd/systemd/blob/v255/man/systemd.kill.xml),
[systemd.service](https://github.com/systemd/systemd/blob/v255/man/systemd.service.xml).
Oracle read-only preflight observed systemd 255.4 and unified cgroup2.

Rollback: revert this source/workflow change. Existing application units are
unchanged. Transient successful units unload after completion; failed probe
units are reset after evidence collection. If the wrapper dies, a failed unit
record may remain for later diagnosis while PID1 still enforces its lifetime.
