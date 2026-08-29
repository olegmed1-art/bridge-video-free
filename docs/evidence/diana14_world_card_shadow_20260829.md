# Diana 14 world-card SHADOW field probe

Date: 2026-08-29

Verdict: **FAIL** for the current LGD gen3 backend/gates. No production or
canonical promotion is justified.

## Exact binding

- source Drive id: `1Hs9rItDk1tsU0nL7vl7A1HF9NHgBU8dr`
- reused completed isolated field run: `33252677308`
- input: 59 evidence keyframes; the source video was not downloaded or rerun
- model: `sroot/lgd-cards-gen3` ONNX
- model SHA-256: `8b767cdfed2c8e954a9134013ac3d2f2c53be048768d559675be01277a8a8fd1`
- result scope: `SHADOW_ONLY`
- canonical promotion allowed: `false`

## Result

| Measure | Result |
| --- | ---: |
| Frames | 59 |
| Strict observations at 0.90 | 0 |
| Diagnostic unique suggestions at 0.50 | 1203 |
| Duplicate detections | 352 |
| Cross-seat conflicts | 31 |
| Derived fourth hands | 0 |

The diagnostic count is not a recognized-card count. It includes candidates
from changing play states and false/duplicate corner detections and is retained
only to diagnose calibration and localization.

## Human-labelled bounded check

Two South hands visible in the immutable evidence frames were labelled and
compared against the diagnostic 0.50 output after within-seat card-code
deduplication.

| Frame | TP | FP | FN | Duplicate/ambiguous detections |
| --- | ---: | ---: | ---: | ---: |
| `frame-0000-000000.jpg` | 12 | 0 | 1 | 4 |
| `frame-0030-003600.jpg` | 10 | 2 | 3 | 4 |
| **Total** | **22** | **2** | **4** | **8** |

- precision: `22 / 24 = 91.67%`
- recall: `22 / 26 = 84.62%`
- required gate: precision `>=99.5%`, recall `>=95%`, seat errors `0`, false
  complete deals `0`

The remembered 90+ behavior is partially reproduced on the first hand
(`12/13 = 92.3%` recall with zero unique false positives), but it does not
generalize to the second labelled hand and does not survive the strict 0.90
confidence gate.

## Fail-closed outcome

- no diagnostic suggestion became an `OBSERVED` fact;
- no three complete 13-card observed hands existed;
- `39 OBSERVED -> 13 DERIVED` was therefore not invoked;
- no `bridge_positions.jsonl` canonical input was created;
- no next video was launched;
- Roboflow was not called because no API credential was configured.

## Required next correction

1. Calibrate LGD confidence on a larger Diana/Bridgit gold split instead of
   lowering the acceptance gate blindly.
2. Detect and merge the two physical card corners before card classification.
3. Add the independent rank and suit channels; LGD remains only the full-card
   reference channel.
4. Repeat the frozen holdout and require the repository 99.5%/95% gate before
   any accepted SHADOW observation or fourth-hand derivation.
