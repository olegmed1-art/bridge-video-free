# Issue #733 — graphic-card recognition evidence

Date: 2026-08-29

Scope: test/shadow only. No production promotion, School Canon write, hidden-card
completion, missing-card inference, fourth-hand derivation, or subsequent-video run.

## Verified implementation boundary

- rank and suit observations are independent and are composed only after both
  explicit confidence gates pass;
- N/E/S/W is assigned by registered screen geometry, never supplied by the
  recognizer;
- incomplete, low-confidence, duplicate, conflicting and ambiguous observations
  fail closed;
- detailed evaluation reports exact card+seat TP, FP, FN, seat errors and
  ambiguous observations;
- all evaluation output is `SHADOW_ONLY` / `REVIEW`, with
  `canonical_promotion_allowed=false`.

## Real-frame evidence state

Two Diana 253 frames were previously inspected locally (including a frame near
3610 seconds), demonstrating stable panel geometry and useful rank crops. Raw
lesson frames were intentionally not committed. The repository currently has no
hash-bound, human-labelled Diana 253 or Diana 14 frame bytes, and therefore no
honest detector run can reproduce TP/FP/FN/ambiguous metrics from `main` alone.

The immutable manifest and byte-hash checks are present. The new evaluator is
ready to consume those verified cases without deriving any missing information.

## Status

`INCONCLUSIVE` for the real-frame acceptance gate. Deterministic safety and
reporting contracts pass, but real-frame accuracy remains unproved until the
approved local frames and their explicit human labels are supplied to the
hash-bound evaluation path.
