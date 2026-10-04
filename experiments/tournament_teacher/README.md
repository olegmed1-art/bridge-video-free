# Tournament teacher: isolated executable experiment

Change ID: TOUR-TEACHER-TEST-20261004. Governance: ASSURED, bounded test evidence.
Baseline main: `1440920191e1778fb9a9ba24e6701937a1a7459c`.
Purpose: demonstrate that confirmed source corrections can affect an actual
teacher answer, with a hand, full auction, explanation and provenance.

## Run

```sh
python -m experiments.tournament_teacher --test-only < request.json
python -m experiments.tournament_teacher.demo
python -m pytest -q experiments/tournament_teacher/test_consumer.py
```

The consumer and demo require only Python 3.12 standard library. Tests additionally
use pytest and the existing API dependencies to verify the unchanged production
profile gate. No services, credentials, student records or full deals are used.

Example `request.json` (all cards and the numeric premise are synthetic):

```json
{
  "mode": "TOURNAMENT_TEACHER_TEST_ONLY",
  "system_profile": "SCHOOL_TOURNAMENT_CURRENT_V1",
  "task": "recommend",
  "dealer": "N",
  "actor": "N",
  "auction": ["1H", "PASS", "1NT", "PASS"],
  "hand": ["S2", "S3", "S4", "H2", "H3", "H4", "H5", "H6", "D2", "D3", "D4", "C2", "C3"],
  "school_points": {"value": 16, "basis": "SYNTHETIC_SCHOOL_POINTS"}
}
```

Result: `RECOMMEND`, action `2NT`, linked to `TDEC-20261002-008` and
`RULE-TOUR-1H-1NT-REBID-2NT`. Forcing remains null. The synthetic value is an
assumption, deliberately unrelated to the honors in this hand. The school point
method is unresolved; the consumer never derives points from HCP. Omitting that
premise yields `ABSTAIN`. This is a conditional experiment, not a recommendation
for an actual player's hand with an established school evaluation.

For `assess_call`, include `proposed_call`. A 3=1=4=5 hand after `1NT,PASS`,
proposed `3H`, yields `SUPPORTED` and explains the heart singleton and both
minor orientations, with `TDEC-20261003-002`. Proposed `3S` is `CONTRADICTED`.
The baseline L1 evaluator blocks these tournament rule IDs as `UNKNOWN_RULE_ID`;
without explicit test opt-in this adapter also abstains. The demo emits both
before and after results, including complete explanations and source fragments.

`SUPPORTED` means the selected call satisfies the six-rule fragment's confirmed
conditions. It does not establish the best or unique bid. At 8 synthetic points
with a four-card major, both Stayman and direct 2NT can meet stated conditions;
`recommend` abstains because no selection priority was supplied. Even one
matching meaning cannot create a choice policy. The only choice implemented is
the explicitly confirmed 5M332, 15–17, 1M–1NT–2NT sequence.

## Evidence and boundaries

Six minimal nonpersonal rules were checked on 2026-10-04 against the supplied
[rules sheet](https://docs.google.com/spreadsheets/d/1LgPnxsbQ8gp_Hy2HIESKo9hquwpPC4OzEkdnEHUH3eo/edit#gid=300003)
and [teacher decisions](https://docs.google.com/spreadsheets/d/1LgPnxsbQ8gp_Hy2HIESKo9hquwpPC4OzEkdnEHUH3eo/edit#gid=300004).
`rules.json` retains IDs, exact small original excerpts, normalized meanings,
decision links and source status. It is not a dump of the private registry.
Teacher excerpt fields combine short literal fragments; they are not complete
transcripts. Source document: SRC-0096, PDF page 2, corresponding auction blocks.

- Only full uncontested dealer-opened auctions `1NT,PASS`,
  `1H,PASS,1NT,PASS`, and `1S,PASS,1NT,PASS` are supported.
- Exactly 13 unique own cards, actor/dealer and explicit profile are required.
  Other hands, unknown keys, wrong turns and omitted opponent calls abstain.
- Stayman accepts literal four-card majors only; five-card-major agreements
  abstain. No invented shape restriction is attached to direct 2NT.
- No numeric threshold is added to 3H/3S. No forcing is invented for 2C or
  either major's 2NT rebid. No opening-NT shape list is reconstructed.
- New DRAFT rules and unresolved questions 13/14 are unsupported and abstain.
- Existing source statuses are preserved, including `BLOCKED_SOURCE_CONFLICT`
  on both rebids and `DRIVE_CANON_ONLY`. A test recommendation does not clear
  these statuses, approve a version or perform runtime activation.
- No production route imports this module. L1 registry, evaluator, SQL gates,
  catalog reader, deployed services and HOLD remain unchanged.

Validation goes through `decide` and the executable CLI: positive, negative,
boundary, malformed input, ambiguity, provenance and disabled-mode cases.
An independent finite-language algorithm enumerates all 560 suit-length tuples
and compares singleton and major-rebid behavior to explicitly enumerated tuple
sets (2,240 end-to-end comparisons). This bounded I2 check covers shape behavior,
not the full canon or pedagogical benefit. The logically separate I1 Red Team
found an unjustified 40-point ceiling; it was removed and regression-tested.
It also prompted fail-closed handling of deeply nested malformed CLI JSON.
Existing L1 suites and the actual production catalog's tournament 404 are run.

## Publication and rollback

Only `test/tournament-teacher-canon-20261004` triggers the new isolated push
workflow: one standard GitHub-hosted job, ten-minute timeout, read-only contents,
no secrets, services, deployment, DB connection or external task dispatch.
`preflight.py` checks the changed-file allowlist, obvious secret/private-material
patterns and push/create/workflow_run side effects. No PR is required for this
experiment; opening one would trigger unrelated repository workflows.

Rollback: stop using the test branch or remove its new experiment and workflow.
No runtime state needs restoration. Production orchestration, point counting,
SQL activation, full auction support, alternative-bid policy, external bridge
engine agreement and learning effectiveness remain unproved and out of scope.
