# Gambler classic original-asset match evidence

Date: 2026-09-12

Status: SHADOW_ONLY / successor evidence. Historical `RECOGNIZER_HOLDOUT_V1` is unchanged.

## Source

Original client resources were supplied outside the repository from the installed Gambler client path:

`res/pics/cards/classic/{1..8}/all.png`

The proprietary PNG bytes are **not committed**. Runtime use is source-bound by caller-supplied path + SHA-256.

Native classic variants:

| Variant | Sprite px | Card px | SHA-256 |
|---:|---:|---:|---|
| 1 | 468x196 | 36x49 | `627cc3170c39d19304d81b54e92144714bddb7b3917e21c66bd221c31bd82390` |
| 2 | 637x264 | 49x66 | `53b9a93fb25c7f4efd367b5219ae664c8b1b7b2e1243df5383f37df779ef2c90` |
| 3 | 923x384 | 71x96 | `0eee20f34a8fe380c06231f93129c5ff32acd2a31d790797e06f12b436dd2d8f` |
| 4 | 1157x480 | 89x120 | `c59dd0cafb91506fdbd5513bf217061f816ea4d3fb956982f79a8859022c9c57` |
| 5 | 1417x588 | 109x147 | `52eb9c97c9685758f48beb6e84013e9289bdb97f8a31b4ae8e46a0ecee3a37fb` |
| 6 | 1560x720 | 120x180 | `eb47ec363bc2824587c5ab0f3897ae52c5d98e26a8403d9605353c3cbdf99517` |
| 7 | 1950x900 | 150x225 | `c653e77694592f2059196f17c26f817a7e0c5a2ad6162014cc5ca5a5577c2695` |
| 8 | 2470x1140 | 190x285 | `22955238debcaefb4ccd797f5a1a3fb4590b3646e6061c314ba122cfa19689d0` |

Sprite layout is 13 rank columns `A K Q J T 9 8 7 6 5 4 3 2` and four suit rows `C D H S`.

## Saved-frame comparison

No new media processing was run. A previously saved 1920x1010 review frame was compared locally to all eight unscaled native sprites with OpenCV `TM_CCOEFF_NORMED`.

For the visible `2S` card, variant 5 matched at `0.9965` at its native 109x147 size. Other unscaled native variants remained below approximately `0.50` for that card.

Additional high-confidence native variant-5 full-card matches in the same saved frame:

- `TC`: `0.9975`
- `4C`: `0.9969`
- `4D`: `0.9920`
- `2D`: `0.9914`
- `TH`: `0.9917`
- `2H`: `0.9915`
- `8S`: `0.9962`
- `4S`: `0.9961`
- `3S`: `0.9830`
- `2S`: `0.9965`

The original-asset rank-crop bank classified all ten of those >=0.97 full-card matches to the correct rank. Example `2S` rank score: `0.9746`, with the next-best rank materially lower.

This establishes that classic variant 5 is the native Gambler artwork/scale present in the saved frame, not merely a visually similar deck.

## Successor integration

The successor adapter `bridge_vision.bridgit_gambler_rank_layout`:

1. validates the external sprite by SHA-256 and exact known dimensions;
2. selects the native variant from verified registered card width/height, never from video resolution;
3. keeps the human-reviewed UI reference for layout/anchor registration;
4. replaces only its 52 rank-template crops with original-client Gambler rank pixels;
5. invokes the existing fail-closed shadow rank-layout recognizer;
6. emits `GAMBLER_CLASSIC_ORIGINAL_ASSET` provenance including variant and sprite hash.

Mouse cursor input is not used. Hidden-hand reconstruction and canonical promotion remain forbidden.
