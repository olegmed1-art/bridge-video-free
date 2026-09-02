# Video 3.1 FREE — subject parity matrix

Audit date: 2026-08-30  
Fresh `main`: `ea221bd75132c12d870b65f25d9aa915c717c342`  
Target: PR #890  
Overall verdict: **FAIL — production activation forbidden**

| Capability | Code | Tests | Real video | Runtime | Parity | Blocking fact |
|---|---|---|---|---|---|---|
| Source identity | Implemented | Covered | Partial request receipt | Wired | `PARTIAL` | Missing per-job runtime revision and complete result manifest |
| Offline ASR | Implemented | Covered | Absent | Wired | `IMPLEMENTED_NOT_PROVEN` | No Diana 13 transcript locator/hash/duration |
| ASR QC | Implemented | Covered | Absent | Wired | `IMPLEMENTED_NOT_PROVEN` | No retained real QC metrics |
| Diarization | Implemented | Covered | Absent | Wired | `IMPLEMENTED_NOT_PROVEN` | No real speaker/timestamp/confidence evidence |
| Named speaker | Missing | UNKNOWN gate covered | Absent | Not wired | `MISSING` | No private source-bound identity proof |
| Role attribution | Implemented | Covered | Absent | Shadow | `IMPLEMENTED_NOT_PROVEN` | No real exact-segment teacher/student evidence |
| Frame extraction | Implemented | Covered | Absent | Wired | `IMPLEMENTED_NOT_PROVEN` | No Diana 13 timestamp/SHA frame manifest |
| Card recognition | Partial | Synthetic | Absent | Not production-wired | `PARTIAL` | Independent gold/holdout metrics unavailable |
| Seat geometry | Partial | Synthetic | Absent | Shadow | `PARTIAL` | No real orientation holdout; seat errors unmeasured |
| Board/dealer/vulnerability | Partial | Synthetic | Absent | Open PR only | `PARTIAL` | #933 unmerged; no real field evidence |
| Auction extraction | Partial | Synthetic | Absent | Open PR only | `PARTIAL` | #940 unmerged; no real two-channel auction |
| Deal validation | Partial | Synthetic | Absent | Open PR only | `PARTIAL` | #950 safe chain unmerged; no verified full deal |
| PBN | Partial | Synthetic | Absent | Open PR only | `PARTIAL` | No real complete legal auction/deal |
| Bridge semantics | Partial | Component | Absent | Component only | `PARTIAL` | Mechanics exist, full lesson semantics do not |
| Methodology analysis | Missing | No E2E | Absent | Not wired | `MISSING` | No verified methodology result |
| Learning episodes | Partial | Synthetic | Absent | Open PR only | `PARTIAL` | #977 unmerged/unreviewed; no real episode |
| Drive artifacts | Implemented | Covered | Absent for Diana 13 | Wired | `IMPLEMENTED_NOT_PROVEN` | No approved exact-job result bundle |
| Terminal receipt | Implemented | Covered | State/error only | Wired | `PARTIAL` | No complete subject-evidence receipt |
| Idempotent repeat | Implemented | Covered | Not proven | Wired | `IMPLEMENTED_NOT_PROVEN` | Repeated intake failures are not replay proof |

## Measurable quality gate

`precision >= 0.995`, `recall >= 0.95`, `seat_errors = 0` cannot be evaluated:
there is no independent human-verified holdout item evidence. TP, FP and FN are
not computable. The correct status is `INCONCLUSIVE`, never PASS.

## Canary and coordination facts

- Diana 13 remains `INCONCLUSIVE`; the available receipts do not contain the
  transcript, speakers, frame manifest, card observations, board context,
  auction, deal, PBN or learning episodes.
- Four Diana 14 requests are recorded in issue #634 after the prior NO-GO.
  All four ended `UV_SUBMIT_COMMAND_FAILED`; subject matter was not assessed.
- These failures provide no positive parity evidence and do not authorize a
  fifth retry or Diana 15+.

The machine-readable source is
`docs/evidence/video31_subject_parity_matrix_2026-08-30.json`.
