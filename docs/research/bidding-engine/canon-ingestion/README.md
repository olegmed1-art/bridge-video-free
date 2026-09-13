# Canonical bidding ingestion

This directory contains the auditable, source-bound ingestion work for explicitly approved SCHOOL CANON bidding sources.

Rules:
- raw extraction is not runtime canon;
- every semantic rule must point to an exact source SHA-256 and page/block locator;
- WORLD / EXTERNAL knowledge cannot silently change SCHOOL CANON;
- ambiguity becomes a gap or director decision, never an invented rule;
- activation requires provenance, tests, conflict checks and an append-only action log.

Current source: `natural-system-2p/` — the director-approved two-page Natural Bidding System covering every opening, response, continuation and rebid actually stated in the PDF.
