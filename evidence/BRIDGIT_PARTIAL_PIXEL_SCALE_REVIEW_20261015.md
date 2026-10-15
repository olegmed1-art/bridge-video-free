# Native-size partial-pixel regression

The independent review of `40f43948` found that its fixed 23×43 corner and
50% white gate rejected unoccluded cells at most pinned Gambler sizes. The v2
observer derives the corner window from each validated sprite's native width
and height. It compares full-colour source corners and checks preservation of
the winning template's white pixels. A uniquely byte-exact source corner has
an explicit identity path; non-exact pixels still face score and margin gates.

With `BRIDGIT_PINNED_GAMBLER_ASSET_ROOT` set to the locally verified eight-PNG
directory, `test_all_52_original_cards_and_unknown_controls_at_each_pinned_size`
ran for variants 1–8. Each variant loaded the fixed SHA-256 sprite, decoded all
52 cells and rendered every native cell at its exact size. Results: **416/416
open cells accepted**, **0 wrong labels**, **0 unknown open cells**. In each
variant a covered corner, a blank corner and an incomplete ROI were separately
tested: **8/8 `CORNER_OCCLUDED`**, **8/8 `CARD_UNCERTAIN`**, **8/8
`CORNER_INCOMPLETE`**, with **0 filled cards**. These are controlled source-cell
checks, not a measure of video accuracy.

The same 16 unverified manual coordinates from the owner-provided single frame
were rerun without changing the coordinates. The observer returned 9 values:
N `TC JD`; E `AD AS`; S `TH JS`; W `JH 5D 4S`. Seven remain unknown: six
`CARD_UNCERTAIN` (indices 0, 3, 4, 5, 9, 10) and one `CORNER_OCCLUDED`
(index 13). The exact frame SHA, slots and scores are in
`BRIDGIT_PARTIAL_PIXEL_SINGLE_FRAME_SMOKE_20261015.json`. This does not
validate manual slot geometry, board identity, source PTS, adjacent-frame
fusion or the 24-case/4-session holdout.
