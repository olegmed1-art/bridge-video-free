# Video 3.1 board context audit — 2026-08-30

Verdict: **IMPLEMENTED_NOT_PROVEN / REVIEW_REQUIRED**.

## Main findings

The current profiled challenger could previously accept `TEACHER_SPEECH` as a
source for board metadata. It also labelled dealer and vulnerability as part of
a confirmed metadata record when they had only been derived from the duplicate
board cycle. That is useful bridge context, but it is not independent visual
evidence.

PR #846 adds a Bridgit compass adapter, but its deal identity is based on a
scoped board number. The same board number can occur again later in one source,
so that identity alone does not prove that frames belong to one deal instance.

## Corrected boundary

- speech cannot create board, dealer, vulnerability, seat or orientation facts;
- board number, dealer marker and vulnerability must each have visual evidence
  bound to the exact frame SHA-256;
- all four compass labels must form a valid 0/90/180/270 bridge rotation;
- observed dealer and vulnerability must agree with duplicate-board mechanics;
- a board instance is bound to source, scope, instance ID and anchor-frame hash;
- at least two strictly chronological, distinct frames are required;
- the anchor frame must occur in the segment;
- an instance cannot disappear and later reappear as if it were contiguous;
- equal board numbers with different instance identities stay separate;
- cross-source, duplicate-frame and inconsistent-orientation input fails closed.

If only the board number is observed, dealer/vulnerability may be retained as
derived context but the record is `PARTIAL_VISUAL_EVIDENCE`, not `CONFIRMED`.

## Proof still missing

No real Diana 13 board-context frames were processed in this cycle. The
compass, boundary and anchor observations still need independent real-video
holdout evidence. Production activation remains forbidden.
