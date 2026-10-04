# Recognizer observation guards — review candidate, 2026-10-04

Base: `711ddd648fa74f2b903f9d7127dadc412f94b277`. Review scope is synthetic regression
coverage and orchestration/provenance fixes. This is not a production promotion or
a recognition accuracy claim. No host, IBM job, source media, Drive original,
school canon, default runtime, or deployment configuration is changed.

## Actual processing order

`run_master_3_1_free.py:364–366` obtains the transcript, selects visual evidence,
then derives deals. Transcript terms supply timestamps to the general visual
stage. The primary card recognizer scans video independently of teacher speech.
The active r26.3 wrapper selects `bridgit-primary-video-gambler-v2`; r26.4/v3 and
r26.5 speech reconstruction are separate candidates. A version such as r25/r26
names a pipeline revision, not an OCR model.

The stable primary route checks the Gambler profile and pinned rank assets,
samples table-region signatures, waits for a stable change, measures four hands,
recognizes two frames 600 ms apart, requires per-frame deal agreement and a
unique 52-card/13-per-seat layout, then groups identical deals. Card suits and
seats come from the supported layout. This is a restricted Gambler recognizer;
arbitrary diagrams, rotated layouts, partial hands and void-fan geometry are not
established supported inputs. Its similarity scores are not calibrated accuracy.
The primary adapter does not itself create a bidding sequence, contract, PBN or
DDS analysis. r26.5 applies teacher facts and deck complement after vision.

## Defects and proposed behavior

1. **P1: lost retry/new board.** Stable events consumed during the 15-second
   recognition cooldown never reached recognition. A board that appeared at
   5 seconds could wait for a watchdog near 188 seconds or disappear first.
   The candidate captures both real observation frames at the event and queues
   them with their original timestamps. Dispatch observes the cooldown. A retry
   follows rejection only while the same state remains current. The queue holds
   at most four pairs; overflow is explicit `PRIMARY_PARTIAL_COVERAGE`. EOF drains
   at most those four pairs. It does not replace missed images with a later board.
2. **P1: one duplicate pair erased earlier deals.** Known duplicate-byte/pixel
   evidence errors used to escape the whole pass and become an empty
   `UNAVAILABLE` result. They now reject only that observation. Unrelated backend
   and integrity errors propagate; they are not reported as absence of cards.
3. **P1, experimental r26.5: inferred fourth hand became VISUAL.** The wrapper
   reconstructed from completed `hands` rather than `visual_hands`. It now saves
   the observation boundary for full, partial and conflict results; reconstruction
   is repeatable. Records marked as reconstructed but missing visual evidence
   fail closed. Derived and speech cards retain their separate provenance.
4. **P1, experimental r26.5: non-facts became card facts.** Seat/card co-occurrence
   admitted “У севера нет туза пик”, English negation, questions and hypotheses.
   A bounded direct possession grammar now rejects unknown context, internal
   sentence boundaries, negatives and questions before declarations are created.
   This deliberately reduces recall; it is not general language understanding.
   Teacher-role threshold, reliability checks and the deal-time window still
   apply. Invalid timestamps reject speech rather than adding cards.

## Candidate boundary and remaining limitations

The v2 change lives in `bridgit_primary_video_candidate.py` with an explicit
candidate adapter. Frozen source/hash contracts remain unchanged. Shared
geometry, rank assignment and acceptance helpers are reused from the frozen
implementation. No default runtime imports the candidate.

The candidate now owns its selector type, so the historical r26 global
retry patch cannot replace its observe method. Input loaders no longer import
the r264 production/video adapter. The gold builder named r264 is reused only
for unchanged pinned v2 geometry and its input/output error boundary.

`bridge_runtime_hardening_r26_candidate.install` requires the exact explicit
candidate revision. It installs the r25.16 predecessor and exactly one v2
adapter, refuses a base with an existing historical primary adapter, restores
the requested revision on failure and refuses retry after partial installation.
It has no `run` entry point and is not selected by any default runtime.

Candidate results belong to each base installation and job. A failed/foreign
job cannot reuse previous primary deals; a report with a foreign or absent job
ID receives NOT_RUN and zero primary deals. Repeated derivation does not append
to the inherited result list. Repeated installation verifies the same hooks
and token provider. A process-wide gate rejects overlapping candidate visual
calls, including calls through separate base installations: native geometry
temporarily patches shared rank helpers. Parallel candidates require separate
processes. This gate does not make mixed historical/candidate runtimes safe.
Unopened video/invalid metadata are input unavailability and release the
decoder. Reference/integrity/program failures still propagate.

This is preparatory integration, not production enablement. Fresh isolated
evaluation remains required for golden/holdout replay. Queue overflow can still
lose coverage explicitly; increasing capacity or lifting the cooldown needs
target-host resource measurements.

Neither r26.4 nor r26.5 is permitted for production (`production_allowed=false`
in their existing contracts). The speech grammar cannot recover an ASR-dropped
negation or resolve discourse/deal-boundary ambiguity within the 120-second
window. These require real transcript evaluation. Repeated identical deal hashes
still share a group even if they occur in separate lesson episodes.

## Validation and promotion prerequisites

- New tests are synthetic control-flow, speech and deck-oracle regressions. They
  cover the original nine audit witnesses as expected guards, including the
  unsupported-void abstention boundary. Frame decoding and rank recognition are
  mocked in orchestration cases; no pixel-accuracy claim follows from them.
- The preceding candidate passed 84 new guards and 106 tests including the
  existing r264/r265 suites, with different-model I2 review. This integration
  adds selector-installation, job-isolation, decoder-input and runtime-wrapper
  regressions. The current exact-head CI and I2 results are recorded in the PR;
  prior results do not establish that changed code has passed.
- The local executor for this integration is unavailable due to an ACL setup
  error. Source is read/published through GitHub and execution occurs in Linux
  CI. No local integration run or newly available private fixture is claimed.
- Original Windows audit: **274 passed, 10 failed, 7 skipped, 1 collection error**.
  Nine failures require POSIX facilities/privileges; one needs OpenCV. The
  collection error is a missing `requests` dependency. These are pre-existing
  environment limitations, not accepted new failures. Historical source bytes
  were checked against Git objects so CRLF did not masquerade as a source change.
- The added Linux workflow checks out the exact PR head and frozen base in
  separate directories. It runs the same 25 existing test modules on both,
  then the new guard suites. JUnit, tested commit IDs, skips and failure-set
  comparison are retained as artifacts. All steps must pass for a green gate;
  baseline failures are classified, never silently suppressed.
- The prior historical 52/52 observation is one known control deal, not a holdout.
  The existing canonical promotion gate is `BLOCKED_HOLDOUT`. This patch does not
  change that gate or substitute synthetic decks for held-out recognition data.
- The newly supplied March lesson was not tested: media bytes are unavailable
  through the authorized connector. No workaround was attempted. A later permitted
  IBM window and an accessible approved fixture corpus are prerequisites for
  target-host pixel evaluation, resource measurements and promotion discussion.

Rollback is to omit the explicit candidate adapter and discard this review
branch. No runtime promotion or main merge is part of this change.

## Future v2-only enablement

Recover and hash-verify the previously approved gold package, reference/profile
and v5 sprite; rerun both currently skipped asset cases and the known 01:55:35
control on old v2 and the candidate in separate processes. That known 52/52
result is a regression control, never a holdout.

Freeze independent gold before output using the existing r263 promotion
contract: 24 cases from 4 sessions, 12 complete and 12 non-complete cases,
3 resolution groups, 2 layout/theme families when available and at least
4 complete void deals. Preserve precision >=0.995, visible recall >=0.95,
zero seat errors and zero false completes. Do not remove unsupported void
cases from the denominator. Keep canonical promotion separately closed.

After any integration change, rerun golden/holdout, exact-head Linux checks and
independent I2. Only after explicit coordination, an authorized IBM window and
a bounded canary may a reversible v2 route switch be considered. Record source,
runtime/profile/asset digests and CPU/RAM/coverage; retain the previous immutable
runtime for rollback. Temporal union, fourth-hand inference, r264/r265
installers, speech reconstruction and v3 production remain off.
