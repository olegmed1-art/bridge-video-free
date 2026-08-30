# 3.1 FREE r25.17 — source-bound deal evidence and PBN

Date: 2026-08-29

Change ID: `3.1-free-r25.17-source-bound-deal-evidence`

Governance mode: `ASSURED`

## Purpose and scope

The stable `3.1 FREE` route now has a universal intake boundary between a
card/auction observer and the already-merged one-page deal review.  A single
optional `BRIDGE_DEAL_EVIDENCE_<job_id>.json` file in the source video folder
may populate `master_analysis.json`, the deal-review PDF pages, and a sibling
PBN file.

The change does not approve, install, or register a pixel backend.  With no
valid evidence bundle, hands and auctions remain unknown exactly as in r25.16.
There are no video-name, student-name, table-seat, or Diana-specific runtime
rules.

## Evidence contract

- Schema: `bridge-3.1-free-deal-evidence/v1`.
- Exact source binding: Drive ID, byte size, and SHA-256 must equal the runtime
  source passport.
- Payload digest: canonical JSON excluding `payload_sha256` must match the
  claimed SHA-256.
- Scope: `SHADOW_ONLY`; canonical promotion and production activation are both
  explicitly false.
- Frame binding: every card or auction reference must match a local full-size
  frame by evidence ID, timestamp, path safety, and SHA-256.
- Machine card gate: independent rank, suit, and full-card channels, each at
  least `0.90`, plus at least two independent frame hashes.
- Human card gate: exact reviewer, method, UTC timestamp, verified seats, and
  reference-frame SHA-256.
- Card integrity: the canonical `bridge-video-deal-v3` contract rejects an
  invalid card, duplicate card, cross-seat conflict, or more than 13 cards.
- Auction mechanics: dealer-relative turn order, sufficient bids, legal
  doubles/redoubles, and legal termination are validated independently of any
  bidding-system meaning.

## Output policy

- The accepted evidence deals replace only unbound semantic deal candidates in
  the visible review surface; the unbound candidates remain preserved in
  `deal_candidates_unbound` inside the embedded master JSON.
- Partial cards use `X-Observed-*` and `X-Unknown-*` PBN tags.
- A standard `Deal` tag requires 52 directly observed or human-verified unique
  cards.
- Exact 39-to-13 deck subtraction is displayed only as `X-Derived-*` plus
  `X-Derivation "39_TO_13_DECK_SUBTRACTION"`.
- A standard `Auction` section requires a complete legally terminated auction
  that the evidence contract approved from at least two frames.
- PDF, PBN, AI_DONE, and METHODOLOGY_READY retain explicit hashes and never
  claim School Canon promotion.

## Checks and assurance

- Seat-agnostic machine and human observations are parameterized across all
  `N/E/S/W` positions.
- Negative tests cover source mismatch, canonical promotion, low confidence,
  insufficient frames, invalid cards, duplicate cards, and illegal auction
  order.
- PBN tests distinguish a 52-card observed deal from a 39-to-13 derived hand
  and verify complete-auction export.
- The existing PDF tests independently reopen and render the final PDF with
  PyMuPDF; the new mechanics validator is independent of the presentation
  layer.
- Stable production promotion requires green repository CI and an independent
  review of the exact branch head before merge.

## Rollback

Repoint `run_drive_3_1_free_generic.py` and the production workflow to
`bridge_runtime_hardening_r25_16`, then revert the optional evidence discovery,
PBN upload, and r25.17 contract files. Existing r25.16 results remain immutable.

## Remaining risk

The evidence intake and publication surfaces are implemented, but automatic
pixel recognition is still not approved for stable use.  A real backend,
profile, and independent labelled holdout remain necessary before an automatic
producer may be enabled.  The adapter is intentionally fail-closed until that
separate evidence exists.
