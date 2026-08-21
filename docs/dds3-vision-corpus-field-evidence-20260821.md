# DDS3 P1 vision corpus field evidence — 2026-08-21

## Scope

This is a **real-image corpus construction gate**, not a claim that pixel-to-deal extraction is operational yet.

The corpus builder renders the original board diagrams from three real Israel Bridge Federation simultaneous-tournament booklets. Canonical truth is read independently from the PDFs' embedded vector text and is accepted only when it validates as exactly 52 unique cards, 13 per hand, with explicit Board / Dealer / Vulnerability. DDS3 is not used to create or repair truth.

No source PDF, rendered board image, participant data, or private Drive identifier is committed to the public repository. The public evidence contains only source filenames, source SHA-256 digests and aggregate counts.

## Real source run

| Source | Source PDF SHA-256 | Accepted real board images | Rejected source panels |
|---|---|---:|---:|
| `sim-6.26.pdf` | `0b5f0fdff5e6b550448fea1f936fd2b36d9da54108998b21528fab090982bef4` | 19 | 5 |
| `sim-7.26.pdf` | `f13436af84e271e50af7e055e50e2d1a8528942cbba67fc239e5c5575ecacd76` | 20 | 4 |
| `sim-8.26.pdf` | `789ab26083e60b04ff3e5f0238997bd061bdf631773478afe4b8ff45f8639f1a` | 21 | 3 |
| **Total** | — | **60** | **12** |

The 12 rejected source panels were deliberately not repaired: source text geometry did not independently validate a complete deal under the strict builder. This is expected fail-closed behavior for corpus truth generation.

## Canonical-truth invariants

- original rendered pixels are the vision input;
- source PDF vector text is the canonical truth channel;
- truth and later DDS3 numerical results remain separate;
- no missing card is reconstructed by complement;
- no dealer/vulnerability is inferred from board number;
- a corpus record is emitted only after 52-unique-card / 13-per-hand validation;
- every emitted image and source PDF has a SHA-256 provenance digest.

## Status against issue #236

The requested 50–100 real-image corpus size floor is now demonstrated with 60 accepted real board images from three independent source documents. This does **not** complete P1: a local/free pixel extractor must still take JPEG/PNG/WebP bytes and produce `ScreenshotDealObservation` with per-field confidence, ambiguity rejection and no bridge-inference repair. The corpus is intended to measure that extractor separately from DDS3.
