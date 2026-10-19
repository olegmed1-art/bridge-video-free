# Finite legacy transition and independent cleanup

Status: implementation and rehearsal; no production transition adapter or Native
admission. Assurance: ASSURED / I2. This component does not resolve the historical
incident, replace the original baseline, accept a CurrentAnchor for execution,
or authorize replay. Existing observation and execution gates retain their scope.

`ops/light_native_legacy_transition.py` accepts an exact, separately pinned
request and finite forward Agreement over fixed trusted ports. It records a new
private create-only journal and verifies an independently armed PID1 cleanup
before the first stop. The original legacy invocation, release, immutable
configuration, hardening and Native HOLD identity must match fresh observations.
Stopping requires the expected process; replacement processes are never adopted.

After the legacy cgroup is empty, transition requires a fresh database, provider
and exclusive-lease drain proof. An empty database connection list alone is
insufficient. The admission drop changes by compare-and-swap, then reload and
readback establish HOLD. Starting a HOLD worker is a separate once-only step.
Each effect has a retained intent. Missing acknowledgements prohibit effect
retries; subsequent observation can establish a safe state without replay.

PID1 cleanup survives the forward process and its time window. Before a stop
intent it leaves the original ACTIVE service untouched. After uncertainty it
attempts only verified HOLD containment. Restoring ACTIVE additionally requires
its own accepted finite recovery Agreement, the original boot/boottime deadline,
an intact journal chain, unchanged Native HOLD, fresh drain evidence and an owned
process/configuration. Reopening the journal cannot renew that deadline. Failure preserves UNKNOWN and never
implies incident closure or permission for Native execution.

The stdlib portable tests exercise the kernel with contract ports. The separate
Linux CI fixture uses actual systemd transient units, `/proc` process identity,
cgroup absence, protected files, compare-and-swap and an exclusive flock. It
requires real systemd as PID1 and fails rather than skipping that requirement.
Its twelve disposable-unit scenarios cover successful HOLD, availability recovery,
lost acknowledgements, a replacement invocation, synthetic provider activity,
SIGKILL and RuntimeMax expiry. Workers have private network namespaces and use
no external service or credential. The database/provider records are explicitly
synthetic; this rehearsal provides no production database or provider acceptance.

The fixture is restricted to a disposable `/run/bridge-native-transition-fixture-*`
namespace on the CI runner. It cannot select school service names. Each CI job
has a two-minute limit; no package installation, secret input or production
deployment is added. Existing software and security checks still apply.

A production adapter must independently verify loaded unit properties and exact
execution commands, durable operation acceptance, current source/STOP guards,
the real database/provider/lease fence, and cleanup identity before invoking
these ports. Permanent supervisor installation and transient execution authority
remain separate review boundaries. The original descendant/cgroup checks must
not be relaxed to admit a PID1-launched execution unit.

Systemd semantics are documented in
[systemd.service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)
and [systemd-run](https://www.freedesktop.org/software/systemd/man/latest/systemd-run.html).
The cleanup uses ExecStopPost so partial startup and abnormal exit are covered;
the caller does not provide a pipe whose lifetime would replace PID1 ownership.
