# Video evidence to School Canon — v1 contract

Status: `IMPLEMENTED / NOT ACTIVATED`  
Governance mode: `ASSURED`  
Tracker: #609; upstream video runtime: #881

## Boundary

Video analysis is a high-value evidence source, not an authority shortcut.
Artifacts move through:

`RAW_VIDEO -> TRANSCRIPT -> OBSERVATION -> TEACHER_ASSERTION -> RULE_CANDIDATE`

The adapter stops at `public.analysis_candidate`-compatible staging. It has no
database writer and cannot insert or activate `bidding.rule`.

## Review eligibility

A video-derived rule candidate is eligible for Canon review only when:

- the source is classified `SCHOOL_PRIMARY_EVIDENCE`;
- a Director decision explicitly approves the exact semantic scope;
- the teacher identity is verified for every cited transcript segment;
- the assertion refers only to transcript/frame evidence bound to the same
  immutable source video;
- normalized rule and tests contain no hidden-hand fields;
- positive, negative, boundary and interference tests all exist;
- no ambiguity or contradiction remains.

`TEACHING_CONTEXT`, `WORLD_EXTERNAL`, unapproved sources, ambiguous statements
and conflicts remain `EVIDENCE_ONLY`. They may help review or identify a gap,
but cannot enter the Canon review lane.

## Activation remains separate

Even `ELIGIBLE` means only eligible for human/independent review. Canon still
requires explicit approval plus regression, integrity, rollback proof and I2.
The existing runtime invariant remains unchanged: `CANON_CONFLICT` stops and
does not call WORLD; only a recorded `CANON_GAP` permits the WORLD lookup.

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
