# Video 3.1 auction and PBN audit — 2026-08-30

Verdict: **IMPLEMENTED_NOT_PROVEN / REVIEW_REQUIRED**.

## Findings in PR #846

- Auction observations are grouped by board number and dealer. That does not
  distinguish a later repetition of the same board number in one video.
- Partial sequences are preserved correctly, but channel observations are not
  individually bound to the exact source/frame identity.
- PBN can display a fourth hand calculated from 39 cards as `X-Derived-*`.
  Video 3.1 FREE must not reconstruct hidden or fourth hands.

## Corrected boundary

- Every auction cell requires agreeing visual OCR and visual-reference channels
  with distinct channel IDs, confidence >= 0.90 and the same frame SHA-256.
- Speech cannot create a call.
- Cell seat and row must match the dealer-relative sequence without gaps.
- The call prefix must satisfy bridge auction mechanics, including doubles,
  redoubles, sufficient bids and termination.
- Complete/partial status is never inferred from convenience; it must match
  legal termination.
- Temporal consensus requires at least two frames and exact source-bound deal
  identity. Equal board numbers from different instances conflict rather than
  merge.
- Standard PBN auction output requires a complete, legal, temporally confirmed
  auction from the same deal instance.
- Standard PBN `Deal` requires 52 unique cards that are either human-verified
  for all four seats or individually backed by two frames and three independent
  visual channels.
- Partial cards and auctions remain only in `X-*` review tags.
- No hidden/fourth-hand derivation is performed or displayed.

No real video, runtime route, Drive output or production activation was used in
this cycle. Real-video auction accuracy and PBN correspondence remain unproved.
