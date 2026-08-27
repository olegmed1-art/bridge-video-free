# Semantic cross-check 01 — complete approved PDF

Status: COMPLETE / FAIL-CLOSED
Source: `school-canon-bidding-natural-system-v1`

## Purpose

Re-evaluate the 33 semantic operators after all 34 source blocks have been covered, and distinguish notation that can safely become executable from bridge meaning that still needs another approved SCHOOL CANON source or a Director decision.

## Resolved mechanically

- OP-001 suit/call symbols: normalize ♣/♦/♥/♠ to C/D/H/S while preserving the call.
- OP-002 NT/БК: normalize to NT.

These transformations add no bridge semantics.

## Resolved from whole-document consistency

- OP-003 `+`: throughout the approved PDF it functions as an inclusive lower-bound marker. No contradictory use was found in the covered blocks. It may therefore compile as `>=` for the immediately attached numeric quantity.
- OP-005 `m`: in printed shape families such as `5m422` / `6m322`, `m` denotes a minor-suit placeholder. It does not identify which minor unless another condition does.
- OP-006 printed shape tokens: `5332`, `4432`, `5m422`, `6m322` can be represented as source shape families without silently broadening them to other shapes.
- OP-017 `НФ`: source use is consistent with a non-forcing label.
- OP-018 `Ф1`: source use is consistent with a one-round forcing label.
- OP-019 `ФГ`: source use is consistent with a game-forcing label.
- OP-020 `ИНВ`: source use is consistently invitational, but the exact target/acceptance evaluator remains line-specific and cannot be invented.
- OP-031 `трешка`: source use is consistent with three-card support; compile only where the referenced suit is unambiguous.

The forcing labels above may be stored as public semantic tags now. Their behavior under opponent interference remains undefined unless explicitly shown by the source, so no interference continuation is inferred.

## Partly resolved / scoped only

- OP-008 `равн/равномер`: the PDF explicitly supplies the opening NT shape family `5332`, `5m422`, `6m322`; shorter balanced-language occurrences elsewhere do not explicitly prove that the exact same family applies universally. Use the explicit family where printed; leave generic `balanced` unresolved elsewhere.
- OP-022 `реверс`: safe as a source semantic tag; any additional strength requirement must come from the exact line, not standard bridge knowledge.
- OP-025 `трансфер`: safe as a source semantic tag and branch relation where the relay/completion is printed; do not import external transfer variants.
- OP-026 `Стейман`: safe as a source semantic tag and only the printed response/continuation graph; do not import standard responses absent from the PDF.

## Still requires another approved SCHOOL CANON definition or Director decision

OP-004 bare 6/7/8 length semantics; OP-007 point-count method; OP-008 generic balanced domain; OP-009 suit-quality comparator; OP-010 complete 1♣/1♦ partition; OP-011 strong suit; OP-012 playing tricks; OP-013 semi-preempt predicate; OP-014 preempt strength ceiling; OP-015 stopper; OP-016 AKQxxxx exact/minimum length; OP-021 fit; OP-023 splinter/autosplinter; OP-024 fourth-suit forcing; OP-027 cue-bid/control definition; OP-028 good long suit; OP-029 full long hearts; OP-030 values; OP-032 second honour at partner; OP-033 opening precedence.

## Important outcome

The complete-document pass reduces several notation/glossary blockers without altering the school system, but it does **not** justify inventing the remaining bridge predicates. Candidates depending only on resolved operators can advance to formal test compilation. Candidates depending on any unresolved operator remain `not_eligible`.

## Database effect

None. This is a semantic review artifact; no rule is activated.