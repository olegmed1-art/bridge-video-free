# Bridgit visible-card temporal shadow V2

Date: 2026-09-11

Governance mode: `ASSURED`

Status: implemented as an opt-in shadow fusion boundary; no production route or pixel backend activation

## Purpose

Recorded Bridgit lessons commonly expose the north and south hands while the east and west hands remain face down. During play, individual cards from every seat become visible on the table. The complete-layout backend must not be used on those frames because it requires four simultaneously visible hands.

`bridge_vision.bridgit_visible_timeline` accepts only card observations already obtained from visible pixels. An observation identifies an exact logical seat, canonical card, source kind (`HAND` or `PLAYED`), confidence, timestamp, encoded-frame SHA-256, decoded-frame SHA-256, card-evidence-region SHA-256 and explicit deal identity.

## Acceptance boundary

- A `card + seat` pair requires at least two byte-distinct frames, decoded-frame-distinct pixels and card-evidence-region-distinct pixels.
- A losslessly re-encoded copy of the same pixels does not add support.
- A frame whose unrelated pixels changed but whose card evidence region is identical does not add support.
- Deal identity is explicit (`EXPLICIT_BOARD` or a separately gated `VISUAL_ANCHOR`); time proximity never joins deals.
- `HAND` and `PLAYED` describe visible pixel locations. Played-card ownership must come from verified geometry, not inferred turn order.
- A card observed at two seats, a hand above 13 cards, malformed hashes, duplicate cards within one frame, low confidence or cross-deal input fails closed.
- Unseen cards remain empty slots. The 39-to-13 deck complement and fourth-hand inference are prohibited.
- Every result is `SHADOW_ONLY`, has `canonical_promotion_allowed=false`, and cannot write SCHOOL CANON/WORLD.

## Invocation

```bash
python tools/bridge_vision_visible_timeline.py \
  --job-root /bounded/job \
  --input observations.json \
  --output visible-card-receipt.json
```

The command reads and writes only below the explicit non-root job directory. Input size, observation count and cards per frame are bounded. The output is written atomically with mode `0600` and contains a self-hash.

## Remaining field gate

This module is the temporal evidence layer, not a pixel classifier. A Diana-video run still requires a source-bound frame observer that emits the validated input records. Its template/label profile must be checked by the single human verifier (Oleg) before the first real recognition output is scored. No hidden hand may be reconstructed unless its cards are individually observed during play.
