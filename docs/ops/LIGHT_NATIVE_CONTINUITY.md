# Legacy continuity evidence for historical Native reconciliation

Status: source candidate. The pure validator and synthetic tests implement an
explicit version-2 evidence relation. The trusted host capture/acceptance adapter,
consumer integration, Linux metadata acceptance and historical evidence chain
remain **NOTRUN / UNKNOWN**. No existing controller, retirement guard, service,
database control, Permit or source-package inventory is changed by this module.

## The relation and its limited meaning

The original baseline `B` remains immutable. A current observation `C` may have a
different legitimate legacy process identity. Putting legacy into HOLD again
cannot recreate a historical PID, invocation or fingerprint. A full historical
HOLD equality guard must continue to fail on that difference.

The proposed alternative is an explicit, independently accepted chain:

```text
exact original B -> accepted actual lifecycle T0 -> ... -> Tn -> fresh exact C
```

Each transition has the full before/after identity and protected configuration
inventory, their canonical digests, the exact changed field/value pairs, hashes
of separately supplied source observations, sequence and previous-receipt hash.
Every adjacent snapshot must match. Missing historical records, disconnected
edges, additional changes, source drift or stale endpoints mean **UNKNOWN**.
Current file equality alone cannot replace the historical chain.

Success returns `ContinuityProof(assurance='CONTINUITY_ONLY')`. It establishes
the relation between the accepted observations. It does **not** prove that no
unobserved ABA event occurred, that task history is safe to replay, that a failed
operation completed, that old incidents are closed, or that Native may run.
Observation and retirement evidence may use a current ACTIVE legacy endpoint.
Native execution still requires a fresh, separately accepted exclusive cutover.

## Inputs and trust boundary

`ops.light_native_continuity.verify_continuity_v2` is stdlib-only and performs no
I/O. It takes exact canonical bytes and explicit trusted pins:

| Input | Required binding |
| --- | --- |
| `baseline_raw`, `baseline_sha256` | Original version-1 baseline bytes and previously accepted content pin; the existing prior, source, package, scope, agreement and protected fields remain unchanged. |
| `current_raw` | Fresh version-2 source observation containing the full current snapshot. |
| `receipts` | Ordered canonical version-2 lifecycle records. The manifest pins every exact receipt hash. |
| `source_records` | Map of exact observation SHA-256 to canonical source bytes. Each used before/after observation must be present. Extra records are rejected. |
| `manifest_raw`, `accepted_manifest_sha256` | Independent acceptance of the exact baseline, endpoint, receipts, source/controller provenance and validator version. |
| `journal_inventory_sha256` | Fresh digest of the unchanged original protected journal inventory, independently compared with its historical accepted pin. |
| `validator_source`, `validator_sha256` | Exact reviewed validator release and file digest supplied by the authenticated loader. |
| `now` | Trusted current integer clock. Current evidence defaults to at most 60 seconds old; the explicit bound cannot exceed 300 seconds. Acceptance lasts at most 900 seconds. |

Canonical encoding matches the retained owner format: sorted keys, compact
separators, ASCII escaping, UTF-8 bytes and no trailing newline. Duplicate keys,
noninteger numbers and noncanonical bytes are rejected. The module does not
rewrite an original baseline into this form. Incompatible original bytes remain
UNKNOWN pending a separately reviewed exact-byte reader.

An observation has exactly `version`, `controller_source`,
`controller_package_sha256`, `installed_release`, `observed_at` and `snapshot`.
The source version is 2. The manifest independently pins each source's controller
commit and package digest. The installed release must equal the snapshot release.
A snapshot has exactly `identity`, `state`, `admission`, `protected` and
`protected_sha256`. Identity retains all five historical fields: hostname, PID,
invocation, release and fingerprint. Protected entries retain their complete
name inventory, mode, read limit and content digest.

These schemas are normalized source outputs, not self-authenticating statements.
The owner adapter must first verify their relationship to **actual** original
controller/host records under the exact pinned source. An independently accepted
manifest digest is an external trust input. Computing a digest of a model's ACK,
claiming `accepted=true`, copying a source pin into a new object, or calculating
an expected result is not independent acceptance.

## Deliberately small lifecycle vocabulary

| Kind | Permitted relation |
| --- | --- |
| `STOP_HOLD` | RUNNING/HOLD to STOPPED/HOLD, PID becomes zero; invocation is preserved or cleared; fingerprint changes. No protected byte or metadata changes. |
| `START_HOLD` | STOPPED/HOLD to RUNNING/HOLD, positive PID and a new invocation; fingerprint changes. No protected changes. |
| `ACTIVATE_LEGACY` | STOPPED/HOLD to RUNNING/ACTIVE, positive PID and a new invocation; only the reviewed admission drop-in digest may change. |
| `RELOAD_ACCOUNTING` | Same running HOLD PID/invocation/configuration; fingerprint changes only through the exact existing ExecStart accounting reconstruction. |

Hostname and installed release stay equal across every edge. Base unit,
environment, credential-derived pins, route and other protected inventory entries
stay equal. A release upgrade or other legitimate configuration change is outside
this vocabulary and remains UNKNOWN; a wildcard change kind is not provided.
Rollback from ACTIVE is likewise outside this minimal version and requires a
separately reviewed concrete transition before it can appear in an accepted chain.

For activation the witness supplies base64 exact before/after drop-in bytes.
The validator permits only either canonical reviewed template: the service
section, exact working directory, one admission environment setting, and
optionally the exact release-relative broker environment file. The only byte
substitution is `AUTOPILOT_ADMISSION_MODE=HOLD` to
`AUTOPILOT_ADMISSION_MODE=ACTIVE`. Both SHA-256 values must match the corresponding
protected entry; mode remains 0644 and the limit remains unchanged. Extra commands,
environment settings, whitespace, file paths, metadata or other file changes fail.

For accounting the witness includes the full old/current fingerprint inputs and
independently captured ExecMain PID/start timestamp. Their canonical hashes must
equal the two identity fingerprints. Reconstructing just the reset ExecStart
start time and PID must reproduce all old fingerprint inputs exactly. There is
no timestamp search, arbitrary command evaluator or general JSON patch engine.

The change list is an exact sorted comparison of the fixed snapshot fields and
the five identity fields. Protected changes carry the entire before/after map.
An omitted, additional, duplicate, reordered, no-op or wildcard change fails.

## Proposed explicit v2 consumer gate

The legacy version-1 guard and `unchanged_files` remain untouched. A separately
reviewed v2 consumer must use this API deliberately, after independently loading
and accepting the evidence, rather than removing equality from the old reader:

```python
proof = verify_continuity_v2(
    baseline_raw=original_baseline_bytes,
    baseline_sha256=accepted_original_baseline_hash,
    current_raw=fresh_source_bytes,
    receipts=accepted_lifecycle_bytes,
    source_records=authenticated_observation_bytes,
    manifest_raw=independently_accepted_manifest_bytes,
    accepted_manifest_sha256=external_acceptance_pin,
    journal_inventory_sha256=fresh_unchanged_original_inventory_hash,
    validator_source=reviewed_validator_commit,
    validator_sha256=reviewed_validator_file_hash,
    now=trusted_now,
)
require_evidence_gate_v2(
    proof,
    purpose='RETIREMENT_EVIDENCE',  # OBSERVE is the other allowed purpose.
    baseline_sha256=accepted_original_baseline_hash,
    current_sha256=fresh_current_hash,
    manifest_sha256=external_acceptance_pin,
    journal_inventory_sha256=fresh_unchanged_original_inventory_hash,
    now=trusted_now,
)
```

The gate repeats freshness and the exact four evidence pins. Its frozen Python
result is a private in-process evidence object, not a signature or an admission
credential. Only trusted verified code may call or consume it. Admission, issuer,
replay, retirement **write** and no-ABA purposes are rejected. A writer still needs
its own operation-bound Agreement, accepted proposal, locks and full independent
retirement verification. A caller cannot translate proof success into RUN.

## Remaining owner adapter and integration obligations

1. Load the validator from the authenticated reviewed controller package. Extend
   the explicit helper/source inventory and test the exact installed-runtime /
   new-controller combination; a new module on disk is not a deployed verifier.
   Preserve the retained runtime and original source/package pins independently.
2. Read original records from fixed inventories with the existing symlink-free,
   root-owned, bounded no-follow/no-atime metadata checks. Retain exact original
   bytes and inode/metadata observations. Authenticate original lifecycle run,
   controller source/package, completed operation and actual host before/after
   evidence. Match all loaded unit/process/environment fields against the
   reviewed operation semantics; opaque fingerprint replacement is insufficient.
3. Build normalized observations only from these verified sources. Independently
   accept the exact complete chain and per-source provenance. A legitimate latest
   activation record covers its own edge; it does not fill earlier missing edges.
   Preserve partial and conflicting original evidence. Absence means UNKNOWN.
4. Collect a fresh endpoint using an observation verifier that understands ACTIVE
   legacy without claiming full-HOLD/DB attestation. Repeat protected file,
   endpoint, source, journal and lease checks around the operation under the
   existing locks and bounded supervisor. Clock expiry or any drift fails closed.
5. Reconcile canceled plan/work, original intent-only prepare, immutable serial
   history, accepted predecessors, DB controls/activity/terminal rows and original
   provider IDs/results independently. Continuity success cannot discharge these
   obligations. Existing no-replay and no-new-authority constraints remain.
6. Publish no operational inventory in this document or public fixtures. Real
   records, credential-derived digests and incident bindings stay in the protected
   private review package. Reconcile any uncertain writer outcome read-only.

Lease renewal is not part of this protocol. Historical evidence can outlive an
expired old execution lease, but that lease cannot authorize a new task, admission
transition or retry. A new observation/retirement Agreement must be operation
bound, current, finite and separately accepted. If lawful DB control changes also
break a historical equality, that is another explicit reconciliation obligation;
the continuity proof does not waive the database guard.

## Validation and next capability boundary

Run the stdlib tests without dependency installation:

```text
python -I -B -S -m unittest discover -s tests -p test_light_native_continuity.py -v
```

Fixtures use only `synthetic-host`, synthetic release paths and repeated dummy
hashes. They cover chained stop/start/activation, both drop-in templates,
accounting reconstruction, canonical bytes, independent pins, exact deltas,
source/endpoint drift, journal drift, freshness and rejected authority uses.
The Linux root metadata/live capture test is explicitly skipped as NOTRUN.
Synthetic PASS establishes a contract; it does not close a historical incident.

Repository repairs beyond READ_ONLY require a separate capability implementation.
The current Plan and Permit admit only READ_ONLY; the adapter admits READ_ONLY /
VERIFY with mutation/paid flags false and zero repair/cost budgets. SQL terminal
success is limited to those modes. Provider request parsing mentions REPAIR, but
its publication transport is unverified and delivery deliberately turns a proposed
successful repair into `BLOCKED / TARGET_PR_NOT_UPDATED`.

A realistic first narrow repair path needs typed scope/path constraints, trusted
publication with exact-head CAS and retained unknown-outcome reconciliation,
compatible SQL/Plan/Permit/adapter changes, leases/budgets, supervisor recovery,
independent review and a separately accepted limited live result. Continuous
task production, monitoring and recovery ownership are additional work. This
continuity module neither implements nor grants any of those capabilities.
