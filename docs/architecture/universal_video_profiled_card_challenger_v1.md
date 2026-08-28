# Universal Video profiled card challenger v2

Date: 2026-08-28

Governance mode: ASSURED

Status: implemented for opt-in shadow/test use; no pixel backend approved; no production promotion

## Outcome

Universal Video now has a deterministic, interface-profiled decision boundary for a future card pixel recognizer. The boundary adopts the transferable principles found in Dealer4, BridgeSorter and TCG-AR without importing proprietary code, model weights, accuracy claims or special-card hardware.

The default Native Bridge Vision runtime is unchanged. With no explicitly injected challenger it still has zero detector families and returns `UNAVAILABLE`. The old BBO adapter remains separately opt-in.

## Adopted mechanisms

1. **Controlled interface profile.** Every backend call is bound to a human-verified profile ID, reference-frame SHA-256, reference dimensions, table geometry, complete rank/suit template hashes, all 52 full-card reference hashes and an immutable template-set hash.
2. **Teach boundary.** `build_teach_profile` requires an explicit `human_verified=True` attestation plus human-review provenance (`method`, reviewer, UTC timestamp and the verified reference-frame SHA). It never sets verification silently. It does not infer glyph labels. A missing symbol, duplicate template hash, unverified profile or changed hash fails closed.
3. **Frame registration.** The backend must supply a homography to the profile reference plus inlier count and inlier ratio. The deterministic layer validates the matrix, thresholds and profile reference before using a card box.
4. **Independent recognition channels.** Rank and suit are supplied separately through a declared glyph-channel ID. Their composed card must agree with a separate full-card reference channel bound to all 52 template hashes. Equal/mismatching channel IDs, disagreement or low confidence are rejected.
5. **Seat from registered geometry and verified rotation.** Card boxes are projected into reference coordinates. Screen top/right/bottom/left is assigned only from the explicit table region and center dead zone; the profile then maps those positions to logical `N/E/S/W`. Only cyclic 0/90/180/270-degree compass rotations are accepted. The recognizer cannot assert a seat directly.
6. **Stable deal identity.** Temporal evidence is joined only by scoped explicit board identity or a gated visual anchor. Time proximity is not a deal key.
7. **Independent-frame consensus.** The default profile contract requires at least two distinct frame SHA-256 observations of the same `card + seat`. Repeating the same file cannot increase support.
8. **Deck conflict gate.** A card seen in different seats within a track produces `CONFLICT`; no deal is emitted. Existing 52-card canonicalization remains authoritative for duplicates, 13-card limits and exact complement derivation.
9. **Explainable rejection.** Pending, rejected and conflicting observations retain bounded profile, frame, registration, channel, geometry and temporal evidence. The manifest frame SHA is re-computed from bytes before shadow output is written. `UNAVAILABLE` is therefore auditable.
10. **Verified layout prior.** The interface profile records the observed school layout: hearts, clubs, diamonds, spades; ranks descend from ace to two. Hands displayed at screen top/bottom use increasing registered X; screen left/right use increasing registered Y. Logical seats are obtained through the verified compass rotation. The rule is applied only to ambiguous candidate sets above the confidence gate. A unique result is labelled `LAYOUT_SUGGESTION`, has `accepted_as_observation=false`, and cannot trigger `39 → 13`.
11. **Attributed speech fusion.** A normalized exact teacher declaration may enter the shadow observed set only when card, seat, transcript locator, bounded timeline, verified speaker identity, speaker assignment confidence and declaration confidence all pass their gates. Student declarations are retained as `STUDENT_SPEECH_SUGGESTION`; they can confirm, contradict or corroborate a layout suggestion but never add a card, create a complete deal or trigger derivation.
12. **Board metadata.** An injected recognizer may provide an attributable, confidence-gated board-number observation. Dealer and vulnerability are deterministically derived from the standard duplicate 4/16-board cycles. Optional directly observed dealer/vulnerability must agree with that cycle or the frame fails closed. Board metadata requires the same independent-frame temporal consensus as cards, and disagreement inside one stable deal identity is a hard conflict. A bare string such as `value=board-7` is only a track identity and is never parsed as an observed board number.

## Runtime contract

The injected pixel recognizer receives the frame path and a read-only profile view. It returns:

```json
{
  "frame_sha256": "...",
  "registration": {
    "reference_frame_sha256": "...",
    "inliers": 100,
    "inlier_ratio": 0.95,
    "homography": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
  },
  "deal_identity": {
    "kind": "EXPLICIT_BOARD",
    "scope": "source-stable-scope",
    "value": "board-7"
  },
  "board_metadata": {
    "board_number": {
      "value": 7,
      "confidence": 0.98,
      "source": "VISUAL_TEXT",
      "evidence_locator": "frame.jpg#board-number"
    }
  },
  "cards": [{
    "box": {"x": 490, "y": 50, "w": 20, "h": 20},
    "rank": {"value": "A", "confidence": 0.98, "channel_id": "glyph-rank-suit-v1"},
    "suit": {"value": "S", "confidence": 0.97, "channel_id": "glyph-rank-suit-v1"},
    "reference_match": {
      "card": "AS",
      "confidence": 0.96,
      "channel_id": "full-card-reference-v1"
    }
  }]
}
```

An ambiguous pixel observation may instead provide two or more independently
matched full-card candidates:

```json
{
  "box": {"x": 400, "y": 50, "w": 20, "h": 20},
  "card_candidates": [
    {"card": "KH", "confidence": 0.86, "channel_id": "full-card-reference-v1"},
    {"card": "2S", "confidence": 0.81, "channel_id": "full-card-reference-v1"}
  ]
}
```

Upstream ASR and speaker attribution may pass a declaration scoped by frame
SHA/file or by an overlapping timeline:

```json
{
  "card": "KH",
  "seat": "N",
  "confidence": 0.98,
  "speaker_role": "STUDENT",
  "speaker_id": "student-1",
  "speaker_identity_verified": true,
  "speaker_assignment_confidence": 0.97,
  "evidence_locator": "transcript.jsonl#segment=7",
  "start": 9.0,
  "end": 11.0,
  "frame_sha256": "..."
}
```

The frame selector is evaluated before fusion. A declared frame SHA has
precedence over file/timeline matching. Speech fusion is enabled only with the
profiled shadow challenger and is emitted separately as `speech_fusion` and
`fused_deal`; it never replaces the raw vision result.

The challenger output keeps `canonical_promotion_allowed=false`. It may produce a shadow `OBSERVED` deal or the existing exact `39 observed → 13 DERIVED` result, but neither is automatically published to the School Canon. Profiled output is written only to `bridge_positions_profiled_shadow.jsonl`; the canonical downstream filename `bridge_positions.jsonl` is never created or overwritten by this path.

## Activation and rollback

Programmatic test activation is explicit:

```python
process_job_frames(
    job_dir,
    profiled_challenger=challenger,
    speech_declarations=normalized_attributed_speech,
)
```

No environment variable, file presence or legacy fallback activates it. An injected engine/parser/challenger and the legacy-old-BBO flag are mutually exclusive; mixed execution is rejected. Shadow summaries declare `result_scope=SHADOW_ONLY` and `canonical_promotion_allowed=false`.

Rollback is removal of the injected challenger or revert of this change. Existing default routing requires no migration and remains fail-closed.

## Gates before any production promotion

- a real pixel backend exists and is independently versioned;
- its artifacts and human-verified train/test split are hash-bound;
- frozen real-video holdout covers multiple layouts, compression levels, partial hands, played cards, overlaps and all missing-seat positions;
- exact `card + seat` precision meets the approved gate (currently the repository gold gate is stricter than 95%: 99.5% precision and 95% recall);
- seat errors = 0;
- duplicate-card errors = 0;
- false complete/derived deals = 0;
- no unapproved external service, model license or frame transfer is introduced;
- logically independent assurance reaches at least I2.

Until all gates pass, the only valid operational mode is opt-in shadow/test with no canonical promotion.
