# Universal Video profiled card challenger v1

Date: 2026-08-28

Governance mode: ASSURED

Status: implemented for opt-in shadow/test use; no pixel backend approved; no production promotion

## Outcome

Universal Video now has a deterministic, interface-profiled decision boundary for a future card pixel recognizer. The boundary adopts the transferable principles found in Dealer4, BridgeSorter and TCG-AR without importing proprietary code, model weights, accuracy claims or special-card hardware.

The default Native Bridge Vision runtime is unchanged. With no explicitly injected challenger it still has zero detector families and returns `UNAVAILABLE`. The old BBO adapter remains separately opt-in.

## Adopted mechanisms

1. **Controlled interface profile.** Every backend call is bound to a human-verified profile ID, reference-frame SHA-256, reference dimensions, table geometry, complete rank/suit template hashes and immutable template-set hash.
2. **Teach boundary.** `build_teach_profile` accepts only already human-labelled template hashes. It does not infer glyph labels. A missing symbol, unverified profile or changed hash fails closed.
3. **Frame registration.** The backend must supply a homography to the profile reference plus inlier count and inlier ratio. The deterministic layer validates the matrix, thresholds and profile reference before using a card box.
4. **Independent recognition channels.** Rank and suit are supplied separately. Their composed card must agree with an independent reference-card match. Any disagreement or low-confidence channel is rejected.
5. **Seat from registered geometry.** Card boxes are projected into reference coordinates. `N/E/S/W` is assigned only from the explicit table region and center dead zone; the recognizer cannot assert a seat directly.
6. **Stable deal identity.** Temporal evidence is joined only by scoped explicit board identity or a gated visual anchor. Time proximity is not a deal key.
7. **Independent-frame consensus.** The default profile contract requires at least two distinct frame SHA-256 observations of the same `card + seat`. Repeating the same file cannot increase support.
8. **Deck conflict gate.** A card seen in different seats within a track produces `CONFLICT`; no deal is emitted. Existing 52-card canonicalization remains authoritative for duplicates, 13-card limits and exact complement derivation.
9. **Explainable rejection.** Pending, rejected and conflicting observations retain profile, frame, registration, channel, geometry and temporal evidence. `UNAVAILABLE` is therefore auditable.

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
  "cards": [{
    "box": {"x": 490, "y": 50, "w": 20, "h": 20},
    "rank": {"value": "A", "confidence": 0.98},
    "suit": {"value": "S", "confidence": 0.97},
    "reference_match": {"card": "AS", "confidence": 0.96}
  }]
}
```

The challenger output keeps `canonical_promotion_allowed=false`. It may produce a shadow `OBSERVED` deal or the existing exact `39 observed → 13 DERIVED` result, but neither is automatically published to the School Canon.

## Activation and rollback

Programmatic test activation is explicit:

```python
process_job_frames(job_dir, profiled_challenger=challenger)
```

No environment variable, file presence or legacy fallback activates it. An injected engine/parser/challenger and the legacy-old-BBO flag are mutually exclusive; mixed execution is rejected.

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
