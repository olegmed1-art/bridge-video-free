# Video 3.1 independent card channels — 2026-08-30

Verdict: **IMPLEMENTED_NOT_PROVEN / SHADOW_ONLY**.

## Finding in PR #842

PR #842 keeps rank and suit recognition separate, but its `GraphicCardBackend`
creates the final card by concatenating those two values. It therefore does not
implement the separately required full-card recognition channel. Its bounded
field result also remains FAIL (`TP=2`, `FP=0`, `FN=9`, recall `0.1818`).

## Candidate contract

The current candidate accepts a visible card only when all of these conditions
hold:

1. rank, suit and full-card observations are all valid;
2. each channel passes an immutable confidence floor;
3. each channel identifies a distinct recognizer/source;
4. each channel is supported by at least two distinct frame SHA-256 values;
5. the independently recognized full card equals the rank+suit composition;
6. a valid visual box is supplied for the separate native N/E/S/W geometry step.

Missing, weak, temporally unstable, non-independent or conflicting channels
produce no card. Transcript and colour fields cannot fill a missing visual
channel. Colour may only constrain the red/black suit family; shape still has
to distinguish H/D or S/C.

The result is permanently marked `SHADOW_ONLY` and
`production_activation_allowed=false`. No runtime route, media, template,
threshold, Drive artifact or production setting is changed by this candidate.

## Remaining proof

- an independent full-card visual recognizer has not yet been selected;
- human-labelled train/template and holdout frames are still absent;
- no new real-video metrics were produced;
- the quality gate remains `INCONCLUSIVE` until #421/#742 evidence exists.
