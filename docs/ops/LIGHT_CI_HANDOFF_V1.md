# Versioned file handoff for the finite CI reader

Status: local production candidate, not deployed or admitted for a live task.
Scope is only `GITHUB_CI_READ_ONLY_V1`. The successful 2026-10-18 cycle is
historical regression evidence for one execution, not approval of these changed
source bytes, continuous scheduling, student-facing teaching, or other capabilities.

## Reuse and the small change

The existing `light_ci_once_admin.prepare` still builds the pinned runtime and
source packet. The source manifest now includes `ops/light_ci_handoff.py`, so
the candidate has a different package digest. Existing bind/rebind constants,
old package22bb46..., old plan35480f... and their approvals must not be reused.
No new binding/recovery mechanism is included in this candidate.

The existing owner SQL transaction remains the only registration mechanism.
Its same-key/empty-queue guards, advisory transaction lock, create-only task,
max_attempts=1 and atomic returned receipt are unchanged. The handoff helper
does not contain a database connector or execute these SQL statements.

The existing supervisor, route lock, live plan/preflight/main/HOLD checks,
durable launch intent, systemd sandbox and whole-cgroup exit proof are reused.
The claim/heartbeat/completion algorithm, runtime source, budget and permanent
service configuration are unchanged. `accepted_control` gains internal reader
and clock-mode parameters for offline packaging and historical inspection;
administrative launch callers retain protected root reads and live validation.

## The two-file receipt, format1

Offline preparation accepts only the genuine returned registration object,
not a connector/tool wrapper or a manufactured replacement for an uncertain ACK.
It checks the existing registration schema/UUID/plan/goal/counters, canonicalizes
the object and creates a new directory containing:

- `registration.json`: exact canonical registration object plus one newline.
- `HANDOFF_V1.json`: strict kind/version1, package/plan digests, task UUID and
  SHA256 of the complete canonical registration file.

The caller receives the SHA256 of the complete canonical HANDOFF file. This
digest must reach the owner through the trusted approval/coordination channel.
Calculating a digest from an untrusted received bundle and accepting that same
digest is not authentication. Unknown versions, extra/duplicate keys, booleans
in integer fields, altered UUIDs/hashes and partial/noncanonical files fail closed.

Files are create-only and fsynced, including their containing directory on
POSIX. Any write/fsync failure is an error, never a prepared receipt. Preserve
the partial directory; no overwrite/resume/cleanup behavior is provided.

## Future operation order, not current execution permission

The following commands contain placeholders. There is no admitted new plan,
task key, database task, transfer, workflow or live run in this local preparation.

1. Review a new exact source packet, target and authorization in advance of
   any future material action. Stage/verify the accepted source using the
   existing owner channel before starting a short preflight window. Verify all
   source digests, including this helper, before executing package Python.
2. Run the unchanged host preflight and owner read-only SQL preflight. Retain
   the original unchanged_snapshot. Only then use the unchanged register request
   once as one atomic transaction through the authorized owner DB connector.
3. Save its positive registration object as a file and prepare the receipt
   directory locally, using the already accepted source packet:

   ```sh
   python -B -m ops.light_ci_handoff prepare --package PACKET --accept-package-sha256 PACKAGE_SHA --accept-plan-sha256 PLAN_SHA --registration-json OWNER_REGISTRATION_OBJECT.json --handoff RECEIPT_DIR
   ```

4. Transfer these two small, nonsecret files through the existing owner SSH/file
   channel into a root-owned0700 receipt directory, root0600 files, single links,
   protected root-owned parents. No credentials, new keys, access-policy changes
   or dependency installation are needed or provided. The owner must already
   possess the independently accepted HANDOFF digest and source package pins.
5. Invoke the short owner command once. No JSON/base64 payload is embedded in it:

   ```sh
   sudo -n -- PYTHON -I -B PACKET/ops/light_ci_handoff.py launch --package PACKET --handoff RECEIPT_DIR --accept-handoff-sha256 TRUSTED_HANDOFF_SHA
   ```

The helper authenticates both files and delegates directly to the existing
supervisor using the staged `registration.json`. It does not create a second
root registration file, run a shell, register again, or retry. The supervisor
rechecks live plan/main/HOLD and preflight age<=600s under its existing lock.
At least180 seconds must remain in the plan. Source staging and scope approval
should precede this window; the helper does not extend or bypass any deadline.
The accepted historical cycle had only14.722058 seconds of preflight margin,
which motivates removing the long manual payload handoff.

## Stop, retain and inspect

There is no new stop/restart/cleanup command. The unchanged transient service
has RuntimeMaxSec165, start/stop5s each, KillMode=control-group and Restart=no.
Persistent HOLD remains unchanged. Unknown DB registration/commit ACK means
read-only reconciliation of that existing identity, never a second registration.

Any retained/partial launch intent prevents another systemd call. A timeout,
missing launch ACK, lost completion ACK or result-write failure retains the
available journals. Do not clear them, reuse the completed key/window, create
a replacement task, call fail_task after an uncertain completion, or infer PASS.

The receipt helper can show a safe projection of the retained root records:

```sh
sudo -n -- PYTHON -I -B PACKET/ops/light_ci_handoff.py inspect --package PACKET --handoff RECEIPT_DIR --accept-handoff-sha256 TRUSTED_HANDOFF_SHA
```

Inspection permits an expired plan only for reading. It has no launch path,
does not query a DB, contact GitHub, inspect/modify a live service or write a
file. It reports `INSPECT_ONLY_NO_REPLAY` for absent/partial/unknown records.
`RUNTIME_RESULT_RETAINED_OWNER_ACCEPTANCE_REQUIRED` means matching retained
terminal/exit records exist, not current exit/HOLD proof or final acceptance.
Its output never grants launch permission. Full final acceptance still needs
the existing pure owner checker, exact retained evidence/cardinality, unrelated
snapshot equality, fresh public result and protected HOLD/actual invocation exit.

CLI exit0 means preparation, launch readback, or retained-result inspection
completed with the stated limited result; it is never final production acceptance.
Inspection returns exit3 for `INSPECT_ONLY_NO_REPLAY`; validation/I/O failures
return exit2. A retained intent timestamp must meet the original launch rule
of at least180 seconds remaining, even when inspected after the plan expires.

## Local verification and remaining admission boundary

Targeted tests combine the real supervisor/runner/journal through explicit
filesystem, identity, systemd and DB test ports. They cover duplicate/partial
handoff, digest/schema drift, stale/expired admission, duplicate launch, lost
launch ACK before/after completion, result/fsync failure and secret-free errors.
Existing claim rollback/unknown completion/concurrency and atomic-registration
argument guards are retained. The historical accepted result is passed only to
the pure acceptance checker; that old task is never prepared or executed again.

These Windows offline tests do not establish Linux filesystem metadata,
directory-fsync/flock behavior, real systemd or duplicate SQL transaction
behavior against Postgres. Existing local guard assertions are not a live DB
rollback/concurrency exercise. No real cloud, network, DB, service or subprocess
entrypoint is allowed by the local test runner.

Before any publication/live acceptance: reconcile then-current main and exact
candidate source/target, independently accept its new package/receipt pins,
review CI/deploy fanout and cost effects, and obtain any required exact scope
authorization. A separately admitted useful bounded canary must establish actual
receipt transfer permissions, new helper CLI, unchanged HOLD, exact DONE/evidence
and whole-cgroup exit. The closed cycle and its approvals cannot authorize it.

## CI source-checkout integration

Run `python -I -B -S scripts/run_light_ci_offline.py` from a source checkout.
The finite stdlib suite uses the retained public source fixture documented in
`tests/fixtures/light_ci/README.md`, without historical Git objects, installed
packages, environment setup, production receipts or external I/O. Its runner
denies process and network entrypoints during discovery and execution.

The separately retained historical owner-evidence regression is skipped unless
`LIGHT_CI_REGRESSION_FIXTURES` explicitly points to that local archive. A skip is
not a current production check. Fixture assertions and the existing simulated
host ports are not a native Linux service/DB canary.

This change adds no workflow and edits no existing workflow, so it does not
connect these tests to GitHub Actions automatically. A future CI execution needs
an explicitly reviewed execution mechanism and fresh fanout/cost assessment.

