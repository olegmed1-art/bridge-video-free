# INGEST-0013 — Two inactive page candidates created in Neon

Date: 2026-08-27

Two versioned page-level knowledge candidates now represent the approved PDF:

- `bidding.canon.natural-system-2p.page-1`;
- `bidding.canon.natural-system-2p.page-2`.

Each candidate:
- has authority target `school_canon` because the source is explicitly approved;
- remains `review_status=unreviewed` and `status=candidate`;
- has semantic status `RAW_EXTRACTED_NOT_SEMANTICALLY_STRUCTURED`;
- has `activation_eligible=false`;
- is linked to the exact source and page evidence;
- has no active `canon_activation`.

A fail-closed verification required exactly two candidates, the expected boundary fields and zero active activations.

Executable rules activated: 0.
