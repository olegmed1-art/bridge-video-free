# Bridgit rank-layout shadow validation

Date: 2026-09-05

Change ID: `bridgit-rank-layout-shadow-v1`

Governance mode: `ASSURED`

Status: isolated shadow implementation validated locally; blocked from production activation

Amended: 2026-09-06 (anchor registration and per-card provenance contract)

## Outcome

The profiled Bridgit desktop recognizer was transferred into the repository as an opt-in module without changing the default Universal Video route, any workflow, server service, production database, SCHOOL CANON or WORLD. It uses only already selected local frames; no full-video batch was launched.

The repository implementation reproduced all five manually checked complete deals (260/260 exact `card + seat` pairs). In each multi-frame deal, every frame independently produced the same card-to-seat assignment and passed its own template, runner-up-margin and rank-ink floors before fusion was accepted. Only two deal groups had the required two or more distinct observation frames and reached `SHADOW_FULL_LAYOUT_CANDIDATE`. Three single-frame groups were rerun after separating evidence quality from frame-count sufficiency and correctly stopped at `PENDING_TEMPORAL_CONSENSUS` even though their shadow card maps matched the manual labels.

These five deals are a development regression corpus, not a frozen holdout: the same source and interface profile were used while tuning the algorithm, including raising the vertical peak floor from 0.72 to 0.76 after an extra false peak was exposed. The figures below therefore do not authorize production activation or satisfy the repository gold gate.

## Immutable local inputs

- source video SHA-256: `438fca0caa1b96d301823d6971743700da121d1052b57fc80c595e1e1bbef7f9`;
- verified reference frame SHA-256: `7a9c29b580e10f77d1dbb6f86c6327545dc9e42de42f273e682925d218a0349b`;
- frame geometry: 1686 by 720 pixels;
- verified screen order: hearts, clubs, diamonds, spades; ranks descend ace through two;
- no raw video or frame bytes were added to the repository.

| Deal group | Distinct frame SHA-256 evidence | Exact pairs | Result gate |
|---|---|---:|---|
| 1 | `84d9d7bc155c62f06c746940deb2d8802fe444115fa71ec169bba7bd9605f0df`, `bd3c3254ebd57ec7faa8d623712f0890d5e1b7e45bcc9d0e3c4c30815d30d557` | 52/52 | `SHADOW_FULL_LAYOUT_CANDIDATE` |
| 2 | `14d4f5012e40dc301bc9b2aa8d311a97c20523e77f55c16582eb5cfa0f34fd69`, `44742277004bfe4aa27c028a127b195dae0c732c6910e72b7a13c57970c3a304`, `86cbc2fbab64d0fb6e2528f93dbe5e3f0d604e7d394fdecd0d6492d3a2e9422d`, `c3922cc7d70ae6034a21e1c05922914d96bda4446b36da9c7d03cae8cfe40deb` | 52/52 | `SHADOW_FULL_LAYOUT_CANDIDATE` |
| 3 | `84bc98e4c0d91d07c6c03e7199aed08e02a7739471b7747a101ca07772f077e9` | 52/52 | `PENDING_TEMPORAL_CONSENSUS` |
| 4 | `383847ad021bbbad54a2de4c523d079e9634173b73cfcf89b2736dd75f99d8be` | 52/52 | `PENDING_TEMPORAL_CONSENSUS` |
| 6 | `65651d3700e89848f3068f9356fae8970a46217948f0395017edff4aaaad0cb5` | 52/52 | `PENDING_TEMPORAL_CONSENSUS` |

## Fail-closed evidence

- one of two locally generated observation frames with a north rank glyph erased: `LAYOUT_AMBIGUOUS`; independently detected fan anchors disagree;
- one of two locally generated observation frames with two visible heart ranks exchanged across east/west: `AMBIGUOUS` with `per_frame_deal_agreement_failed`; the independent frame assignment disagrees with the fused assignment;
- two distinct blank frames: `LAYOUT_UNKNOWN`;
- one mid-play frame: `PARTIAL_PLAY`;
- one frame from deal 1 combined with one frame from deal 2: `LAYOUT_AMBIGUOUS` because independently measured frame geometries disagree; no median-fused hybrid deal is emitted;
- duplicated frame bytes are rejected and cannot inflate temporal support;
- byte-distinct inputs with identical decoded pixels are also rejected as replay;
- the reviewed reference/template frame, including a byte-distinct lossless re-encoding with the same decoded pixels, is rejected as an observation;
- malformed/non-JPEG-or-PNG inputs, dimensions inconsistent with the profile, decoded rasters above 64 MiB, and jobs above the 256 MiB aggregate decoded-raster budget are rejected before OpenCV decode;
- job, profile and compressed-frame size ceilings are enforced with `limit + 1` bounded stream reads rather than post-allocation `read_bytes()` checks;
- vertical scan spans above 512 pixels and jobs above the conservative fixed template-scoring call or dot-product budget are rejected before any frame decode or recognition loop;
- a job with hidden information enabled or any production-write request is rejected before pixel recognition;
- output is fixed to `SHADOW_ONLY` / `MODEL_CANDIDATE`, with `canonical_promotion_allowed=false`, `school_canon_write_performed=false`, and `hidden_hand_reconstruction_performed=false`;
- the default `BridgeVisionEngine` remains empty and unchanged.

Repository contract tests include decoded-pixel reference replay, per-frame deal disagreement, pre-decode image-header validation, decoded-memory gates and pre-recognition scoring-work limits. The exact-current-main Universal Video suite, dependency audit, Ruff checks, Python compilation and `git diff --check` must all pass again on the amended exact head before this evidence is considered current.

## 2026-09-06 bounded extension evidence

No production video or video server was used. Registration tests now exercise a verified upper-right anchor across translated/scaled light UI, translated/scaled intensity-inverted dark UI, absent and ambiguous anchors, competing scales/window sizes, template-weighted whole-job work overflow, a file-replacement simulation whose actual decoded dimensions exceed the aggregate budget, and the peak retention limit for simultaneous source and registered rasters. Valid cases register to reference geometry; ambiguity and over-budget work or memory fail closed before matching. The profile and receipt use normalized anchor/game-window coordinates and retain bounded work evidence. This proves the registration contract and deterministic implementation, not real-world cross-UI accuracy.

Thirteen deterministic evidence-report cases cover: 52 unique cards and 13 per seat, one-frame versus temporal provenance, rejection of cross-frame hybrid consensus, pointer corroboration, pointer/card conflict to `NEEDS_REVIEW`, a pointer frame changed during the gesture, explicit unknown slots, guarded 39-card complement, refusal to guess from incomplete evidence, duplicate card across seats, a hand above 13 cards, required `H,C,D,S` diagram order, normalized region bounds and repeatability under reversed input order. The integrated shadow-job test verifies timestamp binding and that pointer evidence never becomes a visual observation.

Two additional private processed-report exports (27 extracted control frames in total, 960-pixel render width) were inspected locally to confirm the same Bridgit family, the upper-right compass/board anchor and `H,C,D,S` order in horizontal and vertical hands. Their report SHA-256 values are `355173084ee70406cc9692ccd6bc91fadac813b3f56d3a48bc7c54faff0260c8c5d46d98` and `002b05332ff7b0738a7a01dc9e4aafa78d612d2ce24d0a1fa283fa0dbbffbd30`. No raw frame, face, name, Drive identifier or manually transcribed deal was committed. These sources have not passed pixel recognition against independent truth and therefore do not count toward precision/recall or readiness.

| Required scenario | Current evidence | Result |
|---|---|---|
| previously successful complete deals | five development deals, 260/260 exact pairs | regression only |
| previously failed second deal | no frozen, independently labelled input available on this branch | blocked |
| multiple other videos | two private report exports inspected; recognition truth absent | blocked |
| resolution/scale/window shift | bounded synthetic anchor registration | contract pass; real holdout blocked |
| light/dark theme | intensity-inverted synthetic registration | contract pass; real holdout blocked |
| blur/partial/occlusion/transition | erased glyph and mid-play fail-closed checks; broader corpus absent | partial |
| missing/wrong anchor | missing and ambiguous synthetic anchors rejected | pass |
| visual/pointer conflict | deterministic `NEEDS_REVIEW` | pass |
| logical completion conflict/unknowns | complement isolated from visual/canonical output; incomplete evidence stays unknown | pass |
| repeated identical run | receipt and evidence report equality | pass |

## Why production remains blocked

1. Rank-template matching, suit location and the ordered deck bijection are correlated parts of one algorithm. There is no independent full-card channel, so this backend cannot satisfy the existing profiled challenger acceptance contract.
2. There is no frozen, human-verified, unseen multi-layout corpus meeting the current minimum support and 99.5% precision / 95% recall gate.
3. The regression covers one UI skin, one resolution and one source video. Voids in horizontal hands, other compass rotations, scaling, compression, overlap and additional lesson sources remain unproven.
4. Three checked deals have only one retained observation frame and do not meet temporal consensus.
5. Logically independent assurance has not reached I2.

Minimal next action: freeze a hash-bound train/test split from additional sources and implement a genuinely independent full-card classifier or formal checker. Until both pass, keep this module opt-in and shadow-only.

## Rollback

No runtime registration, migration or production artifact exists. Rollback is removal of the optional module, its isolated requirements file, tests and documentation, or simply not injecting its job runner. The default runtime requires no data restoration.
