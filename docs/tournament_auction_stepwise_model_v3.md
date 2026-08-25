# Tournament Analyzer v3 — stepwise canon-aware auction modelling

## Purpose

Model a plausible auction without back-solving from the final contract and without using hidden hands when selecting a call.

## Mandatory call-by-call procedure

For every turn:

1. Expose only the acting hand and the public auction prefix.
2. Compute objective hand facts (HCP, 4 suit lengths).
3. Generate/consider calls only from information legally available to that player.
4. Check each proposed call against the School canon when a canonical rule is available.
5. Classify the proposed call:
   - `CANON` — explicit rule + evidence reference exists;
   - `MODEL` — bridge-reasoning hypothesis, not claimed as the School system;
   - `UNKNOWN` — canon is insufficient to choose safely.
6. Append the call to public history and only then advance to the next player.
7. Never use the known final contract to choose earlier calls.
8. Never promote `MODEL` or `UNKNOWN` to a student error or methodology conclusion automatically.

## Current confirmed constraints used by the model

Only rules with explicit authority may be marked `CANON`.

- Teacher-confirmed in interactive review: a response `1H` after `1D` shows **4+ hearts**.
- Course/video evidence: a new suit response on the second level requires approximately **11+ HCP** and is forcing in the documented context.
- Course/video evidence: when responder cannot bid a new suit on the second level for lack of strength and has no suitable major available on the first level, `1NT` is used in the documented lesson as a 6–10 type response without a four-card major.
- Course/video evidence: after `1S`, `2H` in the documented School context shows **5 hearts**; this is scoped to that sequence and must not be generalized to every heart response.

These constraints are intentionally narrow. Missing opening priorities, rebid structures, raises, reverse rules, game tries, slam methods, competitive meanings, or forcing status must stay `MODEL`/`UNKNOWN` until bound to canonical School evidence.

## Board 15 checkpoint

Tournament 30041 board 15 is encoded only through the verified prefix:

`S: P — W: 1D — N: P — E: 1H`

- `S: P` — MODEL.
- `W: 1D` — MODEL. The hand is 13 HCP, 4-3-4-2; `1NT` 15–17 is excluded, but exact minor-opening priority still needs a canon binding.
- `N: P` — MODEL.
- `E: 1H` — CANON for the confirmed `4+ hearts` condition; East has seven hearts.

The model deliberately stops here rather than inventing West's rebid or East's later game decision.

## Safety invariants

- `hidden_hand_access_allowed_for_call_selection = false`
- `use_final_contract_to_backsolve = false`
- `automatic_student_error_attribution_allowed = false`
- `CANON` requires `canon_rule_id` + `evidence_ref`
- a non-CANON step is not allowed to claim a canonical rule id
