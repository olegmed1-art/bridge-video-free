# Video 3.1 FREE — research learning-candidate contract

Date: 2026-08-30  
Scope: subject handoff from Video 3.1 to Evolutionary Course  
Verdict: `IMPLEMENTED_NOT_PROVEN / REVIEW_REQUIRED`

## Audited gap

The existing `evolutionary_course.video31_adapter` checks transcript IDs and
frame hashes against separate source inventories. It permits an episode with
no frame evidence and does not carry the source-video SHA-256 or algorithm
revision. Therefore it cannot, by itself, reproduce the relationship
`source video -> transcript phrase -> one frame -> learning episode`.

## Added fail-closed boundary

`bridge_contracts.video_learning_candidate` validates a research-only handoff.
An accepted candidate requires:

- exact source file identity, source SHA-256 and source fingerprint;
- a bounded, completed observed interaction with supported actor attribution;
- hashed transcript segments contained in the interaction interval;
- exactly one source-bound frame per transcript segment using the
  `bridge-speech-frame-binding-v1` receipt;
- matching transcript and binding intervals and a reproducible midpoint
  distance;
- explicit `CONFIRMED`, `REVIEW`, or `UNKNOWN` status for board, dealer,
  vulnerability, auction and deal;
- evidence references for every confirmed bridge value;
- no value or evidence smuggled into an `UNKNOWN` bridge field;
- separate confidence values for transcript, frame, actor attribution, bridge
  context and preliminary skill;
- algorithm revision and contract version;
- unresolved questions whenever any bridge-context field is not confirmed;
- `CANDIDATE_RESEARCH` authority with Canon, Student Profile, approved-course
  and publication writes all denied.

The module validates and hashes payloads only. It has no database, filesystem,
publication, activation or promotion writer.

## Verification

Command:

```text
python3 -m pytest -q tests/test_bridge_video_learning_candidate.py \
  tests/test_bridge_video_positions_adapter.py tests/test_bridge_vision_*.py \
  tests/test_evolutionary_course_*.py tests/test_video_algorithm_3_1_test.py
```

Result: `184 passed`.

Additional checks: Python bytecode compilation and `git diff --check` passed.

## Evidence boundary

Only synthetic contract fixtures were used. No Diana media was run and no
real learning episode was accepted. This change is not proof of Diana 13
quality, holdout quality, production parity, or production readiness.
