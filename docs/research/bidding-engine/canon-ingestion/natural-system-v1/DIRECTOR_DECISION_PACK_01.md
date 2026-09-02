# Director decision pack 01 - pending internal cross-check

Status: **PREPARED / NO DIRECTOR ACTION REQUIRED YET**

The questions below affect executable meaning, but they are not being sent one by one. First, every remaining PDF block will be checked for an internal answer. Only questions still unresolved after that pass will be presented to the Director with examples and consequences.

The PDF remains canonical even where its machine operator is not yet defined. Undefined operators block activation; they do not invalidate the source.

## A. Foundational evaluators

### A1. `очки`

**Question:** Which point-count method is intended by every printed range?

**Why it matters:** Every strength boundary depends on this evaluator.

**Current handling:** Ranges are stored with `method=UNRESOLVED_SCHOOL_POINT_METHOD`; no card-level test may claim PASS yet.

**Internal cross-check:** Search the complete PDF for any explicit honour/point definition. If absent, search other explicitly approved foundational SCHOOL CANON only.

### A2. `равн` / `равномер`

**Question:** Which exact shapes are included when the PDF says balanced?

**Why it matters:** 1NT, 2NT, natural NT responses and many rebids depend on it.

**Source evidence already present:** The opening lines explicitly list `любые 5332, 5m422, 6m322` for 1NT and 2NT, but other lines use the shorter words `равн` or `равномер` without repeating the list.

**Decision to prepare:** Whether those shorter terms refer to the same printed opening shape family or to a different school definition.

### A3. `+`

**Question:** Confirm that every `4+`, `5+`, `7+`, `13+` is an inclusive lower bound.

**Current assessment:** Strongly source-implied; expected to become a mechanical operator if the complete-document consistency check finds no counterexample.

## B. Opening partition and priorities

### B1. 1♣ versus 1♦

**Explicit source facts:**

- 3♣-3♦ opens 1♣;
- 4♣-4♦ opens the better-quality minor;
- exactly three diamonds in 1♦ occurs only with 4432;
- neither minor opening permits a five-card major.

**Still needed:** Complete partition for every other minor-length combination and the machine comparator for `лучшая по качеству`.

**Risk if guessed:** The same hand may open both 1♣ and 1♦, or neither.

### B2. 1NT versus a five-card major

**Conflict:** The PDF says 1♥/1♠ starts with five cards, while 1NT accepts `любые 5332`.

**Needed decision:** Which opening has priority on 15-17 balanced 5332 with a five-card major.

**Current handling:** Explicit `CANONICAL_CONFLICT`; never resolved by rule order.

### B3. Two five-card majors

**Conflict:** 1♥ denies five spades, but the 1♠ line does not define the full priority for 5-5 majors.

**Needed decision:** Opening action and public inference for 5♥-5♠ and longer equal majors.

## C. Length semantics and preempts

### C1. Bare `6`, `7 карт`, `8 карт`

**Question:** Do these mean exactly six/seven/eight cards or at least that many?

**Contrast:** Elsewhere the PDF explicitly uses `+`, so the absence of `+` cannot be silently ignored.

**Affected calls:** 2♦/2♥/2♠, all three-level suit openings, all four-level suit openings.

### C2. `полублоки`, `блоки`, `сила до открытия`

**Question:** Are these descriptive labels or additional hand-quality predicates? If predicates, what are their exact boundaries?

**Current handling:** Strength and length written numerically are stored; the descriptive predicates remain unresolved and block activation where they can change applicability.

## D. Strong and gambling openings

### D1. 2♣ `сильная масть`

**Question:** Exact suit-quality definition for the strong-suit branch.

### D2. 2♣ `8+ взяток с руки`

**Question:** Exact playing-trick calculation.

### D3. 3NT `AKQxxxx` and `без задержки сбоку`

**Questions:**

- exactly seven cards or at least seven;
- exact stopper definition;
- whether no side stopper means no stopper in every other suit.

## E. Forcing-state glossary

Terms requiring one school-wide machine glossary:

- `НФ`;
- `Ф1`;
- `ФГ`;
- `ИНВ`;
- `играть`.

The glossary must specify:

- whether partner may pass immediately;
- how long the force lasts;
- target level/contract where relevant;
- which public inference is created;
- how interference affects the state, if the PDF covers it.

## F. Response priorities after 1♣

### F1. 1NT versus 2♣

Raw predicates overlap on 6-10, no four-card major, and 5+ clubs.

### F2. 2NT versus 3♣

Raw predicates can overlap on 11-12 balanced hands with 5+ clubs.

### F3. 3NT versus 2♦

Raw predicates can overlap on 13-15 balanced hands without a four-card major but with 5+ diamonds.

### F4. Multiple five-card suits in a game-forcing hand

The source offers 2♦/2♥/2♠ for 13+ and 5+ cards but does not yet provide the complete suit-selection order in the transcribed block.

## Decision workflow

For each question:

1. search all remaining PDF blocks for explicit internal evidence;
2. search only other approved SCHOOL CANON, if available;
3. construct exact conflicting hands and auction examples;
4. show how each possible interpretation changes bids and public inferences;
5. provide a recommendation, clearly marked as recommendation;
6. obtain one batched Director decision;
7. record it as a separate source/decision object;
8. compile tests before activation.

No answer from WORLD Knowledge will be treated as the school answer.
