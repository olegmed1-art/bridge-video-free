# Diana Longitudinal v1.1 — Link Audit and Optimization
Date: 2026-08-18

## Main structural finding
v1.0 described six streams well but risked implementation as six partially duplicated stores. v1.1 converts them to views over one typed provenance graph. This removes duplicate authority, synchronization drift and expensive full-corpus recomputation.

## Link audit
Video -> Evidence: PASS; SourceAsset/TranscriptRevision/EvidenceSpan are explicit.
Evidence -> Canon: PASS; Claim/CanonVersion separates observation from authority.
Canon -> Curriculum: PASS; version links prevent curriculum from pointing at mutable canon text.
Curriculum -> Student: PASS; Opportunity links topic/skill and preserves denominator.
Student -> Teacher: PASS; response and help are separate linked events, so helped-correct cannot become independent mastery.
Student -> Outcome: PASS; retention/generalization/transfer are linked observations rather than overwritten skill state.
Teacher -> Outcome: PASS with causality guard; intervention precedes outcome but effectiveness remains hypothesis.
DDS -> Student/Outcome: PASS; solver evidence is mathematical validation only, hidden-information guard retained.
Tournament -> Outcome: PASS; source/identity/trading/play evidence restrictions retained.
Canon -> Lesson generator: PASS; generator consumes immutable verified CanonVersion, not inferred video statement.
Student Model -> Lesson proposal: PASS; read-only prediction/projection and proposal-only teacher brief.
META -> all components: PASS; health/improvement layer does not inherit canon/profile authority.

## Errors/risks corrected
1. Duplicate six-stream storage risk -> one typed graph, six views.
2. Canon lifecycle ambiguity -> explicit authority ordering and immutable CanonVersion.
3. Single confidence field risk -> source/speaker/extraction/support/authority dimensions separated.
4. Full 250-video reprocessing risk -> text-first staged pipeline + selective multimodal escalation.
5. Algorithm-update cost explosion -> module/version-based incremental invalidation.
6. Chronology uncertainty hidden -> chronology confidence explicit.
7. Teacher intervention taxonomy too closed -> free-form fallback allowed.
8. Digital Twin global-accuracy trap -> calibration segmented by skill/topic/novelty/help.
9. Curriculum prerequisite correlation overclaim -> hypothesis lifecycle and cross-student validation.
10. Derived library contaminating evidence -> GeneratedExercise and derived products remain non-historical.
11. Canon text copied into downstream objects -> references to immutable CanonVersion preferred.
12. Correction/destructive rewrite risk -> append-only correction/version lineage.

## Performance optimization
Tier 1 metadata/transcript inventory is cheapest and runs over all assets.
Tier 2 text extraction handles transcript-sufficient segments.
Tier 3 multimodal review is sparse and reason-coded.
Derived projections recompute only impacted graph neighborhoods.
Unchanged source assets are not reprocessed unnecessarily.

## Remaining pre-bulk work
- inventory actual Diana Drive corpus;
- resolve chronology/duplicates;
- measure transcript coverage/quality and total duration;
- implement graph persistence/events in isolated lab;
- pilot on chronological sample spanning early/middle/recent lessons;
- measure extraction precision/cost/time before scaling.

## Verdict
LINK_ARCHITECTURE = PASS
AUTHORITY_BOUNDARIES = PASS
INCREMENTALITY = PASS
COST_ARCHITECTURE = IMPROVED
BULK_RUN = NOT STARTED
NEXT = real corpus inventory and pilot.