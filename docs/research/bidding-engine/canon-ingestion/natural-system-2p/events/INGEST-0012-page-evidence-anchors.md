# INGEST-0012 — Page-level provenance anchors

Date: 2026-08-27

Idempotent writes created or re-used two `public.evidence` rows:

- `natural-system-2p-page-1` — page 1;
- `natural-system-2p-page-2` — page 2.

Each row points to the exact approved Drive file and is marked `canonical_pdf_page` / `DIRECT_SOURCE` / `verified`.

These records are provenance anchors only. They do not interpret text and do not activate any bidding rule.

Rules activated: 0.
