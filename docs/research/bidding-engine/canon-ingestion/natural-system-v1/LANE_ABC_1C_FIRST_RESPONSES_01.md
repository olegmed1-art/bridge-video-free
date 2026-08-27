# Lane A/B/C — first responses to 1♣

Status: WORKING CLASSIFICATION / NOT ACTIVE CANON
Source block: `NSV1-P1-R1-C2`
Candidate count: 14

## Lane A — structurally compilable now, subject to point evaluator and explicit conflict overlays

- `1♦` — numeric lower bound, 4+♦ and explicit condition for exactly four diamonds are representable; incomplete priority remains an overlay.
- `1♥` — 6+, 4+♥ and printed 4-4 / 5-4 major selection cases are representable; unprinted multi-major cases remain overlays.
- `1♠` — 6+, 4+♠ and printed exclusions/selections are representable; unprinted multi-major cases remain overlays.
- `1NT` — 6–10, no four-card major, NF semantic tag representable; overlaps 2♣ on some hands.
- `2♣` — 6–10, 5+♣, no four-card major, NF tag representable; overlaps 1NT.
- `2♦`, `2♥`, `2♠` — 13+, 5+ suit, FG tag representable; multiple-suit selection remains an overlay.
- `3♣` — 11–12, 5+♣, INV tag representable; target/acceptance semantics remain non-executable detail.
- `3♦`, `3♥`, `3♠` — 4–7 and 7+ suit are structurally representable; `block` retained as source tag only until quality semantics are defined.

Lane A here means candidate/test structure can be compiled. It does not authorize activation and does not assert that unresolved semantic tags are additional predicates.

## Lane B — blocked by an unresolved applicability predicate

- `2NT` — generic `равн` must be defined for this context.
- `3NT` — generic `равномер` and `играть` applicability/state semantics remain unresolved.

## Lane C — explicit overlap/priority overlays

- `1NT` vs `2♣`: 6–10, no 4-card major, 5+♣.
- `2NT` vs `3♣`: 11–12, balanced, 5+♣.
- `3NT` vs `2♦`: 13–15, balanced, no 4-card major, 5+♦.
- `2♦/2♥/2♠`: multiple qualifying 5+ suits in a 13+ hand.
- `1♥/1♠`: major-selection cases beyond the explicit 4-4 and 5-4 statements.
- `1♦`: diamonds 5+ together with a four-card major where source priority is not yet complete.

## Global blocker

Every card-level numeric strength test remains blocked by `OP-007 school point method`. Symbolic range tests are permitted.

## Safety

No external system was used to resolve any priority. No database activation is permitted from this classification.