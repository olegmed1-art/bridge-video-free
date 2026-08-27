# Residual semantic decisions — concrete examples 01

Status: PREPARED / NOT YET CANONICAL DECISIONS

Purpose: convert abstract unresolved operators into concrete bidding consequences. Examples are diagnostic fixtures only; where a hand depends on an unresolved point evaluator, the stated point range is treated symbolically rather than asserting a card-level HCP total.

## Opening-priority fixtures

### DEX-01 — 1NT versus five-card major
Public state: dealer to act, no prior calls. Hand satisfies the PDF's printed 15–17 range and an explicitly allowed `5332` family, with five hearts.

- If 1NT has priority: action = 1NT; public inference includes the printed NT shape/range.
- If five-card-major opening has priority: action = 1♥; public inference follows the 1♥ opening line.
- Current result: CANONICAL_CONFLICT until Director decision OP-033.

### DEX-02 — equal five-card majors
Public state: dealer to act, no prior calls. Hand has 5♥ and 5♠ and otherwise meets the relevant one-level opening strength.

- The PDF explicitly denies five spades on the 1♥ opening line.
- The remaining complete priority is not machine-defined by the source.
- Current result: KNOWLEDGE_GAP / priority decision required; do not infer standard 'higher suit first' practice.

### DEX-03 — minor partition outside explicit tie cases
Public state: dealer to act. Hand has no five-card major and a minor pattern not covered by the PDF's explicit 3–3 or 4–4 tie statements.

- Candidate 1♣ and 1♦ predicates cannot be made exhaustive/disjoint without OP-009/OP-010.
- Current result: affected hands remain unresolved rather than resolved by longest-minor folklore.

## 1♣ response-overlap fixtures

### DEX-04 — 1NT versus 2♣
Auction: 1♣ – ?. Responder lies in the printed 6–10 range, has no four-card major, and has 5+ clubs.

- Raw source predicates can satisfy both 1NT and 2♣.
- Required: explicit school priority or an additional condition.
- Current result: CANONICAL_CONFLICT.

### DEX-05 — 2NT versus 3♣
Auction: 1♣ – ?. Responder lies in the printed 11–12 balanced range and also has 5+ clubs.

- Raw predicates overlap.
- Current result: CANONICAL_CONFLICT until priority/condition is defined.

### DEX-06 — 3NT versus 2♦
Auction: 1♣ – ?. Responder lies in the printed 13–15 balanced range, lacks a four-card major, and has 5+ diamonds.

- Raw predicates overlap.
- Current result: CANONICAL_CONFLICT.

### DEX-07 — multiple 5+ suits in a game-forcing response
Auction: 1♣ – ?. Responder has 13+ by the school point method and qualifies for more than one printed 5+ suit response.

- Source gives multiple legal-looking actions but the complete selection order is not explicit.
- Current result: priority decision required.

## Weak/preemptive length fixtures

### DEX-08 — printed bare `6`
A hand has seven cards in the suit and otherwise satisfies a weak two line printed with bare `6`.

- Exact interpretation: seven cards fail the length predicate.
- Minimum interpretation: seven cards pass.
- This changes both action eligibility and public inference; therefore no silent choice is allowed.

### DEX-09 — printed `7 карт` / `8 карт`
Analogous boundary fixture for three-/four-level openings. Test exact N, N+1 and N-1 once OP-004 is decided.

## Strong/gambling evaluators

### DEX-10 — 2♣ strong-suit branch
Two hands have identical printed point range and suit length but different suit quality. Without a machine definition of `сильная масть`, the system cannot know whether both, one or neither qualifies.

### DEX-11 — `8+ взяток с руки`
Two hands can have the same point count/shape but different immediate playing-trick expectation. A card-level evaluator is required; HCP cannot be substituted.

### DEX-12 — 3NT AKQxxxx
Compare a seven-card AKQ-headed suit with an eight-card AKQ-headed suit. The bare source token does not establish whether the eighth card is allowed.

## Convention/glossary fixtures

For splinter, fourth-suit forcing, cue-bid, 'good long suit', 'full long hearts', 'values', and 'second honour at partner', candidate tests will be created only after the school predicate is supplied. Standard external definitions are deliberately excluded.

## Effect on compilation

These fixtures partition the rule set into:

1. rules unaffected by any unresolved operator — may advance to positive/negative/boundary tests now;
2. rules with unresolved but non-conflicting semantic tags — may be stored as source candidates but not activated;
3. overlapping/ambiguous rules above — must return conflict/gap until a Director decision becomes separate canonical evidence.