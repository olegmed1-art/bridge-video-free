# Book candidate dry-run

This adapter targets the existing `bridge_contracts.book_material` staging contract.
It reads the two editorial cards from `../evidence/`, maps their derived teaching
statements to `ATOM` records with `origin=INFERENCE`, and calls `produce_bundle`.
It has no storage, WORLD, Canon, or publication call. Output always has
`processing=STAGED`, `publication=NOT_PUBLISHED`, `review=NOT_REVIEWED`,
`verification=M0`, and `rights=RESTRICTED`. The trace also carries the
separate example review statuses. `rejected` and `pending` examples are never
mapped into the ATOM, and both `student_delivery_allowed` and
`canon_promotion_allowed` remain false.

Run the one synthetic provenance test from the repository root:

```powershell
python -m unittest discover -s tests -p test_book_candidate_import.py -v
```

Run the real-card preflight from the repository root:

```powershell
python -m tools.book_candidate_import.adapter `
  ..\evidence\book-semantic-review-20261014\low-card-tricks-source-card.md `
  ..\evidence\book-entries-20261014\entry-preservation-card.json
```

The original `Bridge 04_final6.pdf` was fetched through connected Drive for a
local dry-run. `../evidence/book-entries-20261014/Bridge04-source-evidence.json`
records its SHA-256, page count, printed-to-physical page mapping, and short
text crops. Supply it with `--evidence` and optionally `--output` to save the
complete dry-run result. Its local source and edition IDs do not assert registry
admission or school Canon authority.

For a single offline PDF-to-staging check, pass `--pdf <local-original.pdf>`
along with `--evidence <local-source-evidence.json>`. The optional PDF check
uses `pdfplumber` and `pypdf` to verify the source SHA-256, page count, printed
labels, every crop excerpt and segment hash before staging. Keep the PDF,
cards, evidence and output outside the Git checkout.

The current staging schema has no candidate-version or candidate-status fields.
The returned `trace` preserves each input file's SHA-256 version, original
`candidate_unpublished` status, locator, and supporting printed pages beside
the staging bundle. A future persisted import must retain this trace or add
equivalent fields through an explicit contract change; the bundle alone cannot
represent those editorial facts.

Illustrations are checked before text staging with the existing
`bridge_contracts.book_material.validate_position` contract. A source-declared
complete deal must have exactly 52 unique cards and 13 per hand; a partial
diagram remains partial and is never completed from inference. Duplicate cards
and oversized hands produce separate illustration diagnostics. Explicitly
printed holdings are parsed and compared with the machine transcription, so
silently replacing a printed card produces `TRANSCRIPTION_MISMATCH`. Illustrations
are never included in this adapter's ATOM bundle. The committed
`fixtures/synthetic-duplicate-club-diagram.json` exercises this path without
publishing book content. The real p27 diagram and source hashes remain in local
evidence only: its repeated ♣7 is reported as `invalid / DUPLICATE_CARD`,
while independent source text still stages.
