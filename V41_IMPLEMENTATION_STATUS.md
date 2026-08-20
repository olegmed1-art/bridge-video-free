# v4.1 implementation status

Status: **OPERATIONAL — semantic decision-window layer**.

## Scope

v4.1 tightens semantic decision-window linking over an already completed master. It does not change ASR, diarization, r29 identity mapping, source media, bridge canon or methodology authority.

## Evidence Gate

Deterministic regression and authority/cost guards passed before promotion. The completed-master field Evidence Gate for job `005ebee4db6f76823cd9058fa66d01ba` passed with:

- quality method: `diana-quality-v4.1`;
- complete evidence-backed Learning Interactions: **2**;
- partial Learning Interactions: **49**;
- methodology readiness: `METHODOLOGY_READY`;
- interaction windows: `2285.79–2394.8` and `4456.65–4519.59` seconds;
- each new complete window uses explicit acoustic-role support, 12/14 evidence refs respectively, and does **not** infer bridge correctness from follow-up;
- transcript segments: 582;
- acoustic speaker labels: 553;
- acoustic-role mapped segments: 553;
- semantic fallback role segments: 1;
- role-without-acoustic-speaker segments: 0;
- verified/partial/unknown boards: 0 / 0 / 48;
- source untouched: true;
- raw ASR mutated: false;
- heavy video reprocessed: false;
- paid AI API / paid cloud: 0 / 0;
- canon, curriculum, methodology activation and production student-profile writes: denied;
- field receipt Drive ID: `1RwIDqYZr-sBgMii0sTxLg7L65Jgd-_YV`.

## Normal production-path verification

After merge, the ordinary `.github/workflows/bridge-video-3.1-free.yml` path was triggered again for the same completed job with database persistence disabled.

GitHub Actions run: **32386985083**.

The terminal-receipt preflight correctly skipped free-runtime installation, Drive OAuth preflight for heavy processing, FREE-GUARD and `Process one opaque Drive job`; `Completed job no-op` passed. Routing then ran the normal production compatibility wrapper `diana_longitudinal_postprocess_v3.py`, which now imports v4.1. The longitudinal step completed successfully and wrote a new semantic generation without reprocessing media or ASR.

Production receipt:
- Drive ID: `1fJtrsWGqpRLgT-_HEEVy5OfLXce_XAd6`;
- digest: `bd1cbc8d252f`;
- schema version: 4;
- quality method: `diana-quality-v4.1`;
- readiness: `METHODOLOGY_READY`;
- complete / partial Learning Interactions: 2 / 49;
- source untouched: true;
- heavy video reprocessed: false;
- database staging: `NOT_REQUESTED`.

Production summary Drive ID: `1WPJl7qnYLWIg8QKhCKo9k8qA08VkUbb3`.

## Remaining limitation

Board reconstruction is **not** declared solved for this master. All 48 deal candidates remain `BOARD_UNKNOWN` because the completed evidence does not contain a sufficient exact explicit board identity/card set for safe fragment merging. v4.1 must not invent or merge boards by time, topic or board-number proximity alone.

Named identity remains a separate r29 Evidence Gate. v4.1 may use teacher/student role evidence but must never turn role, filename, lesson label, speaking duration or invitation metadata into a named-person claim.
