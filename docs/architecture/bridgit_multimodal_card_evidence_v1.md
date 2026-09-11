# Bridgit multimodal card evidence v1

Date: 2026-09-11

Status: SHADOW_ONLY library boundary; no production activation.

## Goal

Strengthen card recognition with two additional evidence families while keeping direct pixels authoritative:

1. theme-calibrated suit colour;
2. timestamped teacher speech from the Universal Video ASR timeline.

Mouse-cursor position is explicitly excluded from card recognition, speech binding, confidence, and ownership.

## Parallel processing

Audio transcription and visual recognition are parallel consumers of the same source-video clock.

```text
video
├─ audio -> ASR -> timestamped teacher speech
└─ frames -> hand/played-card visual observations
                    ↓
              multimodal fusion
```

Universal Video emits transcript segments with source-relative `start`/`end` seconds. Visual observers emit frame `timestamp_ms`. Fusion converts the ASR interval to milliseconds and compares it with visual frame timestamps; it does not pretend that an utterance belongs to one exact frame.

## Teacher speech

`bridge_vision.bridgit_teacher_asr_adapter` accepts only reliable ASR segments whose speaker structure identifies the teacher with sufficient confidence, unless the caller explicitly supplies a trusted teacher-only track. It does not invent a calibrated per-segment ASR probability.

`bridge_vision.bridgit_teacher_speech` maintains short-lived hand context and accumulates card claims:

- `у Севера дама` -> seat=N, rank=Q;
- later `пиковая` or `пиковая дама` in the same live context can complete the claim to QS;
- repeated independent mentions increase speech evidence;
- overlapping duplicate ASR fragments do not multiply support;
- explicit corrections (`нет`, `точнее`, `вернее`, etc.) retract the preceding live claim;
- a claim without a unique seat binding remains unresolved.

Speech never creates a card with no compatible visual evidence.

## Temporal binding

`bridge_vision.bridgit_teacher_timeline_fusion` uses an asymmetric interval around each spoken claim:

- up to 12 seconds look-back, because a teacher often plays a card and names it afterwards;
- up to 5 seconds look-ahead;
- frames during the utterance receive full temporal weight;
- repeated frames with identical card-region pixels do not multiply visual support.

A direct visual `card + seat` match inside this window may be labelled `HAND_PLUS_TEACHER_SPEECH` or `PLAYED_PLUS_TEACHER_SPEECH`.

If no matching visual observation exists, the speech claim remains unresolved. No card is added to the deal.

## Played-card ownership

The existing played-card observer recognizes current-trick cards and assigns `seat=N/E/S/W` from verified table geometry. Accepted observations use `source=PLAYED`. A played card therefore remains evidence for the original player's hand even though it is no longer physically inside that hand.

The autonomous reconstruction layer already accepts both `HAND` and `PLAYED` source observations. A played card is not counted as still held, but its ownership contributes to reconstruction of the original deal.

## Suit colour

Visible-hand and played-card observers already use red/black evidence for suit structure. The new generic `bridgit_suit_color` helper adds a theme-calibrated RED/BLACK classifier for fusion consumers. It compares foreground colour with verified profile references rather than assuming fixed absolute RGB values.

Colour is corroborating evidence, not sole suit identity:

- H/D must be compatible with RED;
- C/S must be compatible with BLACK;
- colour conflict can trigger review;
- colour alone cannot distinguish H from D or C from S.

## Fusion policy

`bridge_vision.bridgit_card_evidence_fusion` keeps visual evidence primary.

- Strong visual evidence cannot be overwritten by teacher speech; disagreement becomes `SPEECH_VISUAL_CONFLICT`.
- Strong colour conflict becomes `COLOR_VISUAL_CONFLICT`.
- Weak visual ambiguity may be resolved by strong repeated speech only when the spoken card is already a compatible visual candidate.
- Weak red/black ambiguity may be resolved by strong calibrated colour evidence.
- Speech without visual support remains unresolved.
- Cursor fields are ignored.

Possible provenance includes:

- `VISUAL`;
- `VISUAL_PLUS_SUIT_COLOR`;
- `VISUAL_PLUS_TEACHER_SPEECH`;
- `VISUAL_PLUS_SUIT_COLOR_PLUS_TEACHER_SPEECH`;
- `HAND_PLUS_TEACHER_SPEECH`;
- `PLAYED_PLUS_TEACHER_SPEECH`;
- `UNKNOWN`.

## Safety boundary

The fusion layer is shadow-only and sets/retains:

- `hidden_hand_inference_used=false`;
- `deck_complement_used=false` for the recognition/fusion decision itself;
- `mouse_cursor_used=false`;
- `canonical_promotion_allowed=false`.

It does not change School Canon, production routing, Neon, or Autopilot resources.
