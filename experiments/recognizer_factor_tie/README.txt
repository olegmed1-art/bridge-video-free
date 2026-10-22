Factor identity tie correction - source-only candidate - 2026-10-22

Status: RELEASE HOLD. No runtime wiring, installation, Geometry activation,
threshold change, queue/lifecycle work, model download or full video inference.
Governance: ASSURED; local development evidence is not human ground truth.

Problem and behavior
The frozen fallback uses min(rank_score, suit_score) as a joint quality score.
When the weaker component caps several identities at the same value, argsort
can choose a diagnostic card that contradicts unique component maxima at that
same already selected ROI. This patch keeps that observation UNKNOWN, writes
best_card=null and identity_status=TIE, preserves the entire previous factor
diagnostic in legacy_factor_diagnostic, and returns before any acceptance gate.
component_candidate remains a machine hypothesis, never a confirmed card.

Scope is deliberately narrow: zero legacy joint margin, unique rank and suit
maxima at the same winning ROI, a different component candidate, and equal
joint quality for the two identities. No maxima from different registrations
are combined. No rank, suit, case ID, filename or pixel-coordinate allowlist
is used. All other diagnostic objects are returned unchanged.

On 137 existing development diagnostics, 129 remain verbatim and eight become
explicit TIE/UNKNOWN. There are 22 joint plateaus: fourteen already agree with
their same-ROI component maxima and are preserved. The eight corrections do
not intersect the historical 73 Margin additions. No new card is accepted.
These are regression observations, not accuracy or false-positive estimates.

Source boundaries
This branch starts at main a993b69d931f88f84fac0c2dd3d02592f3d2e5ee.
That main does not contain bridgit_rank_suit_candidate.py. Its partial observer
constructor also lacks the frozen candidate's with_receipts parameter. Thus
adding the frozen fallback directly to main's active observer would be an
unqualified API change. This package supplies an inert, reversible source
patch against exact historical bytes instead. The new pure helper has no
active main caller. BASELINE_PINS.json pins the frozen source, patched source,
helper and patch. frozen_rank_suit_candidate.py.txt is a code fixture only.

Verification
Public offline regressions use only synthetic component scores, stdlib and git:
  python3 -B -m unittest discover -s tests -p test_factor_identity_diagnostic.py -v
They cover arbitrary ranks/suits, component ties, positive margins, malformed
inputs, idempotence, no acceptance, strict patch apply, exact byte hashes and
reverse application. No sprite, source image or private label is committed.

Private local checks first reproduced eight failures on frozen diagnostics,
then passed all 137 saved-array cases. An AST branch replay of the staged
source tail confirms UNKNOWN for all 137 and early return on the eight even
if all old gates are supplied as true. This is NOT a pixel or full-worker run.
Independent assurance uses a separate exact integer oracle over saved binary32
scores, rectangle unions and local Pareto frontiers, rather than this helper.

The human packet is local-only: 59 blank records / 58 unique existing PNGs;
12 requested baseline PNGs are unavailable. It contains no machine answers or
source map. Two humans must freeze independent readings before the private
join. No human labels have been generated or submitted in this change.

Integration gates and rollback
1. Coordinate this isolated branch with the recognizer owner; do not merge
   competing branches blindly. Keep the separate release HOLD in force.
2. Bind an exact release source bundle whose observer API supports the frozen
   candidate. Apply only after all BASELINE_PINS match; pin the helper and new
   source bytes in a NEW candidate manifest, retaining the old manifest.
3. Qualify null best_card / identity_status=TIE with downstream diagnostic
   readers. They must not turn component_candidate into an accepted card.
4. Any later pixel replay, installer or runtime release is a separate stage.
   Human labels and scene/deal evidence remain required for quality claims.

Rollback is to leave the current release selected. The source patch also
reverses cleanly with git apply --reverse; original fixture bytes are retained.
There is no default enable flag or activation step in this package.
