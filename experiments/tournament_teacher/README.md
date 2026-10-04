# Tournament teacher: isolated executable experiment

Change ID: TOUR-TEACHER-TEST-20261004. Governance: ASSURED, bounded test evidence.
Baseline main: `1440920191e1778fb9a9ba24e6701937a1a7459c`.
Purpose: execute the confirmed scope of all 14 October teacher-decision records
through one isolated teacher consumer, with hands, full auctions, explanations
and provenance. Version 2 contains 26 rule/companion entries. Decision record
20261002-011 is partial and is executed with its completion 20261003-001.

## Run

```sh
python -m experiments.tournament_teacher --test-only < request.json
python -m experiments.tournament_teacher.demo
python -m experiments.tournament_teacher.coverage > tournament-teacher-evidence.json
python -m pytest -q experiments/tournament_teacher
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

`SUPPORTED` means the selected call satisfies the bounded fragment's confirmed
conditions. It does not establish the best or unique bid. At 8 synthetic points
with a four-card major, both Stayman and direct 2NT can meet stated conditions;
`recommend` abstains because no selection priority was supplied. Even one
matching meaning cannot create a choice policy. The choices implemented are the
explicitly confirmed opening 1M and subsequent rebid 2NT for 5M332, 15-17.

## All 14 decision records

[Machine-readable mapping](decision_mapping.json) links each record to executable
rules, limits and scenarios. `coverage` replays the hand-written cases through
`decide`, fails on mismatches and emits inputs, outcomes, explanations and source
links. CI retains that synthetic report as an artifact for seven days.

| Teacher decision | Implemented behavior and remaining limit |
| --- | --- |
| TDEC-20261002-001 | Both diamond reverses: own D>=5, exact four-card major, 18+, FG. Original club typo retained as source. |
| TDEC-20261002-002 | PASS conditions 12-14, NF; DRAFT retained. No automatic pass without shape/priority policy. |
| TDEC-20261002-003 | 2C/2D: own minor>=4, 11+, F1; separate 2H>=5 companion preserved. |
| TDEC-20261002-004 | Heart opener's 2S: S=4, H>=5, 18+, FG; no 5332 constraint. |
| TDEC-20261002-005 | 2S: 12-15, S>=6, NF; 3S: 16-17, S>=6, INV. |
| TDEC-20261002-006 | 2C: 12-15 with preserved 6C+ or 5C+4D, no four-card major; 3C: 16-17, C>=6, NF, continuation note preserved. |
| TDEC-20261002-007 | 1S after 1C-1H: 12-22, S=4, H<4, F1. |
| TDEC-20261002-008 | NT opening 5m332 correction; preserved 5m422/6m322; explicit 15-17 5M332 opening1M/rebid2NT. Other opening priorities unknown. |
| TDEC-20261002-009 | 2NT: 20-22, 5m332 and preserved 5m422/6m322. Unlisted shapes abstain. |
| TDEC-20261002-010 | Both 3C continuations: H=4 AND C>=5, 13+, FG. |
| TDEC-20261002-011 | Partial list 3C/3D, linked to following decision; never used alone to invent suit lengths or values. |
| TDEC-20261003-001 | Weak2S responses: own minor>=5, 17+, FG; values phrase only in original source; 3D DRAFT retained. |
| TDEC-20261003-002 | 3H/3S singleton mapping, exact 5431, both minor orientations, FG. No numeric threshold. |
| TDEC-20261003-003 | Stayman 8+ and four-card major; direct2NT 8-9 INV. No overlap priority. |

Every mapped record has positive, negative, boundary and hidden-information
scenarios. Record 002-011 shares the completed semantics' cases; no independent
numeric boundary is attributed to this partial call-list correction. For
003-002, boundary tests concern exact suit lengths, since no points threshold
was supplied. The two preserved companion rows retain their source-row HCP text
separately from the executable synthetic-school-point premises.

## Evidence and boundaries

The minimal nonpersonal subset was checked on 2026-10-04 against the supplied
[rules sheet](https://docs.google.com/spreadsheets/d/1LgPnxsbQ8gp_Hy2HIESKo9hquwpPC4OzEkdnEHUH3eo/edit#gid=300003)
and [teacher decisions](https://docs.google.com/spreadsheets/d/1LgPnxsbQ8gp_Hy2HIESKo9hquwpPC4OzEkdnEHUH3eo/edit#gid=300004).
`rules.json` retains IDs, exact small original excerpts, normalized meanings,
decision links and source status. It is not a dump of the private registry.
Teacher excerpt fields combine short literal fragments; they are not complete
transcripts. Source document: SRC-0096, PDF pages 1-2, corresponding auction blocks.

- Supported partnership histories: empty opening, 1NT, 1S, 2S, 1D-1NT,
  1H-1NT, 1S-1NT, 1C-1H, 1D-1H-1S and 1D-1H-1NT. Every actual opponent
  PASS must be present. Prior calls are supplied context; the consumer does not
  validate all previous bids against the actors' original hands or full canon.
- Exactly 13 unique own cards, actor/dealer and explicit profile are required.
  Other hands, unknown keys, wrong turns and omitted opponent calls abstain.
- Stayman accepts literal four-card majors only; five-card-major agreements
  abstain. No invented shape restriction is attached to direct 2NT.
- No numeric threshold is added to 3H/3S. No forcing is invented for 2C or
  either major's 2NT rebid. No opening-NT shape list is reconstructed.
- DRAFT PASS and 3D receive conditional assessments only; their statuses are
  unchanged and neither is recommended. Deferred questions 13/14 are unsupported.
- Weak2S response cases require `opening_meaning: "WEAK_2S"`; missing meaning
  gives `WEAK_2S_CONTEXT_REQUIRED`. Missing numeric premise gives
  `SYNTHETIC_SCHOOL_POINTS_REQUIRED`. Unknown NT shapes, including 4333/4432,
  give `NT_SHAPE_NOT_ESTABLISHED_NONEXHAUSTIVE_SOURCE`, not a false rejection.
  These reasons are returned in `abstain_reasons` and each affected rule check.
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

The expansion's [independent review](REVIEW.md) also checks the semantic rules
and synthetic hands. Its separate executable [choice-policy oracle](independent_review.py)
exercises 67,200 decisions (560 shapes, 12 point premises, 10 auctions) and
2,574 malformed requests. CI runs this checker at the same exact SHA. This
bounded independent algorithm tests recommendation isolation, not all source
truth or the completeness of bridge strategy. Source-reading limitations and
the two corrected companion-provenance findings are recorded in the review.

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
