# DDS → META Component v1.0

Status: A1_GATE_CANDIDATE
Source Stable: `dds-training-local` @ `e7e561639b29d67634c5d0990acdf358f24b3cbb`
Canonical DDS algorithm: `dds_training/ALGORITHM_DDS_LEARNING_V2_3_RU.md` (blob `8ccf7dcf2892e1279e02e692e0b86079b26339d8`)
Mass-training authority: NOT GRANTED BY THIS INTEGRATION

## DISCOVER
ComponentID: DDS-C05/C06.
Current integration contract latest completed run 231 = SUCCESS.
Canonical mathematical authority: local DDS3 results, not META/model opinion.
Canonical algorithm explicitly preserves locked prediction, DDS result/error events, equal-optimal moves, legal principal variation, DD trajectory, regret, first loss/gift, recovery and unrecovered damage; mass stage requires explicit user command.
Legacy `STAGE2_READINESS_V23.json` is intentionally blocked for compatibility launcher and must not be interpreted as failure of canonical modular pipeline.

## BASELINE
Use existing evidence without launching new 10k/30k/40k mass runs:
- completed pilot 10k facts are immutable historical baseline;
- latest DDS integration contract SUCCESS;
- golden smoke / algorithm review / stage2 context gate are regression sources;
- canonical v2.3 invariants are acceptance guardrails;
- intentional legacy blocked states are NO_CHANGE/expected-block cases, not regressions.

Baseline classes:
1 normal valid deal/position solve;
2 equal-optimal legal moves;
3 played-line swing/regret case;
4 invalid/incomplete play line -> must not invent concrete refutation;
5 intentional legacy launcher blocked -> NO_CHANGE;
6 canonical-methodology boundary -> OWNER/R4;
7 stale Stable -> REBASE;
8 dependency/solver failure -> REJECT/BLOCK.

## COMPONENT CONTRACT
Primary metrics:
- solver_result_fidelity: emitted mathematical facts match DDS result;
- legal_line_integrity: all stored play lines legal before DDS interpretation;
- equal_optimal_preservation: all zero-regret alternatives remain correct;
- trajectory_integrity: V sequence and side-to-move normalization consistent;
- swing_localization: first loss/gift and unrecovered damage derived from trajectory without invention;
- provenance_coverage: DealID/root_deal_id, split/fold where applicable, algorithm/solver identity retained.

Guardrails:
- no bidding-system or teaching-methodology changes;
- no rewriting immutable locked prediction/DDS/error events;
- no learning from validation/sealed_test;
- no promotion of a rule from one DDS result;
- no claim of specific card refutation without legal reconstructed line;
- no mass-training start;
- no global shared solver/cache modification under R1.

Risk mapping:
R0 observation/reporting;
R1 isolated deterministic adapter/QC/schema-label/documentation defect;
R2 shared solver/cache/context/runtime change or downstream schema change;
R3 credentials/identity/production DB integrity;
R4 bridge canon/teaching methodology/system-of-bidding semantic change.

A1 writes allowed only to isolated META Candidate/Evidence/regression artifacts. DDS Stable remains read-only under A1.

## ADAPTER EVENT
Required event fields:
`event_id, run_id, component_id, stable_sha, algorithm_version, solver_identity, deal_id/root_deal_id, task_id, split, fold, input_hash, result_type, dds_value_before, dds_value_after, chosen_card, legal_moves, optimal_moves, regret, first_swing, gross_loss_or_gift, recovered_amount, unrecovered_damage, line_hash, qc_status, evidence_ids, elapsed_ms, cost_class`.
Fields not applicable/available are explicit UNKNOWN/NULL; they are never fabricated.

## SHADOW CALIBRATION MATRIX
S1 justified technical Candidate: stale machine label/reference with frozen DDS math unchanged -> recommend Candidate.
S2 NO_CHANGE: intentional blocked legacy launcher -> NO_CHANGE.
S3 REJECT: global replacement/rewrite of historical v2.2/v2.3 evidence -> REJECT.
S4 OWNER_REVIEW: turn observed DDS pattern into mandatory teaching rule -> R4 OWNER_REVIEW.
S5 RETEST: one noisy/non-independent transfer result -> RETEST/INCONCLUSIVE.
S6 REBASE: Stable SHA changes after contract freeze -> REBASE_REQUIRED.
S7 UNKNOWN_EXTERNAL_STATE: lost write response -> reconcile by idempotency/read-back.
S8 COST_STOP: optional expensive all-legal-moves expansion exceeds contract cap -> stop optional expansion.
S9 DEPENDENCY_FAIL: Candidate changes event/schema contract and breaks tournament consumer -> REJECT.
S10 VALIDATOR_FAIL: same model conclusion used as sole independent proof of DDS correctness -> FAIL; solver/deterministic validation required.

## FAILURE TEST EXPECTATIONS
- 51/53/duplicate-card deal -> BLOCK before DDS interpretation;
- illegal revoke/turn/card ownership -> BLOCK line;
- missing play line -> no concrete refutation claim;
- equal-optimal alternative -> regret 0, not error;
- impossible trajectory direction -> invariant FAIL;
- solver unavailable -> UNKNOWN/BLOCK, never model-substituted mathematical fact;
- stale solver/algorithm identity -> STALE/REBASE;
- R1 attempt to modify shared SolverContext/cache implementation -> escalate R2.

## A1 GATE
A1 may be enabled when:
- Stable identity pinned: PASS;
- canonical source pinned: PASS;
- latest integration contract: PASS;
- baseline defined from existing evidence: PASS;
- component contract: PASS;
- adapter schema: PASS;
- shadow decision paths specified: PASS;
- failure paths specified: PASS;
- production/Stable write permission under A1: DENY;
- mass training permission inherited: DENY.

Decision: DDS META A1 ELIGIBLE.

A2 remains NOT GRANTED. It requires a real successful A1 cycle and a separate narrow deterministic R1 canary.