# Autopilot IBF structured evidence v1

Status: SHADOW IMPLEMENTATION SLICE. Tracks issue #1013.

## Purpose

Upgrade the verified IBF source-retrieval pilot from page hashes and row counts to a
complete, deterministic and de-identified source artifact for every played board.
This slice does not promote Autopilot to production and does not change School Canon.

## Extracted source facts

For each official `board.php` page the extractor retains:

- all four 13-card hands, with a 52-card uniqueness gate;
- dealer and vulnerability, cross-checked against the official DDS source link;
- the complete double-dummy trick table and Par score published by IBF;
- every field row: anonymous seat references, score cells, contract, opening lead,
  both matchpoint percentages, target side and adjusted-result marker when present;
- hashes for the board page and DDS source URL.

Pair names are intentionally excluded from the structured artifact. Source hashes,
event/round/seat identity and seat references are sufficient for reproducibility.

## Analysis boundary

The artifact sorts boards by the target pair's published percentage for review, but
never treats a low score or double-dummy difference as proof of player error. IBF does
not publish the auction or card-by-card play on these pages. Therefore bidding,
competitive decisions, defense and declarer play remain explicitly unobservable;
the opening lead is observed but not evaluated in this slice.

No model call or methodology/canon rule is permitted in this extraction layer.

## Live shadow evidence

On 2026-09-01 the parser was checked read-only against all 24 boards of the verified
latest participation for player 15031: event 29692, round 9, seat 4, dated 2026-08-27.
It extracted 24 complete deals, 24 DD/Par tables, 96 field rows and one `AP` adjusted
row. The canonical 24-board artifact was 44,022 UTF-8 bytes with SHA-256
`c596d02d52a7265282dd4b55018d6dff5e2f44669ea0ac3fff7ee92db2b247b9`.

## Promotion boundary and rollback

This commit is parser and evidence-contract code only. It is not yet wired into the
Oracle worker or stored in Neon because the existing completion manifest is limited
to 8 KiB. The next slice must add a bounded artifact-retention RPC/table on the same
temporary Neon branch, then stage and canary an exact worker revision. Rollback is
removal of that additive temporary-branch migration and continued use of worker v16.

Production Neon, `main`, video, training, DDS3 execution and BEN remain out of scope.
