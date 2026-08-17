# Tournament Analysis → META Component v1.0

Status: A1_GATE_CANDIDATE
Canonical School document: Google Doc `1oBqxZReVovIdc6RvAem5TNEkaEjXx8AAQuFj-_ROYss`, «Единый алгоритм полного разбора турнира и оформления слайдов — v1.0».
Data-model evidence: tournament ingestion/result model commit `acad1ae90e45786fc507bd7163c11206239e05ac`.

## DISCOVER
The canonical School document is authoritative for tournament-analysis procedure and slide rules. It requires direct user command before starting an actual tournament analysis; META onboarding does not constitute such a command for any tournament.
Official tournament/session card, pair card, PBN/lin and published protocols are factual sources. School teaching/bidding canon comes only from supplied School materials. Original sources are not modified.
Database tournament facts are source-scoped; identity attribution requires explicit resolution; TableResult facts are append-only with correction lineage.

## BASELINE
Existing tournament 30041 material may serve as historical layout/output evidence, but onboarding does not re-analyze it.
Baseline classes:
1 complete official board facts;
2 missing auction -> no specific bidding-error attribution;
3 missing card-by-card play -> no specific mid-play error attribution;
4 average/not-played board -> no personal-error attribution;
5 source conflict -> disputed field UNKNOWN/BLOCKED until authoritative resolution;
6 layout geometry/template regression;
7 canonical-methodology proposal -> OWNER_REVIEW;
8 identity provenance missing -> BLOCK attribution.

## COMPONENT CONTRACT
Primary metrics:
- source_fidelity: tournament/session/board facts match authoritative source;
- deal_integrity: four 13-card hands / 52 unique cards when full deal exists;
- contract_consistency: contract/declarer/opening lead/result consistent with published facts;
- evidence_boundedness: claims do not exceed auction/play evidence;
- status_integrity: played/average/not-played/missing-data separated correctly;
- identity_provenance: student/pair attribution has explicit basis;
- layout_fidelity: approved slide/template geometry and user-specific layout rules preserved;
- dds_separation: DDS/machine facts distinguished from human interpretation and added to output only when authorized by current tournament instructions.

Guardrails:
- no invented auction agreements/ranges/methodology;
- no personal error from average/not-played board;
- no factual auction reconstruction presented as fact when auction absent;
- no specific card-play blame without card-by-card evidence;
- no mutation of original PBN/protocol/template/canonical materials;
- no silent identity matching by name alone;
- no actual tournament-analysis execution without direct user command;
- no Stable write under A1.

Risk mapping:
R0 read-only ingestion/QC/reporting;
R1 isolated deterministic formatting/QC/provenance-label defect;
R2 shared parser/template/schema/runtime change affecting other analyses;
R3 identity attribution, student-profile persistence, production DB integrity/permissions;
R4 bidding system, teaching methodology, canonical pedagogical interpretation.

## ADAPTER EVENT
Required fields:
`event_id, run_id, stable_id, tournament_id, session_id, board_id, source_ids, pair_identity_basis, board_status, deal_hash, dealer, vulnerability, contract, declarer, opening_lead, result, score, percentage_or_imps, auction_evidence_status, play_evidence_status, dds_evidence_ids, interpretation_status, layout_version, qc_status, evidence_ids`.
Unknown fields remain UNKNOWN/NULL and are never inferred into facts.

## SHADOW CALIBRATION
T1 justified Candidate: deterministic layout/QC label defect -> Candidate recommendation.
T2 NO_CHANGE: average/not-played board correctly excluded from personal error -> NO_CHANGE.
T3 REJECT: reconstruct missing auction and label it factual -> REJECT.
T4 OWNER_REVIEW: introduce a new bidding agreement/mandatory teaching rule -> R4.
T5 RETEST: source conflict or incomplete official evidence -> RETEST/BLOCK disputed field.
T6 REBASE: canonical algorithm/template revision changes after freeze -> REBASE_REQUIRED.
T7 UNKNOWN_EXTERNAL_STATE: lost write response for isolated Evidence -> reconcile/read-back.
T8 COST_STOP: optional external enrichment exceeds cap -> stop enrichment.
T9 DEPENDENCY_FAIL: template/schema Candidate breaks slide/PDF consumer -> REJECT.
T10 VALIDATOR_FAIL: model interpretation used as sole proof of tournament fact -> FAIL; official source/DDS/deterministic check required.

## FAILURE TESTS
- duplicate/missing card in full deal -> deal QC FAIL;
- contract declarer inconsistent with auction when auction exists -> FAIL;
- opening lead not in LHO hand when full deal exists -> FAIL;
- missing auction + specific bidding blame -> BLOCK claim;
- missing play + specific mid-play blame -> BLOCK claim;
- average/not-played + personal error -> BLOCK claim;
- identity basis missing -> BLOCK attribution;
- original source mutation attempt -> DENY;
- A1 Stable write -> DENY;
- current tournament analysis without direct user command -> DENY.

## A1 GATE
Canonical procedure pinned: PASS.
Tournament immutable/source-scoped data model exists: PASS.
Baseline classes defined: PASS.
Component metrics/guardrails: PASS.
Adapter schema defined: PASS.
Shadow/failure matrices: PASS.
Stable/source mutation: DENY.
Actual tournament execution without direct command: DENY.
Decision: TOURNAMENT META A1 ELIGIBLE.
A2 NOT GRANTED until a real successful A1 improvement cycle and narrow deterministic R1 canary.