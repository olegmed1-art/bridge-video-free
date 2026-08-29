# Universal Video profiled card challenger v2

Date: 2026-08-28

Governance mode: ASSURED

Status: implemented for opt-in shadow/test use; world-model adapters available;
no pixel backend approved; no production promotion

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
10. **Verified layout prior.** The interface profile records the director-verified Bridgit layout: hearts, clubs, diamonds, spades (`H,C,D,S`) in that order for every hand; ranks descend from ace to two inside each suit. Hands displayed at screen top/bottom read the sequence horizontally through increasing registered X; screen left/right read the same sequence vertically through increasing registered Y. Logical seats are obtained through the verified compass rotation. The rule is applied only to ambiguous candidate sets above the confidence gate. A unique result is labelled `LAYOUT_SUGGESTION`, has `accepted_as_observation=false`, and cannot trigger `39 → 13`.
11. **Attributed speech fusion.** A normalized exact teacher declaration may enter the shadow observed set only when card, seat, transcript locator, bounded timeline, verified speaker identity, speaker assignment confidence and declaration confidence all pass their gates. Student declarations are retained as `STUDENT_SPEECH_SUGGESTION`; they can confirm, contradict or corroborate a layout suggestion but never add a card, create a complete deal or trigger derivation.
12. **Bridgit compass and board metadata.** For the verified Bridgit profile, the source is the compass immediately above the cards at the upper right of the table: board number in its centre, `N/E/S/W` around it, the yellow `D` dealer marker, and (when confidently decoded) vulnerability colour. `bridge_vision.bridgit_compass` accepts only the human-verified ROI, a complete cyclic 0/90/180/270-degree compass and attributable observations at or above the confidence gate. It binds the observed board number to the stable deal track and verifies dealer/vulnerability against the standard duplicate 4/16-board cycles. A board change starts a separate temporal track. ROI, compass, profile rotation, dealer or vulnerability disagreement fails closed to `REVIEW`; the adapter never guesses a missing label. Board metadata requires the same independent-frame temporal consensus as cards. A bare string such as `value=board-7` is only a track identity and is never parsed as an observed board number.

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

Every profiled SHADOW run also writes `bridge_positions_profiled_shadow.pbn` for
director review. Accepted observations are accumulated only inside the stable
deal identity supplied by the verified Bridgit compass. Partial hands use
`X-Observed-N/E/S/W` and `X-UnknownCount-N/E/S/W`; they deliberately omit the
standard `Deal` tag because omitted cards must not be misrepresented as voids.
Only 52 unique accepted observations (13 per seat) may produce a standard PBN
`Deal` tag. Conflict records, pending temporal votes, layout suggestions and
diagnostic candidates are not exported as found cards. JSONL, summary and PBN
form one all-or-nothing, hash-bound SHADOW artifact set.

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

## World-model test adapters

`bridge_vision.world_card_backends` supplies two explicit challenger adapters:

- `LgdGen3OnnxDetector` runs a hash-bindable 52-class LGD gen3 ONNX artifact
  locally with ONNX Runtime. The caller remains responsible for the AGPL
  boundary and artifact approval.
- `RoboflowCardDetector` calls one explicitly configured hosted model/version.
  Calling it transfers that selected frame outside Oracle; it is never created
  from ambient configuration or enabled by file presence.

Neither adapter is a complete bridge recognizer. `ProfiledWorldReferenceComposer`
uses its output only as the independent full-card reference channel. A separate
school glyph backend must still emit rank and suit observations, registration
and deal identity. Matching is by bounded box IoU; missing or tied matches are
dropped. Seat ownership remains solely in the registered geometry layer.

Gold evaluation may use `evaluate_card_detector_report` to emit exact per-frame
`TP/FP/FN/ambiguous/seat_errors` plus aggregate precision and recall. The
profiled SHADOW JSONL and summary are published to Drive only as a complete
pair; the canonical `bridge_positions.jsonl` is not published by this path.

## Temporal visibility during play

`bridge_vision.temporal_visibility.TemporalCardVisibilityTracker` separates:

- `VISIBLE`;
- `VISIBLE_FN`;
- `PLAYED_NO_LONGER_VISIBLE`;
- `OCCLUDED`;
- `AMBIGUOUS`;
- `NOT_EXPECTED_VISIBLE`.

A card disappearance never proves play. `PLAYED_NO_LONGER_VISIBLE` requires an
explicit verified play event with an evidence locator, stable deal identity,
and a prior or current observation of that exact `card + seat`. Deal tracks and
duplicate frame identities are isolated fail-closed. Temporal gold evaluation
uses only `VISIBLE_FN` in visible recall; verified played, occluded, ambiguous
and not-expected-visible cards remain separately counted.
