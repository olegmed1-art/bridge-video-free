# Video 3.1 FREE — hidden-hand and speech-only card prohibition

Date: 2026-08-30

Change ID: `video31-hidden-hand-prohibition-v1`

Governance mode: `ASSURED`

## Decision

Video 3.1 FREE accepts a card identity only when it is present in the bounded
visual/human evidence contract. The following operations are prohibited:

- computing the fourth hand from the other 39 cards;
- filling any missing card from the deck complement;
- creating a card from teacher or student speech;
- treating two non-visual suggestions as a visual observation;
- publishing a hidden or inferred card as `OBSERVED`, `HUMAN_VERIFIED`, or a
  standard PBN `Deal` card.

Speech may corroborate an already observed visual card or expose a conflict.
When no matching visual card exists, the declaration remains review evidence
with `accepted_as_observation=false`. Missing cards remain `UNKNOWN`.

## Corrected implementation boundaries

- `bridge-video-deal-v4` rejects `derive_fourth_hand=True`.
- `bridge-video-frame-v3` defaults to no derivation and cannot opt into it.
- native frame fusion and multi-frame tracking preserve only observed cards.
- speech fusion never adds a card to a hand.
- deal-review PDF v2 displays unknown seats and reports
  `hidden_hand_reconstruction_performed=false`.
- standard PBN remains gated separately on 52 directly visual or explicitly
  human-verified cards.

## Superseded claim

The r25.16 review-PDF evidence note permitted exact 39-to-13 deck subtraction.
That claim is retained only as historical provenance and is superseded for all
new Video 3.1 FREE subject results. Existing artifacts containing `DERIVED`
cards are review-only legacy artifacts and cannot establish card-recognition
accuracy, a complete deal, or production parity.

## Verification requirements

Regression must prove:

1. 39 observed cards produce 39 cards, not 52;
2. a single visually exposed fourth-hand card remains the only known card in
   that hand;
3. speech naming an absent card produces `REVIEW`, not a card;
4. speech matching an observed card is stored only as corroboration;
5. the PDF contains `UNKNOWN` and no `DERIVED` hand;
6. the explicit legacy derivation switch fails closed.

This change does not activate recognition, change the production route, run a
video, or promote any result to SCHOOL CANON or Student Profile.
