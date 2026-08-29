# 3.1 FREE r25.16 — evidence-preserving deal-review PDF

Date: 2026-08-29

Change ID: `3.1-free-r25.16-deal-review-pdf`

Governance mode: `ASSURED`

## Purpose and scope

The stable `3.1 FREE` master PDF now appends one landscape review page for each
deal already present in `master_analysis.json`. Each page keeps the hash-bound
source screenshot, observed or explicitly human-verified cards, a separately
labelled reconstructed layout, and the auction together.

This is a presentation and provenance change. It does not enable a detector,
lower recognition thresholds, alter bridge methodology, activate school canon,
or convert SHADOW observations to production facts.

## Evidence and decision

- The school director visually confirmed the accuracy of the proposed deal
  layout for the reviewed Diana 14 example.
- The canonical `bridge-video-deal-v3` contract remains the only card
  normalizer and conflict checker.
- Reconstruction is permitted only by exact deck subtraction when three
  complete, mutually consistent 13-card hands provide 39 unique cards.
- Missing cards, missing auctions, an invalid screenshot hash, and unavailable
  evidence remain visible as unknown or unavailable.
- `HUMAN_VERIFIED`, `OBSERVED`, and `DERIVED` are printed as distinct evidence
  classes. The page states that evidence review is not automatic canon
  promotion.

## Checks and independent assurance

- Unit and integration regression verifies partial human-verified hands,
  completed auctions, exact 39-to-13 reconstruction, cross-seat conflict
  rejection, screenshot SHA-256 binding, mixed portrait/landscape PDF pages,
  embedded `master_analysis.json`, and the no-deals compatibility path.
- ReportLab authors the page; PyMuPDF independently reopens, renders, inspects
  geometry and text markers, merges the appendix, and validates the final PDF
  (`I2`).
- Director visual confirmation of the page layout is retained as `I4` for the
  reviewed example, not as recognizer-accuracy evidence for unseen cards.

## Rollback

Revert the r25.16 route to `bridge_runtime_hardening_r25_15`, restore the
workflow revision pin to `3.1-free-r25.15`, and revert the call to
`append_deal_review_pages`. Existing r25.15 outputs remain immutable and valid.

## Remaining risk

The stable pipeline currently creates candidate deals from semantic episodes;
card identities appear only when upstream evidence has actually populated the
deal contract. The renderer never fills absent recognition evidence. Broader
automatic card-recognition accuracy remains subject to the separate dense
SHADOW field validation before any detector promotion.
