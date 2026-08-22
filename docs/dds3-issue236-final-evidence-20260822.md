# DDS3 issue #236 — final evidence audit — 2026-08-22

## Decision boundary

This audit checks the five Definition-of-Done items in issue #236 against merged production code and real field evidence. It does **not** redefine the raw-image capability as a promise to recognize every unseen graphical design. The operational contract is: a caller supplies raw JPEG/PNG/WebP bytes without manually structuring the deal; the runtime automatically recognizes one of the field-proven layout families or fails closed. Unsupported or ambiguous layouts remain rejection, never bridge-inference repair.

The raw-image path is local/free. It must bind the exact bytes by SHA-256, emit explicit Board/Dealer/Vulnerability and all four hands with confidence, pass the 52-unique-card gate, and only then invoke pinned DDS3. No paid/cloud vision fallback and no alternate numerical solver are allowed.

## P0 evidence

PR #243 merged the persistent position runtime (`6b139ad0fe8db0abb629419e2fcc1956b3de0eb3`). Its authenticated production gate used one long-lived `SolverContext` for the same Stage-2 position twice, returned identical legal-move values, reused the same TT instance and produced the real repeat-search signal `168200 -> 109` nodes. The runtime supports `dd_table`, `position`, `position_all_moves`, equal-optimal/equivalent cards, regret `0/1/2+`, and fixed-partnership trajectory / first swing / first loss / unrecovered damage. Successful numerical results identify `engine=DDS3` and `fallback_used=false`.

## Real raw-image evidence

The automatic ingress dispatcher now contains five independently field-proven local layout families. Each positive gate starts from real raster pixels, derives canonical truth independently from source-PDF vector text, and keeps DDS3 out of truth creation.

| Layout family | Real evidence | Exact positives | Wrong accepts | Real-pixel rejection evidence |
|---|---|---:|---:|---:|
| federation yellow panel | 60 real board images from 3 IBF source PDFs | 42/60 exact deal + metadata | 0 | 5/5 corrupted variants rejected |
| publication cross | 5 public source samples exercised | 1 exact deal + metadata | 0 | severe crop 1/1 rejected |
| publication grid | 2 public source samples exercised | 1 exact deal + metadata | 0 | severe crop 1/1 rejected |
| named quadrant | 4 real VuBridge board pages | 2 exact deal + metadata | 0 | severe crop 2/2 rejected |
| EBU appeals cross | 1 real EBU Appeals board page | 1 exact deal + metadata | 0 | severe crop 1/1 rejected |

The 60-image federation corpus is the fixed 50–100-image regression foundation. The four additional independent families provide cross-layout field evidence and per-family extraction/rejection metrics. Together they meet the audit target of at least five real layout families without treating generated images as substitutes for real source diagrams.

### Federation corpus

The canonical corpus contains 60 accepted real images (`19 + 20 + 21`) from `sim-6.26.pdf`, `sim-7.26.pdf`, and `sim-8.26.pdf`. Twelve malformed/incomplete source panels were rejected rather than repaired. Source PDF vector text is the truth channel; every accepted source/image is SHA-256 bound. The first local extractor measured 42/60 exact full deal + Board/Dealer/Vulnerability, 0 wrong accepts, 18 valid-image fail-closed rejections and 42/42 accepted precision. Real-pixel negatives blur/crop/51-card/duplicate/ambiguous-rank were rejected 5/5.

### Publication cross

PR #260 merged the second family (`9e33c10a277bdb64052b6e8355a0baf6585ae30f`). Field run `32518779093` produced one exact WBF 2006 Board 12, zero wrong accepts and severe-crop rejection 1/1. Other unsupported/malformed source samples stayed rejected/error rather than being repaired.

### Publication grid

PR #263 merged the third family (`91b419d0fc4ec0394843ff004fd9b4ab14dbeb34`). Field run `32521121850` produced exact EBL 2022 Board 1, zero wrong accepts and severe-crop rejection 1/1. The exact image was SHA-256 bound as `0c348941e354ff57b6361d659a5e73915f7975b037949b3c091899095d2c4d9e`.

### Named quadrant

PR #266 merged the fourth family (`08295eb1a1f480bc48e6dae263672b243cfe7a0b`). Field run `32530641366` exercised four real VuBridge pages: two exact positives, zero wrong accepts and severe-crop rejection 2/2. Ambiguous/incomplete pages stayed fail-closed.

### EBU appeals cross

PR #272 merged the fifth family (`c55a003f77ed95e7f2a7606f6157a0b092c62928`). Final field run `32541978741` produced exact EBU Appeals 2001 Board 2: four hands, Board, Dealer and Vulnerability all exact; zero wrong accepts; severe-crop rejection 1/1; production routing exact. The raster input SHA-256 is `7eb25a480c886a528e8b458c6f33cd74403a39a67056a4d91e726fc887af84be`; source PDF SHA-256 is `7348874a26fdfe2b395f4bd1bb1863c5587b1c6cc13996f5ba60839c04778879`. The field receipt explicitly records `dds3_used_for_truth=false`, `bridge_inference_repair=false`, and `paid_or_cloud_vision=false`.

## Final raw-image safety gate

The final issue-closing change strengthens `solve_raw_image()` beyond the generic structured screenshot contract. Before DDS3 is called, raw vision must now provide:

- explicit observed `board_number`, `dealer`, and `vulnerability`;
- a valid confidence value and non-empty source for each of those fields;
- all N/E/S/W hands and S/H/D/C holdings;
- confidence for every hand/suit holding;
- a `vision_extractor` identity;
- `image_sha256` that exactly equals the digest of the received bytes and is marked fully verified.

Therefore board-derived metadata remains available only to explicit structured callers where that looser contract is intended; it cannot silently satisfy the production raw-image evidence gate.

## Definition of Done

1. **Raw image -> vision -> validation -> DDS3 without manual deal structuring: PASS.** `solve_raw_image()` accepts actual JPEG/PNG/WebP bytes, automatically dispatches among five field-proven families, requires pixel evidence/confidence/SHA binding, validates the complete deal, then invokes DDS3. Unknown layouts fail closed.
2. **Stable table + position/all-moves API: PASS.** P0 merged and authenticated runtime evidence is green.
3. **DDS3 provenance / no fallback: PASS.** Numerical DD results are pinned DDS3 only; reference DDS 2.9 is permitted only post-hoc after DDS3 output freeze and never as fallback.
4. **Real golden + position/TT + image negative CI: PASS.** The integration and family field gates exercise real DDS3, persistent context reuse and real-pixel image rejection cases.
5. **Real regression corpus and vision metrics separate from solver: PASS.** The 60-image corpus has independent vector-text truth and measured exact/rejection metrics; four more field-proven families provide cross-layout metrics, while DDS3 is explicitly excluded from truth creation.

## Remaining limitations (not open #236 gates)

- An unseen graphical layout is not guaranteed to be recognized; it must be added and field-proven or it is rejected.
- Valid-image recall is deliberately conservative; the federation family currently accepts 70% of its valid corpus with 100% precision among accepted images.
- A fail-closed rejection is not a numerical DDS3 failure and must remain classified as a vision/input rejection.

These limitations are intentional safety properties. They do not authorize weakening ambiguity, 52-card, provenance, cost, privacy or no-fallback gates.
