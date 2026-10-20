# Finite execution session

Governance: ASSURED. This boundary strengthens the existing authenticated GitHub
owner execution path. It grants no HOST delegation, installation acceptance or
production admission. Native remains subject to the existing HOLD, retirement,
database, provider and lease fences.

The controller captures one original execution clock in request v3: boot ID,
wall time, monotonic start/end and the original authenticated LaneRunBinding
deadline. The end cannot exceed the request's finite seconds, wall expiry or
that original guard deadline. Starting another interpreter never recomputes or
renews this clock. Requests v1/v2 remain readable for owned cleanup only.

Before loading the database driver, the supervisor enters ExecutionSession.
It reads protected request, script, owner context and owner execution pointer,
then performs a read-only readiness check before exclusive journal creation.
Either existing session file, including an orphan first record, is a barrier;
it is never repaired. Type=exec activation may take at most three seconds and
cannot exceed the original deadline. Original clocks and protected inputs stay
checked while waiting. A timeout before intent means no session was accepted.
The complete active identity must agree before and after intent creation, before
the first-binding record is created. Partial completion, a second entry or
another invocation requires explicit reconciliation. There is no delete,
overwrite, retry or ACK-repair method.

Two actual PID1 readbacks and independent process snapshots must agree. They
bind the fixed supervisor unit, its loaded commands, no reload/drop-ins, root
UID/GID, actual NoNewPrivs, executable hash, argv, cgroup, PID/start, boot and
invocation. The protected request and first records are checked again before
effects and during the local loop. A changed input, identity or clock closes
that session permanently.

The loaded ExecStart command must carry this process's positive PID and a
start timestamp. Only the not-yet-run ExecStopPost may have PID zero and no
start timestamp.

The PID1 runtime is bounded against the original monotonic end as well. The
controller requests a conservative remaining RuntimeMax; the guard requires
Type=exec, zero randomized extra and actual ActiveEnterTimestampMonotonic plus
RuntimeMax no later than the retained end. Late activation fails before work.
This follows [systemd v255's service timer implementation](https://github.com/systemd/systemd/blob/v255/src/core/service.c)
and the documented [runtime limit](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html#RuntimeMaxSec=)
and [monotonic activation timestamps](https://www.freedesktop.org/software/systemd/man/latest/org.freedesktop.systemd1.html).

RuntimeMax initiates service termination. Existing stop timeouts, owned
ExecStopPost containment and terminal/database readback remain separate
obligations; a timer expiry alone does not prove completed cleanup or absence
of remote work. Restore runs independently of a live session, GitHub access or
authority expiry, while retaining all existing ownership checks. It denies and
stops; it grants no new workload or later restart.

The existing GitHub authentication is still mandatory. The capture boundary
accepts the concrete LaneRunBinding; a HOST-like object or synthetic workflow
context cannot originate this request. HostAuthority's descendant requirement
is unchanged. A future permanent-host ISSUE adapter needs its own accepted
manifest/context/intent delegation and separate transient execution authority.

The selective workflow separates portable codec/selector checks from the
execution-session fixture. It is designed to use synthetic request and policy
data, no credentials, database or provider, and ownership-checked cleanup.
The selector covers the recursive ops import graph, including deferred imports
inside functions. One startup fixture uses an explicitly synthetic ExecStartPost
barrier to hold real PID1 in its activating phase; this property is not added to
the production unit. Its released and unreleased forms check readiness and
timeout before any intent, while retaining the actual production guard code.
When run, its local systemd/process observations do not establish a production
installation or full database/provider drain. Unchanged historical transition
and HOST proofs are not repeated merely because this proof is added.

Rollback is a reviewed source change. It never rewrites a retained request,
session journal, accepted Plan, original baseline or consumed action window.
This repository change does not update an installed service by itself.
