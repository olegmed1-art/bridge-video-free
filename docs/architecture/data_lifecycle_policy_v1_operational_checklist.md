# Data Lifecycle Policy v1.0 — operational checklist

Status: ACTIVE companion to `data_lifecycle_policy_v1.md`
Date: 2026-08-18

## Before storing a new object

1. Assign class P0–P7.
2. Select canonical storage according to policy.
3. For persistent P0/P2/P3 objects record checksum, size, provider locator and provenance.
4. Do not use GitHub Actions as the only long-term location for P0/P2/P3.

## Before modifying

- P0: never modify in place.
- P1: create a new version/forward change.
- P2/P3: version unless explicitly mutable working state.
- P4/P5: mutable by process contract.
- P6: do not rewrite history to hide errors.

## Before deleting automatically

Deletion is fail-closed. Verify all applicable Deletion Gate conditions from the policy.

Minimum machine-checkable evidence:
- object class;
- active/restart/recovery dependency check;
- durable-location proof where required;
- checksum/integrity proof where required;
- provenance/registry reference;
- deletion reason;
- policy version;
- replacement/recovery evidence where applicable.

If any required evidence is absent, keep the object.

## GitHub Actions defaults

- Artifacts are temporary unless explicitly classified otherwise.
- Prefer the shortest retention compatible with active restart/resume and audit needs.
- Persist milestone/final P2/P3 objects to Drive, verify them, then allow Actions copies to expire or be deleted.
- Caches are P5 and may expire automatically.
- Do not publish user media/transcripts as Actions artifacts.

## Google Drive defaults

- P0/P2/P3 durable objects go to stable project folders.
- Do not treat folder name as identity; register Drive file ID and checksum.
- For connector upload size limits, split storage is allowed only with manifest, per-part checksums and exact reconstruction verification.

## Neon defaults

- Store identity, provenance, location, verification, state and audit evidence.
- Do not store large binary payloads without an explicit architecture decision.
- Record deletion audit for automatic deletion except routine fully reproducible P5 cache eviction.

## Recovery rule

A recovery object is not deletable merely because a newer one exists. Require replacement verification, restore test and observation gate first.

## Failure rule

A failed experiment may discard large transient P4/P5 material after extracting durable root-cause/regression evidence. Never erase the existence or lesson of a material failure.
