# DDS3 screenshot end-to-end contract

The user-facing operation is `screenshot -> result`. The mathematical engine remains DDS3-only.

## Vision adapter output

A vision-capable caller MUST extract, when visible:
- `board_number`
- `dealer`
- `vulnerability`
- all four hands N/E/S/W, suits S/H/D/C
- optional table metadata (pair/team/player names, contract, declarer, lead, result, auction)
- per-field `confidence` and `source=screenshot`

It MUST NOT estimate DD tricks, par, best play, or repair uncertain cards by bridge inference.

## Gate before DDS3

`ScreenshotDealObservation.canonicalize()` must pass before DDS3 execution. It verifies the complete 52-card deck. Board-derived dealer/vulnerability are allowed when those fields are absent. If observed values conflict with board-derived values, the observed values are preserved and explicit warnings are returned.

If any card is unreadable/ambiguous or the deck is incomplete/duplicated, the adapter must fail closed and request a corrected image or explicit card value. DDS3 is not invoked.

## DDS3 output

Only after validation, call `solve_screenshot_observation()` / `compute()`. Numerical DD values and par originate exclusively from the pinned DDS3 engine. No model, web solver, heuristic, or alternate solver fallback is permitted.

## Standard user-facing pipeline

1. Receive screenshot/image.
2. Vision extracts `ScreenshotDealObservation` without solving bridge.
3. Validate board metadata and 52 cards.
4. Call DDS3.
5. Return board metadata + 5x4 DD table + par + provenance.
6. Optionally pass the DDS3 JSON into a downstream teaching/analysis algorithm.
