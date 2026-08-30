# 3.1 FREE r25.18 — autonomous profiled deal extraction

Date: 2026-08-29

Change ID: `3.1-free-r25.18-autonomous-profiled-deal-extraction`

Governance mode: `ASSURED_SHADOW`

## Outcome

The stable 3.1 FREE worker can now produce its own source-bound deal-evidence
bundle from a video and consume that bundle in the same run.  The complete
path is:

`video → dense frames → registered pixels → board/seat/card/auction evidence → validation → PBN + PDF`

Activation is opt-in and fail-closed.  The source folder must contain exactly
one hash-valid `BRIDGE_VISION_PROFILE.zip`.  The profile is reusable for a
verified interface layout; it contains no student name, video title, fixed
board number, or hard-coded South-seat rule.  If the profile is absent, r25.17
behaviour is unchanged.

## Frame production

- The configured interval is explicit and bounded to 1–30 seconds.
- Both logical endpoints are represented.  A 6,950-second video at a
  three-second interval has 2,318 planned frames.
- Every planned frame must decode and receive an evidence ID, timestamp,
  byte count, and SHA-256.
- The former 300-frame ceiling is absent.  The explicit safety limits are
  20,000 frames and 2 GiB of encoded frames.
- An incomplete timeline or exceeded byte budget aborts the profiled producer.

## Pixel evidence

- ORB/RANSAC registration maps each frame to the human-verified reference
  geometry.
- Full-card-corner matching uses the complete grayscale corner.
- Rank and suit use separately supplied glyph templates and an independent
  channel ID.
- A machine card needs rank, suit, and full-card agreement, confidence at
  least 0.90 per channel, and two distinct frame hashes.
- The verified compass region provides explicit board number, dealer, and the
  screen-position-to-`N/E/S/W` mapping.  All four rotations are supported.
- Auction cells, when configured, require template/OCR agreement and then pass
  dealer-relative bridge-mechanics validation.
- Transcript card phrases are retained only as corroborating diagnostics; a
  phrase alone cannot create a card or auction call.

## Publication and boundaries

The generated bundle is independently revalidated by the existing r25.17
consumer before it may populate `master_analysis.json`.  Accepted observations
produce a sibling PBN and one deal-review page per board in the PDF.  The page
contains the four hands, observed or derived status, auction, confidence, and
an actual source screenshot where available.

All generated evidence remains `SHADOW_ONLY` with
`canonical_promotion_allowed=false` and
`production_activation_allowed=false`.  The runtime change enables an
isolated producer in the 3.1 FREE route; it does not approve any specific
profile, merge a draft pull request, promote results into School Canon, or
process additional videos.

## Verification

- A real synthetic pixel test builds a complete 52-card template pack and
  verifies that the production recognizer reads `A♠`, board number, all four
  compass directions, and dealer marker from image pixels.
- An end-to-end test processes two independently hashed frames, produces the
  stable evidence schema, and revalidates it through the r25.17 consumer.
- Negative tests cover ZIP traversal, duplicate members and JSON keys, asset
  and profile hash mismatches, missing independent channels, one-frame cards,
  metadata conflicts, and invalid auctions.
- The dependency audit binds OpenCV, NumPy, optional ONNX Runtime, and
  Tesseract to declared profiled SHADOW purposes.
- Repository CI installs the OCR runtime, executes the producer and inherited
  evidence suites, compiles the complete revision chain, and leaves the pull
  request in draft state for independent review.

## Remaining risk

The code path is end-to-end, but real-world accuracy is profile-dependent.  A
specific interface profile still needs an independently reviewed asset bundle
and a labelled holdout before that profile can be treated as proven.  Until
then, failures and disagreements remain REVIEW and no canonical or production
promotion is permitted.

## Rollback

Repoint the worker and workflow to `bridge_runtime_hardening_r25_17`, remove
automatic profile discovery, and leave the r25.17 sidecar evidence intake in
place.  Existing outputs remain immutable.
