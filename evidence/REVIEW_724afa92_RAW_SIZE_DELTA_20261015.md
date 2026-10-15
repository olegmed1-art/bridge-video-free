# REAL_SHADOW raw-size binding: review delta

Base: `724afa92dbb92328853410dc92dd931e9fc6a022`. This delta addresses the
metadata-binding defect in the independent review at
`C:/Users/Oleg/Documents/Codex/2026-10-11/task-5/ocr-review/REVIEW_724afa92.md`.

For each `REAL_SHADOW` sample, the adapter now decodes the frame after the
existing byte-hash check, compares both declared raw dimensions with decoded
pixels, and compares concrete observer geometry with those pixels. Mismatches
fail before board-number OCR, pixel observation, or any detector receipt.
`FRAME_IDENTITY_UNPROVEN` remains the error for missing, unknown, noninteger,
boolean, zero, or negative size metadata. `RAW_FRAME_SIZE_MISMATCH` marks a
well-formed but false declaration; `OBSERVER_FRAME_SIZE_MISMATCH` marks observer
geometry disagreement. Successful receipts record the declared and decoded
sizes, frame/source hashes, timestamp, coordinate space, pixel board read,
detected corners/boxes, and unknowns. The `SYNTHETIC_REGRESSION` contract still
supports byte-fixture frames and records no decoded size.

Red regression: the pre-fix adapter accepted generated 1680x934 PNG frames
declared as 1x934 or 1680x1. The review's original read-only probe, using the
saved 1680x934 real frame and declared 1x1, now exits with
`RAW_FRAME_SIZE_MISMATCH`. Green regressions cover both dimensions, malformed
and unknown metadata, mismatched observer geometry, and size-bearing receipts.
No recognition score, confidence threshold, sprite, or canonical profile was
changed.

The limited single-frame observations from review `724afa92` remain distinct:
22 raw observer values, 20 after the native `.80` confidence gate, and 28
observer unknowns. JPEG Q95 gave 21 raw values on 49 candidates. Those figures
are diagnostic; they are neither paired-frame evidence nor the 24-case/four-
session holdout result. The original AVI, independently verified source hash,
authentic PTS, and second adjacent frame remain unavailable here. Next, bind
those inputs to two separate decoded frames from one verified board occurrence
and run the untouched holdout before any promotion.

Validation on this delta: adapter tests `17 passed`; related local regression
set `162 passed, 4 skipped`; `git diff --check` clean. The four skips are not
holdout passes. No external publication or deployment was performed.
