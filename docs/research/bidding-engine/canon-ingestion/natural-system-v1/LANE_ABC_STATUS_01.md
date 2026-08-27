# Lane A/B/C status 01

Status: WORKING CLASSIFICATION / NOT ACTIVE CANON

## Opening candidates (19)

### Lane A — structurally compilable/testable now (4)

- NSV1-OPEN-1H
- NSV1-OPEN-1S
- NSV1-OPEN-1NT
- NSV1-OPEN-2NT

Meaning of Lane A: source predicates are structurally representable using resolved notation. It does **not** mean activation-ready. All four still require the school point-count evaluator before card-level strength tests can PASS; 1H/1S/1NT also participate in unresolved opening-priority cases.

### Lane B — source-faithful candidate, semantic operator unresolved (13)

- NSV1-OPEN-1C — minor quality/partition
- NSV1-OPEN-1D — complete minor partition
- NSV1-OPEN-2C — generic balanced/strong suit/playing tricks
- NSV1-OPEN-2D, 2H, 2S — bare length + semi-preempt predicate
- NSV1-OPEN-3C, 3D, 3H, 3S — bare length + below-opening/preempt predicate
- NSV1-OPEN-3NT — stopper + AKQxxxx length semantics
- NSV1-OPEN-4C, 4D, 4H, 4S — bare length + below-opening/preempt predicate

Note: the list contains 15 IDs because the four 3-level and four 4-level suit calls are separate atomic actions; the opening candidate set totals 19, so Lane B count is 15 and Lane A count is 4.

### Lane C — conflict/gap overlays

Lane C is not mutually exclusive with A/B at the source-record level; it is a runtime conflict overlay on affected hands. Current opening overlays:

- 1NT vs 1H/1S on allowed NT shape with five-card major;
- equal five-card major priority gap;
- 1C/1D partition gap for patterns not explicitly disambiguated.

## First Lane A tests

`tests/OPENING_LANE_A_TEST_SPEC_01.json` now defines abstract positive, negative, boundary, conflict and hidden-information tests for 1H, 1S, 1NT and 2NT.

No card-level strength test is allowed to PASS until OP-007 is resolved. No rule is activated.