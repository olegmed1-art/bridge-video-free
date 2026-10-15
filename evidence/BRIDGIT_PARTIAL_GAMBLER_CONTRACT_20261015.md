# Local partial Gambler interface

The current full-layout production adapter (`bridgit_primary_production.py`,
`bridgit_primary_inputs_candidate.py`) explicitly downloads
`bridgit_gold_v2.zip` and uses it to build the pinned 1920×1010 geometry and
UI reference. The later Gambler adapter replaces its rank-template pixels with
original sprites. Gold-v2 is therefore a real dependency of that full-layout
adapter, but it is not the source of its final card artwork. No frozen profile
or production wiring was changed here.

`accumulate_gambler_partial_frames` is a separate **shadow-only** interface.
It requires a local original Gambler classic sprite in `asset_root`, a measured
registered card width/height for variant selection, and a caller-supplied
`pixel_observer(frame_path, validated_sprite)` returning `table_region` and
individual `cards` with card identity, confidence and pixel box. The existing
sprite resolver selects a variant from card scale; its loader checks the pinned
SHA-256 and native 13×4 dimensions. A missing or altered sprite fails with
`PINNED_GAMBLER_ASSET_UNAVAILABLE`. No Gold-v2 package is read by this
interface. It does **not** supply or validate a Bridgit table layout itself.
The shadow adapter opens the bounded local sprite in binary mode on Windows,
preserving PNG bytes for the SHA-256 check. The SHA-pinned production loader
remains unchanged.

Each sample must provide a local `frame_path` and its exact SHA-256, source
video SHA-256, explicit episode ID, board occurrence, two matching `READ`
board-number observations, raw-frame dimensions, PTS in milliseconds,
consecutive observation index and data class. The adapter binds a sprite-based
reference ID, validates declared identity before invoking the observer, checks
local frame bytes against the declared SHA-256, and passes visual observations
through the four-seat detector to the adjacent-frame accumulator. All tracks
remain `REVIEW_*`; canonical promotion and real-video accuracy are false.
The declared video source, episode and PTS still need independent upstream
verification; the adapter cannot infer them from a PNG filename.

`GamblerPartialPixelObserver` supplies a deterministic observer for explicit
native-size card corners. Its v2 matcher builds normalized, full-colour corner
templates for all 52 cards from the selected pinned sprite. The ROI dimensions
come from that sprite's actual native cell size. Unique byte-exact corners are
accepted; other crops require both an absolute score and separation from the
next card, plus preservation of the candidate template's white pixels. A
covered corner or uncertain match is returned as `unknown`; the adapter
carries that into review evidence bound to the frame hash. Its geometry includes image size, table
region, card size and a list of card-corner coordinates with seat labels. The
labels must agree with the four-seat geometry. In v3, automatic geometry can
replace those manual corners. Resampling and unverified native layout variants
are not silently accepted.

Synthetic tests replace only the asset-loading seam where needed. Local
verified assets are available outside this checkout at
`C:/Users/Oleg/Documents/Codex/2026-10-11/task-5/ocr-review/gambler-assets/`;
the eight files were revalidated against pinned SHA-256 and decoded to 52
cells each. One owner-provided 1680×934 masked frame was used only for a
single-frame, manually positioned smoke test; its output is in
`BRIDGIT_PARTIAL_PIXEL_SINGLE_FRAME_SMOKE_20261015.json`. The positions are
not a validated layout profile, its board identity/PTS are unverified, and no
paired observation is available. The source video is unavailable locally.
The scale regression uses 52 exact rendered source cells in each of the eight
pinned variants: 416 accepted, 0 wrong, 0 unknown. It also checks covered,
blank and incomplete corners once per variant: 24 unknown, 0 filled. This is
source-cell coverage, not video accuracy. The v2 single-frame smoke leaves 7
of 16 manually selected positions unknown (six `CARD_UNCERTAIN`, one
`CORNER_OCCLUDED`) and emits nine card values; no guess fills those gaps.
The separate 24-case/4-session holdout remains unrun.

The v3 observer supports `auto_discover=True` with no manual corners. It
derives a common border cue from all 52 cells of the selected pinned sprite,
then follows the bounded local `matchTemplate` and peak-suppression pattern of
`anchor_registration.py` in four seat bands. Search is capped at 13 candidate
corners per seat. The existing full-layout geometry in
`bridgit_primary_compat.py` requires four complete suit runs and 13 cards,
so it cannot be used for partial-card corner detection. The same 52-card
matcher identifies discovered corners and searches bounded pixel offsets.
Manual `CardCorner` values remain a synthetic-test and retrospective-smoke
input, not a `REAL_SHADOW` input.

For `REAL_SHADOW`, the adapter checks each frame's upper-right board tile with
`read_board_number` and requires automatic corner discovery. Two matching
board-read metadata fields and the episode/board-occurrence boundary remain
required. Without source video and PTS, the adapter cannot prove those two
metadata reads came from independent frames. It does not activate canonical
output. A measured non-native card size fails `CARD_SCALE_UNSUPPORTED` rather
than being silently resampled.
Each adapter result includes per-frame `detector_receipts` with the verified
frame/source hashes, timestamp, pixel board read where applicable, detected
box coordinates and unknown observations. Coordinates are in the local PNG;
this shadow path performs no crop or affine registration of its own.
