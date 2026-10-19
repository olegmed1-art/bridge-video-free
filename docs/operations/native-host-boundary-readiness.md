# Loaded HOST identity and read-only readiness

The host authority uses Manifest v2 with a required `service_unit_sha256`.
Version 1 is refused. The same-directory helper is read through protected root
metadata and verified before code execution. No credential source, installer,
service activation or issuer delegation is added by this contract.

`host_identity` now calls the fixed read-only collector. Two bounded systemctl
queries and independent `/proc` snapshots must agree with the exact prospective
unit profile. Disk bytes, loaded commands, reload state, drop-ins, process
UID/GID, actual NoNewPrivs, executable, argv, cwd, cgroup, boot/start and process
INVOCATION_ID are checked. Loaded NNP=yes cannot substitute for actual NNP=1.
Systemctl can omit empty array properties even with `--all`. Their absence
cannot establish an empty value: a separate bounded D-Bus read must attest the
seven exact typed empty arrays. Missing scalar or malformed array evidence
remains a refusal.
Existing installation, source, STOP, deadline, child ancestry and purpose checks
remain required. PID1 execution delegation is a separate authority boundary.

The CI-only fixture uses a real fixed unit on an otherwise empty hosted runner,
with a self-terminating secretless worker and ownership-checked cleanup. Seven
cases cover the actual authority entry, foreign process, Python pin, stale
reload, a no-op forbidden drop-in, effective NNP differing from loaded NNP, and
helper integrity before execution. It never grants installation acceptance.

`light_native_readiness_observe.sql` is a diagnostic SELECT for an externally
established REPEATABLE READ, READ ONLY transaction with a five-second timeout.
It reports complete counts, including unknown statuses, without task content or
row identities. Its normalizer must independently bind the selected database,
owner/full-row visibility, snapshot and exact query profile. Readiness accepts
only fresh complete source-bound evidence. Missing evidence is UNKNOWN; known
producer, backend, task or lease obligations are NOT_READY.

Quiet database rows alone cannot establish readiness. A full service-profile
provider inventory, stable local journals, current STOPPED HOLD/cgroup fence and
exclusive producer lease are independent requirements. Database and provider
reads must fall inside the same verified fence. READY_FOR_TRANSITION is only a
momentary diagnostic; every execution, transition, incident and replay authority
flag remains false. Retained capabilities on a disabled role grant no authority.

CI selects changed proof boundaries. HOST/readiness changes do not repeat the
unchanged transition rehearsal; changes to its kernel select readiness tests
as well. Changes to old proof job bodies select those proofs again. No old
task, historical UNKNOWN, original baseline or consumed action window is renewed.
