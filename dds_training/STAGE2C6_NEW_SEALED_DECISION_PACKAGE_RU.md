# DDS Stage 2C.6 — new sealed decision package

Date: 2026-08-30  
Tracker: #941  
Verdict: **BLOCKED**  
Authority: **PREPARATION_ONLY / SEALED_CLOSED**

## Decision

Stage 2C.6 is not ready for an owner decision authorizing a new sealed execution.

The candidate, TRAIN and validation evidence are internally consistent, but the tracked repository does not contain a complete machine-readable family-identity exclusion manifest. Counts, zero-overlap assertions and a task-set digest do not prove that a proposed new sealed wave excludes the exact 650 Stage 2C.4 sealed source families already opened.

No DDS, TRAIN, validation or sealed execution is authorized by this package.

## Verified candidate identity

The fixed Stage 2C.6 policy is:

`defense_suit_learned__defense_nt_baseline__declarer_baseline`

Meaning:

- learned selection is enabled only for defense in suit contracts;
- NT defense remains baseline;
- all declarer positions remain baseline.

Tracked evidence:

| Evidence | Identity |
|---|---|
| Stage 2C.6 TRAIN locked predictions | `84c3c6e78fd0a4d1a4699c34fc6cb92c5dd97b99496edffc91bc9bf4cc28a92e` |
| Stage 2C.6 validation locked predictions | `6b2b4abbc13e6d70930441f9e7162f526f2caaf2f4d2e290499d4fb679ffe7d5` |
| Stage 2C.6 validation artifact | `sha256:fa53eb64d11539fde49948e3fbe2852fd244866845fb224562edcad650170a6c` |
| Stage 2C.6 validation inner archive | `3aade4653a6357ed228fa8960b6760993c6fffd47c5ec7a4cd687df626b40b1f` |
| Stage 2B source artifact | `sha256:efdd62ab9a2643f231d3faea5c9475fe519cbd5b2e94dc9a764ca6087c043604` |
| Stage 2C.2 source artifact | `sha256:1fd61be6816df747824467266167dc22e6a88ae4a62794643f14fd176e566da2` |
| Main TRAIN logical archive | `c46cda2fb127371cbc1ab836c74580b25f0a0bb8ab358dfaab97cf591dd8a246` |

Stage 2C.6 TRAIN reports 644 evaluated families, locked-before-DDS predictions, zero overlap with fit/prior-shadow families, and no validation or sealed access.

Stage 2C.6 validation reports 645 evaluated families from a newly selected 650-family source wave, zero overlap with the two prior 650-family validation waves, locked-before-DDS predictions, no sealed access, and PASS on all eight predeclared conditions.

## Existing sealed evidence

The immutable corpus records:

- 3,000 sealed source families;
- 6,000 sealed tasks;
- sealed task-set digest  
  `dd98b5e146d5b78fe93a21249ce26452a0d8cbbf0b0add4108cc7e2c1a091dcb`;
- Stage 2C.4 opened a deterministically selected 650-family sealed source wave;
- 646 families contributed to the balanced 2,000-position Stage 2C.4 sealed evaluation;
- Stage 2C.4 sealed locked-prediction SHA-256  
  `49afd9f7bc5d1905f13fef0f0ef61d99ff6a32ceac5c9646b202dc636872c484`;
- the Stage 2C.4 sealed gate failed;
- the old Stage 2C.4 sealed wave is not reusable.

## Blocking evidence gaps

The repository does not provide all of the following as a single independently checkable manifest:

1. stable family IDs for all 3,000 sealed source families;
2. the exact 650 family IDs selected for Stage 2C.4 sealed;
3. the exact remaining eligible sealed family IDs;
4. deterministic proposed selection parameters for a new wave;
5. a hash of the proposed family-ID list computed before any labels or DDS outcomes are opened;
6. a formal set-difference proof against every TRAIN, validation and prior sealed family set;
7. an independent I2 verification of that set-difference proof.

The existing `sealed_task_id_digest` proves the identity of the complete 6,000-task pool, not the identity of a new unused subset. The recorded pairwise zero-overlap assertions apply to the historical split construction and do not prove non-reuse of the already opened Stage 2C.4 sealed subset.

## Required unblock package

A future preparation-only change may move this verdict to `READY_FOR_OWNER_DECISION` only if it adds:

- normalized family identifiers with a documented canonicalization rule;
- sorted manifests for all excluded TRAIN, validation and Stage 2C.4 sealed families;
- a sorted candidate manifest for the proposed new sealed wave;
- SHA-256 for every manifest;
- deterministic selection seed/rule fixed before DDS;
- executable set-disjointness verification;
- I2 evidence from a different checker/algorithm;
- proof that the checker reads identities and metadata only, not sealed labels or DDS results;
- a predeclared gate and fail-closed artifact contract.

## Predeclared execution boundary

Even after the manifest gap is resolved, this decision package remains non-executing. A later sealed run requires a separate explicit owner authorization after a fresh primary-source check.

Any later execution must preserve:

- exact Stage 2C.6 candidate identity;
- predictions locked and hashed before DDS;
- real DDS3 with `fallback_used=false`;
- no learning, tuning or historical database mutation;
- no old sealed-family reuse;
- immutable artifact plus durable verified copy;
- no automatic promotion;
- fail closed on any missing or inconsistent provenance.

## Safety statement

This review did not run DDS, TRAIN, validation, sealed evaluation or Oracle compute. It did not open sealed labels/results, change routing, mutate a database, or promote a model, canon rule or student-facing behavior.
