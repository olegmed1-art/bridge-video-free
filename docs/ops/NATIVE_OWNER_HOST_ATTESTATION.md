# Owner read-only probe on the protected Oracle runtime

This closes the credential-delivery/import proof gap only. It does not grant
permissions, approve a snapshot, pause workflows, admit tasks or release HOLD.
ASSURED: independent I2 review and exact-head CI precede live dispatch.

The owner-triggered manual workflow requires the current reviewed main SHA and
uses `database-production` with only `LIGHT_MAINTENANCE_DATABASE_URL` as its DB
credential. It holds the existing Oracle workload and backup groups. These
groups are not proof of direct-admin coordination for a future mutation.

The GitHub runner downloads the same three pinned ARM64 wheels as driver
preparation. A strict parser rejects credentials for other targets. The credential
is removed from Python's environment and sent solely through the existing pinned
SSH host channel's stdin. It is never embedded in remote command arguments,
source digests, logs or a credential file. Local SSH receives a clean environment.
The fixed host code is independently bound to the exact source and bundle digest;
the wheel envelope has its own digest. Duplicate JSON keys and oversized inputs
refuse. The transient PID1 service disables core dumps and limits runtime to 100s.

The host takes the existing installation lock without creating it, validates the
root-only runtime bytes against the pinned wheels and current OS Python identity,
then imports the driver under `-I -B -S`. Ambient psycopg imports refuse; loaded
psycopg modules must originate from the verified site. Existing installation files
are neither replaced nor repaired. The owner permission engine is imported only
after this validation; the runner's parser does not import the engine.

The existing owner read-only transaction checks target identity, dormant state,
empty queue/receipts and absence of the proposed EXECUTE grants. Host HOLD
identity is observed before and after, and runtime bytes are verified again.
Only a fixed redacted result and snapshot digest leave the host. Exception text
and captured SSH error output are never printed. The digest is an observation,
not an independently accepted permission manifest.

Rollback: stop using the new manual probe. It makes no persistent DB/runtime
change and performs no cleanup of existing evidence. SSH loss can leave this
read-only process alive until its existing PID1 deadline; this probe is not a
proof of the full mutating executor's controller continuity or timing budget.

Still required before ACL mutation or pilot: independently retained accepted
source/manifest recovery, the complete checkpoint/executor path, scoped privileged
writer/admin/rerun coordination, drain and exactly-one-task admission. A successful
probe must not be reported as completion of those conditions.
