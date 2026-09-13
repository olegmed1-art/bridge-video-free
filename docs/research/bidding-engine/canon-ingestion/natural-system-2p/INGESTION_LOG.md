# Журнал импорта: «Натуральная система торговли — 2 стороны»

Status: **ACTIVE / APPEND-ONLY BY POLICY**

## Authoritative decision

The School Director explicitly approved the whole two-page PDF as a SCHOOL CANON source for every opening, response, continuation and rebid actually stated in the document.

Boundaries:
- absent or ambiguous meanings are not invented;
- WORLD / EXTERNAL knowledge may be used for comparison and Red Team only;
- the rejected `Курс Бридж - Конспект. Правки.` file is not a canonical source;
- source approval does not itself activate any executable bidding rule.

## Source identity

- Drive file ID: `1HkVff4iH2e3HT5kwblvd3mY8TUQPR6jf`
- Canonical locator: `gdrive://1HkVff4iH2e3HT5kwblvd3mY8TUQPR6jf`
- Pages: 1–2
- Exact SHA-256 is recorded in the private extraction manifest and must be copied into the durable source record before rule activation.

## Event log

### INGEST-0001 — Controlled ingestion started
- Director requested continued implementation with visible progress and a durable record of every action.
- Decision: keep a human-readable GitHub log plus machine-readable ingestion events.
- No bidding meaning was created or activated.

### INGEST-0002 — Source fingerprint and raw extraction
- Local source was fingerprinted.
- Page text, text blocks and table candidates were extracted into a private working bundle.
- Every extracted fragment is `RAW_EXTRACTED_NOT_SEMANTICALLY_STRUCTURED`.
- No raw fragment is runtime eligible.

### INGEST-0003 — Conservative fragment inventory
- A detector marked likely auction fragments and normalized visible call tokens only.
- It did not infer HCP ranges, suit lengths, forcing, alerts, negative inferences, priorities or continuations.
- All fragments remain `UNREVIEWED`.

### INGEST-0004 — Canonical source registration issued
- Idempotent write issued to `public.source` for the exact Drive locator.
- Intended type: `school_canon_pdf`.
- Intended trust class: `school_canon_director_approved`.
- Read-back verification is required before this event becomes `VERIFIED`.

### INGEST-0005 — Director approval evidence registration issued
- Idempotent append-only evidence write issued to `public.evidence`.
- Locator records pages 1–2, approved scope and the no-silent-WORLD-promotion boundary.
- Read-back verification is required before this event becomes `VERIFIED`.

## Required next gates

1. Read back the source/evidence rows from Neon and record stable IDs.
2. Visually reconcile both PDF pages with extracted blocks.
3. Build the auction-branch map without filling missing semantics.
4. Create atomic rule candidates with exact page/block provenance.
5. Produce positive, negative, boundary, interference and hidden-information tests.
6. Run Curator, Observatory and I2 Red Team checks.
7. Activate only unambiguous rules that pass all gates.
