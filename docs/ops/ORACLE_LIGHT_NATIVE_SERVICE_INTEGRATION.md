# Oracle Light native CLI service integration — 2026-09-25

Status: ASSURED, staged profile correction; HOLD remains mandatory. Tracking #1946.

## Primary evidence

Main before this change: eaad9bf697dc6096ccbe581554fcf23e891e3c50.
CLI0.157.0 staged successfully by run36161835788; ubuntu owner-assisted login
confirmed CLI_AUTH_READY. Credentials remain private in ubuntu's own profile.
Full HOLD/READ_ONLY_PASS/queue0 attestation: run36161993961 at16:38:17 UTC.
Later unprivileged checks confirm active HOLD and the same invocation, but do
not refresh the database queue count.

Live systemd inventory (2026-09-25):
- User/Group: school-autopilot.
- WorkingDirectory: production-light/releases/5eb0e1bb2c2932bd8d02ff187b9cf24f6bc09c7c.
- ProtectHome=yes, NoNewPrivileges=yes.
- ReadWritePaths=/opt/bridge-school/school-autopilot-production-light/runtime.
- Production root: root:root0755; runtime: school-autopilot:school-autopilot0700.
- production-light/runtime-bin and runtime-bin/codex: absent.
- ubuntu cannot inspect private service runtime contents.

The existing service profile and readiness probe point to the older
/opt/bridge-school/school-autopilot tree. A positive probe there would not prove
readiness of the current Light unit. A symlink into /home/ubuntu conflicts with
ProtectHome and would couple service execution to a user-writable installation.

## This staged change

Add explicit bridge profile `light`:
- CLI: /opt/bridge-school/school-autopilot-production-light/runtime-bin/codex
- HOME: /opt/bridge-school/school-autopilot-production-light/runtime
- CODEX_HOME: HOME/codex-home
- Dispatch state: HOME/codex-dispatch

The Light readiness probe checks the same paths as this profile. Existing ubuntu
and legacy service profiles retain their paths; default selection stays ubuntu.
No live service configuration, credentials, files, database or admission changes.
The production release remains immutable and is not replaced by merging this PR.

## Next production stage and gates

1. Independently review an exact installer before execution by an authorized
   privileged operator. Direct Desktop Commander sudo was rejected; do not
   disguise privileged commands or alter its restrictions.
2. Freshly attest live HOLD, same invocation, read-only DB access, zero queue,
   current main, root-owned parent chain and absent destination. Never infer
   empty service credential state through ubuntu PermissionError.
3. Stage a self-contained pinned ARM64 distribution outside /home, under a
   root-owned non-writable runtime-bin. Preserve the complete native package
   resource layout; do not assume the single ELF alone is sufficient. Verify
   the original registry integrity before trusting/copying user-owned bytes.
   No npm install as root and no recursive permission changes.
4. Prepare CODEX_HOME under the existing service-owned runtime with mode0700.
   Obtain a separate owner-assisted device login as school-autopilot. Do not
   copy, hardlink or share ubuntu auth.json or tokens between profiles.
5. Verify private metadata, version, sanitized login status and path access in
   an equivalent systemd sandbox (ProtectHome and write boundaries retained),
   then repeat live HOLD/queue0 attestation. No model/task execution for this gate.
6. Review native transport integration separately: current worker.py does not
   invoke codex_cli_bridge or codex_cli_delivery. Installing a CLI does not wire
   the receipt queue, reservation/ACK/terminal path or migration0339 permissions.
   Audit those contracts against actual database state before a bounded pilot.

## Rollback and decisions

For this repository-only correction, revert the profile/probe commit; no host
rollback is necessary. The future installer must record newly created paths and
inode identities, never replace an existing target, and define restoration before
apply. Do not remove credentials or revoke a login automatically on ambiguity.
An uncertain stage result keeps HOLD and requires fresh inventory.

Owner-assisted service authorization is a separate account action. Neither this
profile correction nor CLI readiness authorizes a task, cloud dispatch, worker
activation, HOLD release, broker grant, schema migration or credential transfer.
