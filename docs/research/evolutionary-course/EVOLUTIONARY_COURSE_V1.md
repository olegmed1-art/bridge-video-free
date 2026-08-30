# Evolutionary Course v1

Status: **RESEARCH CANDIDATE / NOT ACTIVE CURRICULUM**

Tracker: [#875](https://github.com/olegmed1-art/bridge-video-free/issues/875)

## Purpose

Evolutionary Course v1 turns evidence from the five-year Diana lesson archive into
a longitudinal model of learning.  Its atomic unit is a completed learning
interaction:

`learning task -> learner action -> feedback -> retry/outcome -> candidate mastery transition`

The first slice is intentionally narrow.  It validates evidence-bound learning
episodes and builds deterministic per-skill trajectories.  It does not process
media, recognize cards, run DDS3, change a student profile, publish content, or
activate School Canon.

## Input boundary

An episode may consume only exact, reviewable references from an already produced
lesson result:

- immutable Drive file identity and source name;
- bounded video start/end;
- one or more transcript segment identifiers;
- zero or more SHA-256 frame identities;
- `OBSERVED` or `VERIFIED` evidence state.

Claims cannot reference evidence outside that exact source envelope.

## Episode model

Every episode records:

- learning task and prerequisite skill identifiers;
- teacher actions and learner actions;
- outcome and degree of support;
- FACT / INFERENCE / RECOMMENDATION / UNCERTAIN claims;
- a candidate transition between skill states;
- review and authority state.

Skill states are:

`NOT_INTRODUCED -> INTRODUCED -> RECOGNIZED -> SUPPORTED -> INDEPENDENT -> TRANSFERRED -> MASTERED`

`UNSTABLE` is explicit and may occur after any previously observed state.  The
contract does not force progress: it records a candidate transition only when
FACT or INFERENCE evidence supports it.

## Authority boundary

Every accepted episode is permanently created as `CANDIDATE_RESEARCH` with:

- `canonical_promotion_allowed=false`;
- `curriculum_activation_allowed=false`;
- `student_profile_write_allowed=false`;
- `publication_allowed=false`.

Human review may approve an episode as a research candidate.  Approval does not
activate it.  A future, separately authorized governance path must select and
promote course material.

## Relationship to Video 3.1

Video 3.1 remains the evidence producer.  Evolutionary Course v1 is downstream:

`Video 3.1 evidence -> learning episode candidates -> longitudinal candidate trajectory -> methodology review`

Technical completion does not imply pedagogical correctness.  Unavailable card
recognition does not block speech-based episodes, but a card-specific claim must
carry its exact verified frame evidence.

## Validation

Run:

```bash
python -m pytest -q tests/test_evolutionary_course_v1.py
```

The tests cover canonicalization, source binding, authority escalation, claim
classification, mastery evidence, ordering, duplicate identities, and
discontinuous longitudinal histories.

## Next bounded milestone

After this contract passes CI:

1. map existing Video 3.1 output fields to this contract without changing Video 3.1;
2. create 3-5 private review fixtures from already processed Diana lessons;
3. compare extracted episodes with a human lesson review;
4. adjust only the extraction layer and retain this authority boundary;
5. propose the first skill dependency graph for methodology review.

No batch of 254 videos should be launched by this milestone.
