# Test plan - Wave 01

Scope:

- `NSV1-P1-R1-C1` - openings;
- `NSV1-P1-R1-C2` - first responses to 1♣.

Status: **DESIGNED / NOT EXECUTED / NO CANON ACTIVATION**

## Principles

1. Every activated rule requires at least one latest PASS for `positive`, `negative`, `boundary` and `hidden_information` tests.
2. Additional enabled conflict/interference/regression tests must also have latest PASS.
3. Test fixtures use only the acting hand and public auction state.
4. A source ambiguity is tested as an expected `gap` or `conflict`; it is never resolved by test code.
5. Test labels distinguish source facts from unapproved operator assumptions.

## Feature-vector test format

Until a 13-card hand parser and the school point evaluator are frozen, source tests are expressed as deterministic feature vectors:

```json
{
  "auction": [],
  "acting_hand_features": {
    "points": 12,
    "lengths": {"S": 5, "H": 4, "D": 2, "C": 2},
    "shape": "5422"
  },
  "public_context": {},
  "operator_profile": "PENDING"
}
```

A feature-vector test is design evidence, not runtime evidence. Before activation it must be converted into one or more exact 13-card hands and run through the final evaluator.

## Opening tests

### 1♥

Source conditions: 12-22, at least five hearts, denies five spades.

Required cases:

- positive: 12 points, 5♥, at most 4♠;
- upper boundary: 22 points, 5♥, at most 4♠;
- negative lower: 11 points with otherwise matching shape;
- negative upper: 23 points with otherwise matching shape;
- negative spades: 12-22 with 5♥ and 5♠;
- public inference: selected 1♥ must publish `hearts_min=5`, `spades_max=4`, and the approved point interval;
- hidden-information: adding actual partner cards must be rejected without changing the result.

Readiness: point operator required; otherwise source boundary is clear.

### 1♠

Source conditions: 12-22, at least five spades.

Required cases:

- positive and both point boundaries;
- negative at 11 and 23;
- conflict case with 5♥/5♠ because the PDF does not state which major is opened;
- hidden-information rejection.

Readiness: point operator plus major-opening priority for two five-card majors.

### 1NT

Source conditions: 15-17; shapes printed as any 5332, 5m422, 6m322.

Required cases:

- point boundaries 14/15/17/18;
- one exact hand for every accepted shape token and suit permutation;
- one rejected neighbouring shape for each token family;
- conflict cases with a five-card major, because `любые 5332` overlaps 1♥/1♠;
- no inference beyond the exact approved shape domain;
- hidden-information rejection.

Readiness: shape-token mapping and opening precedence required.

### 2NT

Same shape-family tests as 1NT, with point boundaries 19/20/22/23. Test that 23 does not enter 2NT if the 2♣ balanced branch starts at 23.

### 1♣ / 1♦

The pair must be tested as a partition rather than independently.

Required cases:

- 3♣-3♦ selects 1♣;
- 4♣-4♦ invokes the unresolved suit-quality comparator;
- exactly 3♦ requires shape 4432 for 1♦;
- 5♣-4♦, 4♣-5♦ and longer-minor cases;
- every 12-22 hand without a five-card major must yield exactly one of: `1♣`, `1♦`, an explicitly different opening, or a documented gap;
- no hand may match both minor openings after the partition is approved.

Readiness: complete minor partition and suit-quality operator required.

### Weak and preemptive openings

For `2♦/2♥/2♠`, `3x`, `4x`:

- point boundaries around every printed interval;
- suit lengths immediately below, at and above the printed length;
- expected conflict until exact-versus-minimum length is approved;
- separate tests for whether `полублоки`, `блоки` or `сила до открытия` add a quality condition.

### 2♣ and 3NT

Keep non-executable until playing tricks, strong suit, `AKQxxxx` and stopper operators are approved. Tests must explicitly return `operator_unresolved`, not a bid.

## First responses to 1♣

### 1♦

Required cases:

- 6-point lower boundary;
- exactly four diamonds with no four-card major;
- exactly four diamonds with a four-card heart or spade must reject 1♦;
- five or more diamonds plus a four-card major is an explicit priority test, currently unresolved;
- public inference must not deny a four-card major unless diamonds are exactly four and the source condition applies.

### 1♥ / 1♠

Required cases:

- 4-4 majors selects 1♥;
- 5♥-4♠ selects 1♥;
- 4♥-5♠ selects 1♠;
- 5-5 and longer equal majors must produce `conflict/operator_unresolved` until approved;
- lower strength boundary;
- forcing state `Ф1` must be propagated after glossary approval.

### 1NT / 2♣ overlap

Both printed rules accept 6-10 with no four-card major; 2♣ additionally requires 5+ clubs.

Required conflict/priority cases:

- 6-10, no four-card major, 4 clubs: 1NT candidate only;
- 6-10, no four-card major, 5+ clubs: both raw predicates match; the resolver must not rely on row order;
- approved priority must make exactly one action applicable.

### 2NT / 3♣ overlap

Both use 11-12; 2NT is balanced without a four-card major, while 3♣ requires 5+ clubs.

Required cases:

- balanced 11-12, 4 clubs: 2NT candidate;
- balanced 11-12, 5+ clubs: raw overlap to be resolved explicitly;
- non-balanced 11-12 with 5+ clubs: 3♣ candidate if the final balanced operator excludes it.

### Game-forcing two-level suit responses

For 2♦/2♥/2♠:

- 12 versus 13 lower boundary;
- 4 versus 5 cards in the named suit;
- multi-suit hands require an explicit suit-selection policy;
- the result must set `ФГ` without consulting hidden partner cards.

### Three-level preemptive responses

For 3♦/3♥/3♠:

- point boundaries 3/4/7/8;
- suit boundary 6/7/8 cards;
- `блок` quality/operator test remains unresolved.

### 3NT

- point boundaries 12/13/15/16;
- balanced-domain cases;
- absence/presence of a four-card major;
- overlap with a game-forcing 2♦ response when a balanced hand contains five diamonds must be tested explicitly.

## Cross-cutting tests

- WORLD rule with identical conditions never appears in the SCHOOL runtime catalog.
- Candidate or unreviewed rule never appears in runtime.
- Missing source link blocks activation.
- Any latest FAIL or missing required test blocks activation.
- Open conflict removes the affected active rule from retrieval.
- Active rule, test definition and relation cannot be rewritten.
- Decision trace and ingestion event are append-only.
- Actual partner/opponent hands and full deals are rejected recursively.
- Same request fingerprint cannot produce two different traces.

## Exit criteria for Wave 01

Wave 01 is activation-ready only when:

1. exact source transcriptions are reviewed;
2. every required semantic operator is approved or linked to other active SCHOOL CANON;
3. the conflict matrix produces exactly one action or an intentional gap for every tested state;
4. exact 13-card fixtures replace abstract feature vectors;
5. all required tests pass independently at level I2 or higher;
6. no external/world meaning has been promoted silently.
