# Bridgit server play-event capture V1

Date: 2026-09-11

Change ID: `BRIDGIT-PLAY-EVENT-CAPTURE-V1`

Governance mode: `ASSURED`

Status: implemented in the autonomous-video shadow worker; not production activated

## Purpose and scope

The server records an evidence screenshot for every newly detected played-card
position. Screenshot selection, decoding, hashing and PNG encoding all happen in
the bounded server job. ChatGPT does not inspect or select the video frames.

This change covers play-card evidence and deal-local card memory. Bidding
recognition and bidding explanations remain a separate pipeline and are not
modified by this change.

## Event and screenshot contract

- The worker samples at 10 Hz by default.
- A new occupied trick seat creates an append-only server play event.
- A card-shaped, seat-resolved region whose rank or suit is unreadable creates
  an `UNKNOWN_CARD` event instead of a guessed card.
- Every event has `snapshot_required=true`, a source frame index, timestamp,
  source-pixel SHA-256 and one manifest reference to a lossless PNG.
- Multiple events detected in the same source frame may share the same PNG, but
  every event remains explicitly referenced in the manifest.
- The second video decode must reproduce the exact source-pixel SHA-256 or the
  job fails closed. Evidence files use mode `0600` and bounded counts and bytes.
- Sampling cannot prove capture of a card displayed for less than the sampling
  interval. This limitation must be measured in canary video before activation.

## Memory

Accepted cards are retained append-only within a stable deal identity even
after they disappear from the table. Memory is never reused across deals and
does not contain cards inferred from hidden information.

Bridge-logic weighting is intentionally absent from this version. Acceptance
uses only direct visual evidence and temporal consensus; an unreadable card
remains `UNKNOWN_CARD`.

## Evidence and assurance

Focused observer, reconstruction and raw-video tests cover recognized and
unknown regions, append-only memory, event-to-screenshot completeness and exact
pixel identity.

## Rollback and promotion

Rollback is a revert of this change. The worker remains `SHADOW_ONLY`, sets
`canonical_promotion_allowed=false`, and has no production route in this
change. Promotion requires canary measurements for missed short-lived cards,
false event transitions, storage cost and evidence retention policy.
