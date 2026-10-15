# Partial card evidence: offline vertical synthetic path

`bridge_vision/bridgit_partial_evidence.py` defines one explicit contract for
sample spacing, frame identity, and shadow aggregation. It requires a matching
`bridgit-adjacent-candidate-v1` profile; a sample interval that can fit within
the profile's 10,000 ms neighbor bound; a confirmed upper-right board number;
explicit source-video SHA-256, episode, board occurrence, frame dimensions,
independent frame hashes, timestamps, and consecutive observation indices.
Tracks split when identity or timing changes. Unknown identity fails closed.

The `accumulate_detected_frames` entry point checks local frame bytes against
their declared SHA-256, calls a supplied card detector, and passes accepted
visual observations to the same aggregator. The vertical regression uses the
existing `NativeFourSeatCardDetector` with an explicitly synthetic stub
backend. Two partial samples contribute three distinct observed cards; the
remaining cards stay unknown. A rejected center-seat card and low-confidence
card remain review evidence. Cross-seat ownership conflict removes the
disputed card from the merged hand and keeps both supporting observations.
The output stays `REVIEW_*`, has `canonical_promotion_allowed=false`, and
reports `real_video_accuracy_evaluated=false`.

The existing frozen Gambler rank backend cannot yet supply this input for a
real incomplete hand: its geometry pass returns `PARTIAL_PLAY` before it emits
card observations. The primary video extractor still uses its independent
two-frame full-layout path and accepts only validated 52-card candidates.
This synthetic path does not activate a partial pixel backend or change the
frozen r26.3 Gold profile. The current production sampling gap (15,000 ms)
also fails the new partial contract rather than being silently treated as
neighboring observations; an explicitly configured partial sampler must use
a compatible interval and independently verified episode identity.

Verification: 102 passed, 4 skipped in the affected OCR, multiframe,
manifest, and compare modules before the final minimum-gap assertion; the
partial-evidence module then passed again with that assertion: 14 passed.
These results are synthetic contract checks, not real-video precision/recall.

Still required for a real evaluation: local bytes and verified hashes for
Gold-v2 and the eight pinned Gambler sprites; an approved per-card partial
pixel observer with native geometry/seat mapping and reference receipts;
source video plus two independent frames of the same board with verified
episode and PTS; and the separate 24-case/4-session holdout. No owner card
relabeling was requested.
