# Video evidence to School Canon — v2 contract

Status: `AI AUTO-PROMOTION IMPLEMENTED / PRODUCTION NOT ACTIVATED`
Governance mode: `ASSURED`  
Tracker: #609; upstream video runtime: #881

## Boundary

Authorized teacher video is a canonical learning source after the AI gate.
Artifacts move through:

`RAW_VIDEO -> TRANSCRIPT -> OBSERVATION -> TEACHER_ASSERTION -> RULE_CANDIDATE -> AI_VERIFIED -> ACTIVE`

The evidence adapter stops at `public.analysis_candidate`-compatible staging.
The separate promotion gate seals an idempotent activation command only after
all required checks pass. Per-rule human approval is not required.

## AI verification eligibility

A video-derived rule candidate enters AI verification only when:

- the source is classified `SCHOOL_PRIMARY_EVIDENCE`;
- source policy explicitly binds the exact video SHA-256, Drive file identity,
  trusted teacher and semantic scope;
- the teacher identity is verified for every cited transcript segment;
- the assertion text SHA-256 exactly matches its single transcript span;
- the rule includes source-backed why/purpose and consequences;
- normalized rule and tests contain no hidden-hand fields;
- positive, negative, boundary and interference tests all exist;
- no ambiguity or contradiction remains.

`TEACHING_CONTEXT`, `WORLD_EXTERNAL`, unapproved sources, low-confidence or
ambiguous statements and conflicts remain `EVIDENCE_ONLY`. They may identify a
gap, but cannot enter automatic Canon promotion.

## Automatic activation gate

`AI_VERIFICATION_PENDING` becomes `AUTO_PROMOTION_READY` only after all 16
checks in `video-canon-ai-promotion-v1` pass. Semantic parsing and bridge-logic
verification must be I2/I3 and come from different verifier families. The
hidden-information firewall must also be I2/I3. Regression, integrity,
conflict scan and a tested restore path are mandatory.

The activation command binds both candidate SHA-256 and verification-bundle
SHA-256 and is idempotent. The existing runtime invariant remains unchanged:
`CANON_CONFLICT` stops and does not call WORLD; only a recorded `CANON_GAP`
permits the WORLD lookup.

Implementation boundaries:

- `video_canon_evidence.py` seals exact source, speech, logic and tests;
- `video_canon_ai_promotion.py` evaluates the 16-check bundle;
- `video_canon_auto_pipeline.py` produces promotion commands or explicit gaps;
- migration `0322_video_canon_ai_promotion.sql` separates verifier and promoter
  roles and performs the atomic database activation;
- the Diana v4.2 quality layer invokes the pipeline when a complete
  `video_canon_*` input bundle is present.

## Explanation is part of knowledge

A teachable rule needs a source-bound explanation candidate containing the
reasoning chain, prerequisites, rejected alternatives, an example and a
counterexample. The analyzer must preserve the teacher's reason rather than
generate a plausible replacement. A rule observation without such evidence
creates an explicit `EXPLANATION_MISSING` gap. It may still be reviewed as a
rule candidate, but it is not a complete teachable knowledge unit.

The analyzer now extracts an explanation candidate directly when the same
source-bound transcript segment is attributed to the teacher with confidence
at least 0.8 and contains an explicit causal marker such as “потому что”,
“поэтому” or “так как”. The exact speech, speaker evidence and timestamps are
preserved. This first pass intentionally records a partial explanation rather
than completing missing premises, alternatives or examples with generated text.

Explanation covers both cause and purpose. Explicit links are stored as typed
relations: `CAUSE` (why), `PURPOSE` (what for), `CONSEQUENCE`, and
`ALTERNATIVE_CONSEQUENCE`. Each relation retains the exact left and right
clauses around the teacher's connective. The target teachable logic is:

`conditions -> cause/purpose -> conclusion -> action -> consequences -> rejected alternatives`

Missing links remain explicit completeness gaps; plausible model-generated
links are not evidence.

## Offline DDS consequence comparison

When a full board is independently verified, the analyzer can stage a
`DDS_DECISION_COMPARISON` linking the player's source-bound logic and chosen
play/defense action to DDS3 alternatives. It stores a hash and source references
for the full deal, but never the deal or hidden hands in the student-visible
payload. DDS is explicitly `offline_only`: it measures consequences after the
fact and does not validate a bidding rule, become Canon evidence, or enter a
live resolver request.

## Learning feedback loop

Evidence-bound corrections for ASR, speaker, card, auction, extraction and
pedagogy are emitted as immutable versioned `ANALYZER_TRAINING_EXAMPLE`
records. Human corrections remain useful but are not a prerequisite for every
Canon rule. A candidate model is only represented by a
`MODEL_IMPROVEMENT_PROPOSAL` when a named holdout compares it with a baseline
and records a rollback model version. Model deployment remains a separate
gate; a model passing holdout is not itself permission to change Canon.
