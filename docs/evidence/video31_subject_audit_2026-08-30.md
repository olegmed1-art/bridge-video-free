# Video 3.1 FREE subject audit — 2026-08-30

Verdict: **BLOCKED / no production activation**.

This audit covers subject correctness only. Container installation, Oracle service state,
systemd, spool permissions and container promotion belong to the container workstream.
No media, ASR, Drive write, Neon write or next-video request was executed for this audit.

## Evidence hierarchy

Repository code proves implementation, tests prove bounded contracts, and real-video
receipts prove field behaviour. None of those layers substitutes for another. Unit tests
alone cannot produce `PARITY_PROVEN`.

| Capability | Code | Tests | Real video | Oracle runtime | Parity | Current blocker |
|---|---:|---:|---|---|---|---|
| Source identity | yes | yes | PASS binding in #819 | shadow wired | not proven | schema-bound parity receipt missing |
| Offline ASR | yes | yes | unavailable | shadow wired | not proven | no readable real ASR evidence |
| ASR QC | yes | yes | unavailable | shadow wired | not proven | no real QC metrics |
| Diarization | yes | yes | unavailable | shadow wired | not proven | no real speaker evidence |
| Named speaker | no | no | unavailable | missing | missing | no Oracle identity overlay |
| Role attribution | partial | yes | unavailable | shadow wired | partial | teacher/student evidence unavailable |
| Frame extraction | yes | yes | FAIL in #881 | shadow wired | partial | `UV_WORKER_IO_FAILED` owned by container chat |
| Card recognition | partial | yes | FAIL in #842/#843 | component only | partial | precision/recall gates failed |
| Seat geometry | yes | yes | inconclusive bounded sample | component only | partial | independent holdout missing |
| Board/dealer/vulnerability | partial | yes | unavailable | component only | partial | source-bound real evidence missing |
| Auction extraction | no in main | no in main | unavailable | missing | missing | implementation remains in #846 |
| Deal validation | yes | yes | unavailable | component only | partial | not wired to video result |
| PBN | no in main | no in main | unavailable | missing | missing | implementation remains in #846 |
| Bridge semantics | no on Oracle route | no | unavailable | missing | missing | semantic stage absent |
| Methodology analysis | no on Oracle route | no | unavailable | missing | missing | methodology stage absent |
| Learning episodes | adapter only | yes | unavailable | component only | partial | verified upstream interactions absent |
| Drive artifacts | yes | yes | unavailable | shadow wired | not proven | #881 produced no publication |
| Terminal receipt | yes | yes | INCONCLUSIVE in #819 | shadow wired | not proven | no unique schema-bound receipt |
| Idempotent repeat | yes | yes | unavailable | shadow wired | not proven | real repeat proof missing |

## Pull-request lineage

- **#846 is the primary end-to-end candidate**, because it connects profile-bound pixels,
  board metadata, independent visual channels, auction validation, deal evidence, PBN and
  review output. It is not mergeable as a whole onto current `main`: its tree predates the
  current container, parity and Evolutionary Course changes.
- **#841 remains the exact TP/FP/FN evaluator source.** Its useful evaluator and tests are
  not present in current `main`.
- **#842 remains the current Bridgit pixel implementation source.** Its real probe is FAIL
  (`TP=2`, `FP=0`, `FN=9`), so it cannot be activated.
- **#843 is partially incorporated conceptually by #846** for transcript, world-card,
  auction and PBN work, but its dense SHADOW evidence and temporal components require an
  explicit transfer review.
- **#741 contains the labelled holdout gate.** Do not close it until that evaluator is
  transferred or replaced by an equal strict gate.
- **#709/#732** use complete OCR tokens and do not solve Bridgit graphic cards. Preserve
  them until their opt-in tests and any non-Bridgit utility receive an explicit disposition.
- **#734/#738** are earlier composition/backend generations. They are superseded candidates,
  but must remain open until useful contracts are verified in #741/#842 or current `main`.

## Real evidence

- Issue #819: exact source/runtime identity passed, but ASR, speakers, cards and a unique
  schema-bound receipt were unavailable. Subject verdict: `FINAL INCONCLUSIVE`.
- Issue #881: the resumed Diana 13 frame canary ended `FAILED` with
  `PermissionError / UV_WORKER_IO_FAILED`. No card, frame, ASR or pedagogical PASS follows.
- PR #842 bounded field probe: precision 1.0, recall 0.1818, seat errors 0. This is FAIL
  because recall is far below 0.95.
- PR #843 Diana 14 SHADOW probe: precision 0.9167, recall 0.8462 with two labelled hands.
  This is FAIL because precision is below 0.995 and recall below 0.95.

## Next subject implementation sequence

1. Port #841 exact evaluation and #741 independent holdout contracts onto current `main`.
2. Port the fail-closed rank/suit/full-card channels from #842 without default activation.
3. Port #846 in bounded layers: board metadata and geometry, auction, deal evidence/PBN,
   then review artifact wiring.
4. Build immutable human-labelled train/holdout manifests under #421/#742.
5. Await the existing #881 job boundary from the container chat; do not launch Diana 14.
6. Only after a real independent holdout meets precision ≥ 0.995, recall ≥ 0.95 and zero
   seat errors may individual capabilities receive structured parity receipts.
