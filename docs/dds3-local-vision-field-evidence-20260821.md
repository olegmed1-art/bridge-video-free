# DDS3 P1 bounded local vision field evidence — 2026-08-21

## Scope

This field check evaluates `local_tesseract_federation_yellow_v1`, a **local/free and fail-closed** pixel extractor for the yellow Israel Bridge Federation diagram family. It is not evidence that arbitrary screenshot layouts are solved.

The extractor receives the original rasterized PNG bytes, fingerprints the bytes with SHA-256, locates the visible yellow board/compass geometry, OCRs the four hands plus Board/Dealer/Vulnerability with local Tesseract, and emits `ScreenshotDealObservation` only after exactly 52 unique standard cards / 13 cards per hand are present. It never repairs a missing card by deck complement and never derives Dealer or Vulnerability from the Board number. Rejected images do not reach DDS3.

Canonical truth remains independent source PDF vector text produced by the already-merged real-corpus builder. DDS3 is not used to create, repair, or choose the vision truth.

## Real-image field result

The private/local corpus contains 60 accepted real federation board images from three source booklets (`sim-6.26.pdf`, `sim-7.26.pdf`, `sim-8.26.pdf`), with truth and image SHA-256 provenance established before this extractor evaluation.

| Metric | Result |
|---|---:|
| Real valid images | 60 |
| Exact deal + exact Board/Dealer/Vulnerability | **42/60 (70.0%)** |
| Accepted but wrong | **0** |
| Fail-closed valid-image rejections | 18/60 (30.0%) |
| Precision among accepted images | **42/42 (100%)** |

The important safety result is `wrong_accepts=0`: the current extractor prefers rejection over producing a numerically valid but misread deal. The 30% rejection rate is still too high to call the extractor general or complete.

## Negative checks derived from real pixels

Five corrupted variants of a real accepted board were evaluated. All **5/5 were rejected** rather than repaired:

- strong blur;
- crop removing required layout evidence;
- one rank occluded to produce an incomplete/51-card observation;
- a duplicated rank glyph producing a 52-card-but-51-unique deck;
- a partially occluded/ambiguous rank.

These are rejection checks, not synthetic substitutes for the 60-image real corpus.

## Runtime boundary

The authenticated DDS3 runtime adds an explicit `image_dd_table` operation. It accepts base64 JPEG/PNG/WebP bytes, validates the real image signature and SHA-256, invokes only the bounded local/free extractor, then performs the existing 52-card validation and pinned DDS3 calculation. There is no paid/cloud vision fallback and no alternate numerical solver. Unsupported layouts return a 422-style fail-closed error; DDS3 is not invoked.

## Status against issue #236

This closes a meaningful part of the raw-image gap for **one proven layout family**: real pixels can autonomously produce a strictly validated `ScreenshotDealObservation` and then DDS3, with measured real-image precision/rejection behavior.

It does **not** close the full P1/Definition-of-Done requirement for arbitrary screenshots. Remaining work is broader real-layout coverage (the audit target is at least five layout families), improvement of valid-image recall without weakening `wrong_accepts=0`, and field metrics per additional family. Issue #236 must remain open until that broader evidence exists.

No source PDF, rendered corpus image, private Drive identifier, or participant data is committed in this public evidence.
