# Automatic partial-corner shadow result

The existing `bridgit_primary_compat.horizontal_geometry_detail` requires
four suit runs and a 13-card fan. It cannot locate a partly occluded hand.
The new v3 geometry stage derives a shared border cue from the 52 cells of
the hash-pinned Gambler sprite and searches four bounded seat bands using the
same local template-search pattern as `anchor_registration`. The existing
52-card observer still decides the card value. `REAL_SHADOW` requires this
automatic mode and a fresh upper-right board-number read per frame; it also
retains the two-read metadata and episode boundary gates.

Independent transformations of pinned variant-5 source cells, with all 52
identities used only to select test pixels and score outcomes, produced:

| Input | Correct | Unknown | Wrong accepted |
| --- | ---: | ---: | ---: |
| Exact native cell | 52 | 0 | 0 |
| Card displaced +1 px x/y | 52 | 0 | 0 |
| JPEG quality 95 | 30 | 22 | 0 |
| Card resized +2%, native geometry retained | 23 | 29 | 0 |

The measured +2% scale is not a validated native variant. Supplying its
measured 111×150 card size to the observer fails `CARD_SCALE_UNSUPPORTED`.
Additional automatic-geometry probes of `8H` and `KS` succeeded at all eight
pinned native sizes. These source-cell checks are not video accuracy.

On the one retained owner frame, automatic geometry selected 50 corners and
emitted 22 card values, leaving 28 unknown. The former 16 manual smoke slots
had 9 values and 7 unknown; all 9 manual values were also found within 5 px
by automatic geometry. The extra 13 automatic values have no owner labels
and are **not** counted as correct. A +1 px translation retained 22 values;
JPEG quality 95 retained 21 and left 28 unknown. No accepted identity changed
at a corresponding location in either transformed frame. Full candidate
coordinates and observations are in
`BRIDGIT_PARTIAL_AUTO_GEOMETRY_SMOKE_20261015.json`.

The single-frame upper-right tile was read as board 1, but no independent
second frame, verified PTS, source-video bytes, episode or board occurrence is
available. No adjacent-frame fusion or 24-case/4-session holdout accuracy has
been claimed.
