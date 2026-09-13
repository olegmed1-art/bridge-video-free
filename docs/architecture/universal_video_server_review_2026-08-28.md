# Universal Video server final review and card complement reconstruction

Date: 2026-08-28

Change identifier: `UV-SERVER-REVIEW-CARDS-20260828`

Governance mode: `ASSURED`

Status: implementation candidate; production activation requires reviewed merge and Oracle rollout.

## Purpose and scope

Move repeatable final technical verification to the resident compute server for every Universal Video input. The implementation is source-agnostic: no file name, teacher, lesson number, Drive ID, or platform-specific layout is encoded.

Included:

- a deterministic `server_review.json` generated after the first result-conformance pass;
- an independent second conformance pass that binds and revalidates the review packet;
- exception-only handoff with bounded excerpts and no raw media or full transcript;
- automatic fourth-hand reconstruction when three complete 13-card hands provide 39 unique observed cards;
- evidence fusion for visually recognized cards and normalized teacher-speech declarations;
- the same reconstruction after safe multi-frame accumulation;
- explicit `OBSERVED` versus `DERIVED` provenance and confidence basis;
- fail-closed handling of incomplete hands, invalid cards, duplicate cards, cross-seat conflicts, and tampering.

Excluded:

- automatic School Canon promotion;
- claims that deferred bridge or pedagogical interpretation ran;
- inference of a missing hand from fewer than three complete hands;
- layout-specific rules or source-specific exceptions;
- new paid compute or model dependencies.

## Evidence and assurance

Primary evidence is the immutable result bundle (`manifest.json`, transcript/QC artifacts and keyframe hashes) plus the canonical 52-card deck contract. The review generator and the result-conformance validator are separate code paths. The validator independently recomputes the exact expected exception inventory and fails closed on omission, mutation, unsafe locators, unbounded excerpts, or a changed canon boundary.

Card reconstruction is allowed only after the card contract proves three complete hands and 39 unique cards. Deck subtraction is exact conditional on those observations. The derived hand records:

- `provenance_class=DERIVED`;
- the three source seats;
- preserved observed cards in the fourth hand, if any;
- each computed card;
- logical-complement confidence and the available source-observation confidence floor.

After play starts, one card from the previously hidden fourth hand may become visible. This is a valid 40-card evidence state, not an overfull-deal error: the exposed play card remains `OBSERVED`, while the other 12 cards of that hand are `DERIVED`. The same rule works for additional consistent exposed cards. An exposed card that duplicates or contradicts a card in another seat fails closed.

A teacher may also name a card and assign it to a hand. Language-specific extraction must first produce a normalized bounded declaration with card, seat, teacher role, transcript segment locator, time interval and confidence. The generic fusion boundary admits only attributable declarations at or above the confidence threshold. Low-confidence or unattributed speech remains a review candidate; a cross-seat contradiction with visual or prior speech evidence produces `CONFLICT` and no fused deal. Accepted speech evidence participates in the same 39-card complement rule and remains traceable as `TEACHER_SPEECH`.

An incomplete spoken designation, such as “West has an ace” or “North has a club”, is stored as a rank/suit constraint and never promoted directly to an exact observed card. It resolves only when the already canonical hand admits exactly one matching card. Multiple candidates remain `PARTIAL_CARD_AMBIGUOUS`; zero candidates in a complete hand produce `PARTIAL_CARD_CONTRADICTS_COMPLETE_HAND`. A uniquely resolved constraint is kept separately as constraint evidence and does not relabel a logically derived card as visually or verbally observed.

Boundary and invariant tests cover one/two/three visible hands, 38/39/40 recognized cards, all four possible missing seats, cross-seat duplicates, conflicting partial fourth-hand observations, multi-frame accumulation, server-review tampering and missing review packets.

## Cost and operational effect

The post-process uses deterministic Python over already-created compact artifacts. It does not rerun ASR, decode the source video, call an external model, or create a new paid resource. The downstream chat receives summary and exceptions instead of needing the full media/transcript for routine integrity checks.

## Rollback and compatibility

Rollback is the revert of this change before rollout, or redeployment of the preceding reviewed Universal Video revision. Existing in-flight jobs remain pinned to their submitted revision and are not interrupted. The low-level `canonicalize_video_deal` function keeps derivation opt-in for callers that need observation-only normalization; recognition and multi-frame reconstruction enable the exact complement rule by default.

No production promotion, Oracle restart, or mutation of an active job is part of this change record.
