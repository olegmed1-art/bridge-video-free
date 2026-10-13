# Local coverage repair: six SQL scenarios

Status: **harness implementation + offline checks only; PostgreSQL NOT_RUN for these repairs**.
No connection, migration execution, CI job, service, or publication is part of this change.
The frozen candidate and prior SQL run are separate evidence; offline tests cannot upgrade
their result or demonstrate PostgreSQL acceptance.

The source requirements are the six corresponding cases in the
[29-case plan](../plans/fixed_subset_admission_v1.json) and the local
`atomic-sql-29-evidence-requirements.json` review. The executable probes and explicit
`REQUIRED_VARIANTS` inventory live in [run.py](run.py). The inventory is independent
of probe loops: missing, duplicate, failed, or unexpected variant IDs prevent case PASS.
These six cases require 179 unique variants; they do not add a thirtieth case.

| Plan case / implementation | Required variant IDs (prefixes expanded in `REQUIRED_VARIANTS`) | Intended boundary and result |
| --- | --- | --- |
| `G01_DIRECT_DML_GUARD` / `g01` (6) | `G01_unbound_batch`, `missing_reservation`, `wrong_reservation_owner`, `missing_observation`, `complete_untrusted_fields`, `invented_batch_binding` | The first, second, third, and sixth deny at `SUBSET_LEGACY_INSERT_HOLD`. A complete DRAFT-to-FINALIZED write with the trusted observation removed reaches `SUBSET_OBSERVATION_BINDING_HOLD`. Complete copied job fields from an attacker reach `SUBSET_CAPABILITY_DENIED`. |
| `G02_IMMUTABLE_IDENTITY_MUTATIONS` / `g02` (52) | `G02_job_<field>` (11), `batch_<field>` (16), `subset_admission_<field>` (13), `source_reservation_<field>` (7), each table's `_delete`, `heartbeat_preserves_binding` | Mutate every immutable persisted column, including UUIDs, manifest flags, source kind, result mode, mappings, fingerprints and timestamps. Reservation updates deny at `SUBSET_RESERVATION_MISMATCH_HOLD`; other updates and all deletes at `SUBSET_IMMUTABLE_HOLD`. A valid heartbeat preserves the full recovered binding. |
| `G04_GUC_FORGERY_NOT_AUTHORITY` / `g04` (5) | `G04_extra_source`, `duplicate_job_identity`, `foreign_job_identity`, `foreign_batch_identity`, `forged_worker_claim` | Copied complete rows plus forged request/admission/contract/worker GUCs do not confer authority. INSERT probes deny at `SUBSET_CAPABILITY_DENIED`, before uniqueness/FK checks; fake worker claim separately denies with SQLSTATE `42501`. |
| `G05_APPROVAL_FULL_CONTENT_VALIDATION` / `g05` (48) | `G05_canonical_0..6`, `numeric_domain_0..2`, `intake_<mutation>` (25), `plan_provenance_mismatch`, `persisted_<mutation>` (12) | Actual SQL/Python codec cross-vectors; independently rehashed invalid contracts; persisted malformed, hash-only, divergent JSON/bytes, plan-pin and revoked approval checks. Exact expected errors appear below. |
| `S08_OBSERVATION_TRUST_BINDING` / `s08` (29) | `S08_admission_<unknown/null/cross_approval>`; `duplicate_nonce_same_approval`; `collector_<missing_source/extra_source/duplicate_source/wrong_parent/trashed/future/expired>`; `untrusted_<collector_acl/update_acl/collector_guard/update_guard>`; `admission_scope_<contract_sha256/request_key/approval_id>`; `consumed_observation_new_request_replay`; `attempt_scope_<lease_token/attempt_count/job_id/worker_key/approval_id/contract_sha256/request_key/consumed_at>`; `attempt_missing_source`; `recovery_observation_independent` | Admission/attempt scope errors: `SUBSET_OBSERVATION_SCOPE`. Missing/extra collector source count: `SUBSET_SOURCE_SET_MISMATCH`; duplicate/parent/trash/native source errors: `SUBSET_SOURCE_REVISION_CHANGED`; bad clock: `SUBSET_OBSERVATION_EXPIRED`. Trust probes separate `42501`, `SUBSET_CAPABILITY_DENIED`, and `SUBSET_IMMUTABLE_HOLD`. Same-approval nonce duplicate requires `23505` and the exact `source_observation_approval_id_nonce_key` constraint. |
| `W02_LEASE_RETRY_BUDGET_AND_LIVENESS` / `w02` (39) | Five defects `null_token/stale_token/wrong_owner/expired_token/wrong_attempt_domain` × four operations `heartbeat/retry/finish/attempt` (20), plus recovery for each defect (5); `wrong_observation_attempt_<attempt/finish>` (2); `current_lease_without_observation_<heartbeat/retry>` (2); `reclaim_second_attempt`, `claim_third_attempt`, `raw_max4_acl`, `raw_max4_guard`, `attempt_counter_reset`, `third_retry_terminal`; `managed_exhausted_<QUEUED/LEASED>`, `historical_exhausted_<QUEUED/LEASED>` | Full lease defect cross-product must deny at `SUBSET_LEASE_LOST`; observation count mismatch at `SUBSET_OBSERVATION_SCOPE`. Raw max4 first denies with `42501`, then reaches `SUBSET_ATTEMPT_OR_RELEASE_HOLD` after a test-only EXECUTE grant. Counter reset uses a capable runtime LOGIN and reaches the same guard. Same-job claims rotate tokens/count 1→2→3. Third retry fails the canary without releasing reservations; no fourth claim. |

All negative probes use `h.variant(..., unchanged=True)` **after** any privileged
fixture seeding. The variant manager compares all seven persisted tables, including
observation consumption, and records SQL statement indices and canonical state hashes.
Exact guard message + SQLSTATE `P0001` are required; another guard, `42702`, `23514`,
generic permission denial or any unexpected success fails that variant. ACL probes
explicitly require `42501` and are not substitutes for structural guard probes.

`grant_guard_probe` verifies a genuine session LOGIN lacking superuser, BYPASSRLS,
or CREATEROLE, and grants only scoped table DML to an already appropriate capability
principal. It never adds a capability, owner role, or SECURITY DEFINER membership.
Privileged fixture corruption disables triggers only within the existing fixture
helper's bounded setup transaction, with guards restored before the tested action.
The schema has no job `admission_id` column: actual linkage is job `batch_id` → batch
`request_key` → persisted admission, checked together with source reservation ownership.

## G05 content and digest semantics

`rehash_approval` recalculates every present affected digest layer: native revision
fingerprints when requested, subset digest, queue inventory digest, deterministic
request key, queue manifest digest, and outer contract digest. It preserves the
intended invalid semantic value. Probe values are independently checked by the
[offline variant tests](../../../tests/test_universal_video_admission_variants.py).

| Mutation labels | Exact expected SQL message |
| --- | --- |
| `missing_files`, `missing_enqueue_manifest`, `missing_plan_sha256`, `flag`, `invalid_plan_anchor`, `scalar_plan_sha256` | `SUBSET_CONTRACT_INVALID` |
| `manifest_name`, `manifest_checksum`, `manifest_order`, `manifest_sequence`, `source_removed` | `SUBSET_QUEUE_PROJECTION_INVALID` |
| `source_fingerprint` | `SUBSET_FINGERPRINT_INVALID` |
| `source_duplicate`, `scalar_files_0_parent/version/modifiedTime/name/mime_type` | `SUBSET_SOURCE_INVALID` |
| `source_added`, `wrong_canary`, `scalar_enqueue_manifest_output_folder_id/work_folder_id` | `SUBSET_MANIFEST_INVALID` |
| `cost_fraction`, `cost_float_integer`, standalone `1.5`, `1.0`, `1e-1` | `SUBSET_CANONICAL_INTEGER_DOMAIN` |
| `cost_exponent` (`1e30`, whose PostgreSQL numeric value becomes an integer spelling) | `SUBSET_CANONICAL_BYTES_MISMATCH` |
| `plan_provenance_mismatch` (valid alternative SHA with independently original provenance) | `SUBSET_APPROVAL_INVALID` |
| Persisted `missing_files`, `flag`, `manifest_name`, `manifest_checksum`, `manifest_order`, `source_removed`, `source_added`, `invalid_plan_anchor` | Same semantic message as intake; persisted input and passed digests are synchronized first. |
| Persisted `hash_only`, `json_bytes_disagree`, `plan_pin`, `revoked` | Respectively `SUBSET_CONTRACT_INVALID`, `SUBSET_DIGEST_MISMATCH_HOLD`, `SUBSET_APPROVAL_CONTENT_HOLD`, `SUBSET_APPROVAL_NOT_ACTIVE` |

Persisted plan-pin corruption may be detected by full binding reconstruction after
tentative enqueue. The required invariant is complete transaction rollback and
`NOT_FOUND` recovery; the harness does not claim every corruption is caught before
the first internal write. A syntactically valid changed plan SHA alone is not an
invalid schema and is not treated as an independently pinned approval.

## API boundaries preserved by the tests

These limits follow the actual [0060 SQL functions](../../migrations/0060_fixed_subset_admission.sql):

- Nonce uniqueness is `(approval_id, nonce)`. The negative replay test reuses a
  consumed observation row for another request. It does not invent a global nonce ban.
- Heartbeat/retry accept job ID, lease token and owner, with no observation or caller
  attempt-count argument. Both reject a persisted attempt count of four. Wrong
  in-range observation attempt count applies to authorize/finish; current valid
  heartbeat/retry succeed without consuming that mismatched observation.
- Finish lease denials are seeded with distinct committed pre-work and fresh
  post-work evidence so missing pre-work cannot mask the intended lease/scope error.
- Managed exhausted QUEUED or expired LEASED attempt-three rows must become FAILED
  with `UV_WORKER_CRASH_RETRY_EXHAUSTED` while later eligible work is claimed.
  Pre-cutover historical rows are skipped unchanged; they are not adopted or forced
  through the managed attempt cap. Both schedules prove progress by an actual claim.
- Freshness is measured at final locked authorization, not guaranteed at every
  COMMIT after deferred constraints are forced immediate. This repair changes no
  SQL contract or freshness promise.

The offline tests validate the helper arguments (including literal NULL tokens),
rehash layers, semantic mutations, immutable column coverage against migration DDL,
required IDs and negative snapshot placement. They load selected pure AST functions
instead of importing the SQL runner, driver, or networking modules. Real SQL results
remain absent until a separately authorized invocation records them.
