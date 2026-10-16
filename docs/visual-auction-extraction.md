# Review-only auction extraction from pixels

Status: source prepared and statically reviewed; tests NOT RUN; real-video accuracy
NOT EVALUATED. No default-runtime activation or production promotion.

## Scope and dependencies

Base: main 62f2c3c71edf1065f2c917892c3af3533516070d.
This patch adds files only. Existing main files, database and server workflows are
not modified. The candidate core and its regression fixtures come from PR2103
74c98215d433c0383a8cc1021b4a24f9caade8fe. Its unrelated r26.5 reconstruction
changes are excluded. The unchanged laws-only prefix validator and six contract
tests come from PR940 14fde4908118a0c3a732a4211bda8690530045de; that PR's observer
requiring already-labelled two-channel input is not used as fake pixel recognition.

## Actual data path

The existing candidate video scan calls AuctionObserver on each decoded scan frame,
before full-card geometry gating. Each configured rectangular cell is cropped from
pixels, converted to grayscale, and matched against reference-image call exemplars
using the existing rank_layout._similarity. Acceptance requires correlation >=.97,
winner margin >=.05 and mean pixel error <=8. These are uncalibrated engineering
thresholds, not probabilities or measured accuracy.

Blank cells require uniform pixelwise agreement with calibration; ambiguous cells
remain UNREADABLE. A blank never becomes PASS. Only the configured vocabulary is
recognizable, including separately labelled PASS/X/XX and level/strain bids.
No manually recognized call sequence is passed into the pixel reader.

Seat labels require matching visible calibrated header marks. Prefix ordering
requires all four clockwise seat headers, a verified first-row marker and contiguous
READ cells starting in the first row. Gaps, scrolling and unknown seats retain raw
cell observations and visible fragments; no missing calls are filled. Laws-only QC
checks the sequence, and never reconstructs it.

A terminated prefix needs two distinct full decoded frame hashes separated by at
least 500 ms. This is temporal repetition, not independent OCR agreement; a cursor
elsewhere can make two full frames distinct. The latest observed state must match
the complete sequence. A rewind or loss of prefix context prevents completion within
that occurrence, including when an earlier identical frame reappears.

Occurrences use source video hash, ordinal, first timestamp and a visible context
anchor hash. A changed/missing anchor starts a new occurrence. This cannot prove
actual deal identity when the same anchor persists or a transition is missed.
Deal links remain UNVERIFIED_TEMPORAL_OVERLAP; auction/contract/declarer are not
written into a hand record. Standalone auction observations retain their own QC.

Speech mentions are separate NEGATED/HYPOTHESIS/UNVERIFIED_MENTION records, with
segment text/time and no inferred seat/board. This parser covers explicit Latin
bid notation and PASS/X/XX plus Russian pass/double labels, not unrestricted
spoken Russian bidding. It never creates factual visual calls.

## Evidence survives the temporary job directory

Raw offline results retain PNG paths and hashes. The candidate adapter replaces
temporary paths by PDF_ATTACHMENT locators, including within nested call evidence
and QC. The existing final-PDF embed hook is wrapped only in the explicit candidate:
all observed auction PNGs, calibration profile JSON and reference image are embedded,
with pre-write and post-write SHA256 verification. The master carries the attachment
manifest. A missing/tampered attachment or mismatched job/manifest stops export.
Auction-only/no-hand/no-episode output therefore has an evidence retention route;
it does not depend on the report selecting a screenshot for a visible page.

## Configuration and unsupported inputs

Runtime opt-in: 3.1-free-r26.3-auction-candidate3; no run() entry point is added.
The new revision prevents reuse of candidate2 completion markers. Changed profiles
under the same revision require a fresh job/output identity.

Supply optional auction_profile_path to the raw candidate, or set the candidate-only
BRIDGE_AUCTION_PROFILE_PATH. For sealed offline inputs, the same object can be placed
under "auction" in the existing rank profile. Absence reports NO_AUCTION_PROFILE.

Schema bridge-auction-cell-profile/v1:
- frame_size: exact [width,height].
- reference_pixel_sha256: AuctionObserver.pixel_sha-compatible digest of the exact
  reference image: SHA256(str(image.shape).encode() + image.tobytes()).
- cells: row-major, nonoverlapping [x,y,w,h] crops; four columns and 1..20 rows.
- headers: four null or {seat:"N"|"E"|"S"|"W",box:[x,y,w,h]} entries.
- templates: 2..40 {call,box} exemplars from that same reference image.
- blank: uniform blank crop, same dimensions as every cell/exemplar.
- start_marker: nonblank visual indicator present only at the first auction row;
  a static panel title is not a valid calibration.
- board_anchor: visible stable within-board context, not the changing auction.

Only a calibrated fixed four-column table is supported. No real Gambler auction
profile is shipped. There is no automatic layout discovery, resizing, rotation,
generic-font OCR, arbitrary seat-name OCR or scrolling reconstruction. The reference
must actually contain the needed labelled exemplars. Unsupported dimensions and
decode gaps report coverage loss. Calibration labels themselves still need review.

Evidence is limited to 128 unique snapshots /128 MiB PNG bytes; exhaustion suppresses
completion. The inherited card-candidate scan cutoff is also reported as truncation.
Exact anchor hashing and full-frame uniqueness require real-video qualification.

## Comparison runner and validation

PR2120 at 1243f2feab9998ca62dfa77a3452881f440657c7 is not vendored into this patch.
Its unchanged offline audit is used by a proposed pixel-path regression; the matcher
uses no external OCR process or network. The original runner's runtime whitelist
rejects candidate3: a small, separately reviewable companion change is required
before using its full loader with this revision. Do not claim full compatibility
from the offline-audit test alone.

The prepared workflow checks pinned main and the proposed candidate, runs existing
card controls, auction pixel tests, real synthetic lossless-video decode, temporal
regressions, PDF evidence retention after source cleanup, calibration hashes,
tamper rejection and job isolation. It supplies the pinned runner root and rejects
skipped new tests. None of these proposed tests have been executed.

Static review found and corrected stale completion after rewind and evidence loss
on temporary-directory cleanup. Remaining gates: run the complete synthetic suite,
calibrate on authorized real frames, freeze gold and profiles before evaluation,
measure false completion/seat/occurrence errors, and coordinate publication.
