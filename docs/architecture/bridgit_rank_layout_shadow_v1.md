# Bridgit rank-layout shadow backend v1

Date: 2026-09-05

Governance mode: `ASSURED`

Status: implemented and locally validated; opt-in shadow only

## Purpose and boundary

`bridge_vision.bridgit_rank_layout` recognizes a complete, visibly displayed four-hand deal in the verified Bridgit desktop interface. It is a bounded pixel worker for already selected frames, not a video decoder, frame sampler, production route or canonical knowledge writer.

The output is always:

- `result_scope=SHADOW_ONLY`;
- `provenance_class=MODEL_CANDIDATE`;
- `canonical_promotion_allowed=false`;
- `school_canon_write_performed=false`;
- `hidden_hand_reconstruction_performed=false`.

The module is intentionally not exported as a default `BridgeVisionEngine` detector. Its rank templates, suit-position prior and ordered deck constraint are correlated evidence, not the two independent recognition channels required by `ProfiledCardChallenger`.

## Algorithm

1. Load one human-reviewed, self-hashed layout profile and a reference frame whose SHA-256 is sealed by that profile.
2. Read job, profile and image files through bounded streams that request at most the applicable limit plus one byte; reject overflow before JSON parsing, hashing or image decode. Compute each image's byte SHA-256 and parse its JPEG/PNG dimensions before calling OpenCV. One decoded BGR raster is capped at 64 MiB and aggregate input rasters at 256 MiB. Before decoding, also reject vertical scan spans above 512 pixels and jobs whose conservative template-call or template-dot-product estimate exceeds the fixed worker budget. Decode once and compute a decoded-pixel SHA-256. Duplicate bytes, duplicate decoded pixels, or a byte-distinct re-encoding of the reviewed reference pixels cannot increase temporal support.
3. When the verified profile includes `geometry.interface_anchor`, locate its normalized upper-right UI anchor over a bounded list of scales. The zero-mean matcher is invariant to light/dark intensity inversion, derives the translated/scaled game window, and resamples that window to reference geometry. Missing, weak, work-budget-exceeding, out-of-frame or spatially ambiguous anchors fail closed. The receipt retains normalized input-frame anchor/window regions, score, margin and scale. Legacy profiles without this field still require exact reference dimensions and do not establish cross-resolution coverage.
4. Build a rank-template bank from all 52 reviewed reference slots. Each rank has one sample per suit. Local translations use white padding and never wrap pixels across a crop boundary.
5. Independently in every observation frame, detect the four north/south suit fans in the verified screen order `H,C,D,S`. A missing fan or a non-13-card hand stops as `LAYOUT_UNKNOWN` or `PARTIAL_PLAY`; frames with different lengths or fan anchors stop as `LAYOUT_AMBIGUOUS`.
6. Independently in every observation frame, count west/east cards from edge-anchored contiguous chains of visible rank glyphs. Every frame must have hand totals and per-suit deck totals of 13, and all frames must produce the same geometry before assignment.
7. Calibrate the west/east overlap step independently for each suit over a bounded 18.00–25.00 pixel search.
8. For each suit, solve a deterministic dynamic program that assigns `A,K,Q,J,T,9…2` exactly once across the visible ordered slots. Solve both the fused evidence and every frame independently. Each frame must produce the exact same card-to-seat assignment, exceed the template floor, and retain a sufficient best-versus-runner-up margin; otherwise the result stops as `AMBIGUOUS`.
9. Require actual rank ink in every supplied observation frame for every slot. Evaluate that evidence-quality gate separately from the minimum frame-count gate: one otherwise-valid frame stops at `PENDING_TEMPORAL_CONSENSUS`, while missing ink in any supplied frame stops at `AMBIGUOUS`. This prevents the deck constraint or median fusion from filling an erased, occluded, or transition-frame glyph.
10. Build a per-card evidence report. Every accepted card retains seat, suit, rank, source, frame hash, timestamp, normalized region, confidence kind/value and recognizer version. Independent matching frames change the primary source from `VISUAL` to `TEMPORAL_CONSENSUS`. A normalized `TEACHER_POINTER` event may only corroborate the visual card under its point; an absent/ambiguous target requires review and any card/seat contradiction produces `NEEDS_REVIEW`.
11. Emit a deterministic, self-hashed receipt by atomic temporary-file write, file `fsync`, rename and directory `fsync`.

The deck bijection is an integrity/ordering constraint over 52 visible slots. It is not evidence for a missing card. If the layout is not fully visible, recognition stops; the worker never emits a completed hidden hand.

`bridge_vision.deal_evidence` also exposes an explicit offline-review complement for the narrow case of 39 temporally confirmed cards in three complete hands. Its 13 output records are marked `LOGICAL_INFERENCE`, have no frame/timestamp/region, set `visually_recognized=false`, `available_to_player=false`, `accepted_as_visual_observation=false`, and never enter `canonical_observed_deal`. Visual model candidates themselves set `available_to_player=null`, `player_availability=NOT_EVALUATED` and `accepted_as_canonical_observation=false`; screen visibility is never treated as legal availability to a player. The shadow job always calls this layer with logical inference disabled. All other gaps remain `UNKNOWN`.

## Profile gates

Schema: `bridge-vision-bridgit-rank-layout/v1`.

A profile must contain:

- a valid profile ID, reference-frame SHA-256 and human-review record;
- exact frame dimensions;
- verified `H,C,D,S` suit order and descending rank order;
- all 52 cards exactly once as reviewed template slots, with unique pixel locations;
- N/E/S/W anchors and bounded horizontal/vertical search regions;
- optionally, a normalized upper-right interface-anchor region, 1–33 unique scales, score floor and ambiguity margin; this field is required for any cross-resolution/translated-window claim;
- glyph size, local-registration radius, binary threshold, template and peak floors, peak prominence, rank-ink floor, assignment margin and at least two independent frames;
- `profile_sha256`, calculated over the entire profile except that field.

Unknown schema versions, incomplete templates, duplicate JSON keys, invalid coordinates, out-of-frame crops, changed hashes and malformed gates fail closed.

## Job contract

Schema discriminator: `BRIDGIT_RANK_LAYOUT_SHADOW_V1`.

```json
{
  "job_type": "BRIDGIT_RANK_LAYOUT_SHADOW_V1",
  "input_root": "/bounded/job-directory",
  "profile_id": "bridgit.desktop.example.v1",
  "profile_ref": {"path": "/bounded/job-directory/profile.json", "sha256": "..."},
  "reference_frame_ref": {"path": "/bounded/job-directory/reference.jpg", "sha256": "..."},
  "frame_refs": [
    {"path": "/bounded/job-directory/frame-1.jpg", "sha256": "...", "timestamp_ms": 1000},
    {"path": "/bounded/job-directory/frame-2.jpg", "sha256": "...", "timestamp_ms": 2000}
  ],
  "teacher_pointer_events": [
    {
      "source": "TEACHER_POINTER",
      "frame_sha256": "...",
      "timestamp_ms": 2000,
      "point": {"coordinate_space": "NORMALIZED_FRAME", "x": 0.71, "y": 0.18},
      "confidence": 0.96,
      "claimed_card": "AH",
      "claimed_seat": "N"
    }
  ],
  "allow_hidden_information": false,
  "production_write": false
}
```

All input paths must resolve beneath one explicit non-root `input_root`. Bounded stream reads enforce a 256 KiB job, one profile of at most 1 MiB, 1–16 JPEG/PNG frames and 32 MiB of compressed bytes per frame without first materializing an oversized file. Before decode, header dimensions, a 64 MiB per-raster ceiling, a 256 MiB aggregate decoded-raster budget, a 512-pixel per-side search-span ceiling, 1,000,000 template-scoring calls and 24,000,000,000 conservatively estimated template dot products are enforced. The work estimate includes per-frame side scans, all overlap-step trials, fused assignment and every independent frame assignment; a profile that permits 16 frames does not imply that every profile/frame-count combination fits the CPU budget. Timestamps and both byte/pixel identities must be distinct. Neither the reviewed reference bytes nor an independently encoded image with the same decoded reference pixels can count as an observation.

Local invocation:

```bash
python -m bridge_vision.bridgit_rank_layout \
  --job /bounded/job-directory/job.json \
  --output /bounded/job-directory/receipt.json
```

This command only creates the requested receipt. It does not register the backend, dispatch work, read a video, contact a service or modify SCHOOL CANON/WORLD.

## Statuses

| Status | Meaning |
|---|---|
| `SHADOW_FULL_LAYOUT_CANDIDATE` | 52 visible unique cards, 13 per seat, evidence and temporal gates passed; still non-canonical and non-independent |
| `PENDING_TEMPORAL_CONSENSUS` | Pixel assignment is complete but fewer than the required independent frames support it |
| `AMBIGUOUS` | Weak local evidence, insufficient per-card ink, narrow assignment margin, or any per-frame card-to-seat disagreement |
| `PARTIAL_PLAY` | Visible horizontal hand contains fewer than 13 cards |
| `LAYOUT_UNKNOWN` | Required profile fan geometry is not established |
| `LAYOUT_AMBIGUOUS` | A frame's side hand/suit peak counts fail totals, or independently measured frame geometries disagree |
| `REJECTED` | Job, profile, path, hash, image encoding/registration, decoded-memory or scoring-operation budget, replay, runtime dependency or production/hidden-information gate failed |

The nested deal-evidence report independently uses `COMPLETE_VISUAL`, `PENDING_TEMPORAL_CONSENSUS`, `PARTIAL`, `COMPLETE_WITH_LOGICAL_INFERENCE` (offline helper only), or `NEEDS_REVIEW`. These statuses never authorize canonical promotion.

## Production gates

Do not connect this backend to the canonical engine until all of the following exist:

1. a separate full-card classifier or formal channel with genuinely independent training/evidence;
2. agreement between that channel and rank/suit recognition;
3. a frozen, hash-bound, human-verified train/test split from multiple source videos and UI conditions;
4. the repository gold precision/recall gate, zero seat errors, zero duplicate cards and zero false complete deals;
5. negative coverage for voids, partial play, scaling, rotations, compression, overlap, occlusion and replay;
6. logically independent assurance at I2 or higher;
7. explicit resource limits and an isolated shadow canary that cannot preempt Autopilot.

## Rollback

There is no activation or data migration. Omit the explicit module invocation or revert the module, optional requirements, tests and documents. No production data restoration is required.
