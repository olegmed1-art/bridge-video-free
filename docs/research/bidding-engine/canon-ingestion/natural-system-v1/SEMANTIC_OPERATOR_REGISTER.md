# Semantic operator register

Source: `school-canon-bidding-natural-system-v1`  
Status: **ACTIVE / NO GUESSING**

This register separates exact source transcription from the operators needed to execute it. A term appearing in the PDF is canonical as text, but it becomes executable only after its machine meaning is fixed without changing the source.

## Classification

- `MECHANICAL` - notation conversion only; no bridge meaning is added.
- `SOURCE_IMPLIED` - meaning appears strongly implied by several explicit source lines, but must be verified through consistency tests.
- `DIRECTOR_DECISION` - a material bridge definition is not fully specified by the PDF.
- `OTHER_CANON_REQUIRED` - should be linked to an already approved foundational school definition if one exists.

## Operators

| ID | Source term / pattern | Needed machine meaning | Class | Current status | Effect if unresolved |
|---|---|---|---|---|---|
| OP-001 | `♣ ♦ ♥ ♠`, `C D H S` | Canonical call/suit normalization | MECHANICAL | ready | none |
| OP-002 | `NT`, `БК` | Normalize to `NT` without changing the bid | MECHANICAL | ready | none |
| OP-003 | `+` after points or length | Inclusive lower bound | SOURCE_IMPLIED | pending consistency test | blocks precise boundaries |
| OP-004 | bare lengths `6`, `7 карт`, `8 карт` | Exact length versus minimum length | DIRECTOR_DECISION | open | blocks weak/preemptive openings |
| OP-005 | `m` in `5m422`, `6m322` | Minor-suit placeholder and allowed permutations | SOURCE_IMPLIED | open | blocks exact NT shape matcher |
| OP-006 | `5332`, `4432`, `5m422`, `6m322` | Shape canonicalization independent of suit order | SOURCE_IMPLIED | open | blocks shape evaluation |
| OP-007 | `очки` | Exact point-count method used by the school | OTHER_CANON_REQUIRED | not located | blocks all HCP boundaries |
| OP-008 | `равномер` | Exact balanced/semi-balanced shape set | OTHER_CANON_REQUIRED / DIRECTOR_DECISION | open | blocks many NT and rebid rules |
| OP-009 | `лучшая по качеству` | Suit-quality comparator for equal minors | DIRECTOR_DECISION | open | blocks 1♣/1♦ partition |
| OP-010 | minor-opening partition | Longest-minor and equal-length tie rules beyond explicitly stated 3-3 and 4-4 cases | DIRECTOR_DECISION | open | may create 1♣/1♦ overlaps or gaps |
| OP-011 | `сильная масть` | Strength/quality condition for the strong 2♣ suit branch | DIRECTOR_DECISION | open | blocks part of 2♣ |
| OP-012 | `8+ взяток с руки` | Playing-trick evaluator | DIRECTOR_DECISION | open | blocks part of 2♣ |
| OP-013 | `полублоки` | Whether this is descriptive only or an additional hand-quality condition | DIRECTOR_DECISION | open | blocks or weakens 2♦/2♥/2♠ tests |
| OP-014 | `сила до открытия` | Exact upper strength boundary for preempts | DIRECTOR_DECISION | open | blocks 3- and 4-level preempts |
| OP-015 | `задержка` / `без задержки сбоку` | Stopper evaluator by suit | OTHER_CANON_REQUIRED / DIRECTOR_DECISION | open | blocks 3NT gambling opening |
| OP-016 | `AKQxxxx` | Exact seven-card pattern versus at-least-seven headed AKQ | DIRECTOR_DECISION | open | blocks 3NT boundary |
| OP-017 | `НФ` | Non-forcing auction state | SOURCE_IMPLIED | glossary pending | blocks forcing-state trace |
| OP-018 | `Ф1` | Forcing for one round | SOURCE_IMPLIED | glossary pending | blocks forcing-state trace |
| OP-019 | `ФГ` | Game-forcing auction state | SOURCE_IMPLIED | glossary pending | blocks forcing-state trace |
| OP-020 | `ИНВ` | Invitational action and target contract | SOURCE_IMPLIED | glossary pending | blocks ranking and stop conditions |
| OP-021 | `фит` | Minimum guaranteed combined trump length and any source-specific exception | OTHER_CANON_REQUIRED | open | blocks fit-dependent responses |
| OP-022 | `реверс` | Semantic tag plus any required strength/shape operator not already explicit on the line | SOURCE_IMPLIED | pending line review | may block continuations |
| OP-023 | `сплинтер`, `(авто)сплинтер` | Shortness domain, fit requirement and forcing level | OTHER_CANON_REQUIRED / DIRECTOR_DECISION | open | blocks splinter branches |
| OP-024 | `форсинг четвертой мастью` | Artificial fourth-suit action, forcing level and public inferences | OTHER_CANON_REQUIRED | open | blocks several rebids |
| OP-025 | `трансфер` | Forced relay and completion semantics | SOURCE_IMPLIED | pending line review | blocks NT continuations |
| OP-026 | `Стейман` | Question/response state and continuation semantics | SOURCE_IMPLIED | partly explicit in PDF | blocks NT continuations |
| OP-027 | `кюбид` / `кюбиды` | Control-showing semantics and control definition | OTHER_CANON_REQUIRED | open | blocks slam continuations |
| OP-028 | `хорошая длинная масть`, `хорошая 6-карточная масть` | Suit-quality threshold | DIRECTOR_DECISION | open | blocks selected invitational/forcing branches |
| OP-029 | `полная длинная черва` | Exact suit-length/quality predicate | DIRECTOR_DECISION | open | blocks one 1♣ continuation |
| OP-030 | `ценности` | Response semantics after a forcing minor response to 2♠ | DIRECTOR_DECISION | open | blocks that continuation |
| OP-031 | `трешка` | Exactly three-card support | SOURCE_IMPLIED | pending glossary | blocks transfer continuations |
| OP-032 | `при второй фигуре у партнера` | Honour model and trick-count predicate | DIRECTOR_DECISION | open | blocks the 1♦-3♣ invitation |
| OP-033 | opening-rule precedence | Priority among overlapping explicit openings, especially 1NT versus five-card-major openings | SOURCE_IMPLIED / DIRECTOR_DECISION | open | resolver could choose two calls |

## Handling policy

1. Mechanical mappings may be implemented with reversible tests.
2. Source-implied mappings require a cross-document consistency check and an explicit entry in the ingestion log.
3. Director-decision items are batched into a small decision pack; they are not asked one line at a time.
4. An unresolved operator may appear in a candidate record, but that candidate remains `not_eligible` for activation.
5. External systems may be cited in the decision pack as comparison evidence, never as authority for the school definition.
