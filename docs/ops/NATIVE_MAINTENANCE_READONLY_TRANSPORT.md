# Read-only verified-source transport

2026-09-26, ASSURED preparation. This change makes the existing main-gated,
pinned-host SSH HOLD audit execute from the verified source package. It also
runs three harmless sleeping-process probes: explicit cancellation, stdin EOF,
and missing heartbeat. No production grant, owner credential delivery, service
configuration change, worker restart or native task is introduced.

The reviewed workflow supplies the bootstrap modules from exact Git objects in
its event commit. The command carries the expected commit and package digest;
stdin carries bounded canonical package bytes, followed only after READY by
BEAT or CANCEL frames. SSH authenticates the host with the existing pinned key.
The self-built digest checks delivery consistency from that trusted workflow;
it is not an independently approved mutation manifest or operator agreement.

The remote root interpreter uses isolated mode without site initialization.
Only the fixed HOLD audit entrypoint runs from the extracted package, also with
isolated Python and a clean environment. The existing HOLD audit performs its
database connection in a child running as the Light service user. No owner DSN
is transmitted. Core dumps are disabled; raw child stderr/output are not copied
to workflow logs. The public response contains only fixed result codes.

## Lifecycle evidence and limits

Packet receipt has a 15-second deadline and the package size bound. Supervision
has a 90-second deadline, 10-second heartbeat timeout, bounded control/output
buffers, and a one-frame rate check. The client has its own 110-second deadline.
The fixed child starts in a new process group with no inherited control FD.
Signals to the supervisor latch cancellation rather than throwing during fork.
On observed cancellation, EOF, malformed control, timeout or child completion,
cleanup sends TERM, then KILL to the group and reaps the direct child before
emitting the outcome. An unreaped leader reserves the group ID during cleanup.

Local/CI tests exercise actual Linux processes, including a TERM-ignoring forked
descendant, bounded output, malformed control, packet timeout, actual bootstrap
decoding and tamper rejection. The live workflow probes fixed harmless children
before running the full read-only HOLD audit.

These guarantees require the supervisor to remain alive and scheduled. Its
SIGKILL, host crash or suspension is not solved here. A descendant that changes
its session/group is also outside this fixed-process-tree guarantee. Sending
KILL and reaping the direct child is not proof that a database backend drained
or a transaction rolled back. Observed success cannot prevent a subsequent
connection loss. Heartbeats and source identity do not prove GitHub concurrency
ownership and are not a distributed write lock.

Production mutation reuse remains blocked pending independent process lifetime
enforcement, actual run/group and scoped-writer coordination, separately approved
manifest/HOLD identity, trusted owner-capable runtime, and ambiguous-commit
reconciliation. The existing fail-closed production guard stays unchanged.

Rollback: revert the workflow to its preceding direct read-only audit command.
The transport installs nothing on the host; ordinary cleanup removes its private
source directory. Abrupt supervisor death may leave source-only temporary files.
