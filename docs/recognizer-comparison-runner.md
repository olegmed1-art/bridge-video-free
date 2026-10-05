# Bounded recognizer comparison — offline handoff

This tool captures primary visual-card evidence and permits optional visual
auction extraction from an embedded `profile["auction"]` when the selected runtime
supports it. It does not run ASR, the job publisher, DDS, hidden-hand inference or
a production server. It does not score accuracy or authorize promotion. No
supplied lesson has been tested by preparing this runner.

Runner revision: `recognizer-comparison-v1-auction-scope-r3`. Seal, worker status
and comparison summary use
`PRIMARY_VISUAL_WITH_OPTIONAL_EMBEDDED_PROFILE_AUCTION; NO_ASR_DDS_OR_PUBLISHER`.
This describes permitted processing, not successful observation. Each returned
worker reports `auction_result_status` copied from its raw result, or
`NOT_REPORTED` when that runtime has no auction result. An absent profile,
abstention, returned result or permitted scope is not a recognition success.

Original pinned card-comparison snapshots:
- baseline main: 546f52d6bab7f836dcb4f6325a6caa79c982f16e, r26.3/v2;
- candidate PR #2103: 74c98215d433c0383a8cc1021b4a24f9caade8fe.
The candidate loader also accepts the exact opt-in revision
`3.1-free-r26.3-auction-candidate3` and sets that revision before installation.
It continues to accept candidate2; baseline accepts only r26.3. The original
candidate commit above is candidate2 and does not contain candidate3. A candidate3
replay requires its own clean, separately reviewed exact-SHA checkout in the
manifest. No candidate3 publication or runtime compatibility is attested by this
source-only delta. The live IBM runtime is not attested here. Retain its receipt
separately before claiming that a replay represents the deployed runtime.

## Inputs and launch

Provision two clean, detached checkouts at these exact commits. Keep inputs and
output outside both checkouts. Use the same Python environment for both children:
Python 3.12, OpenCV, NumPy, Pillow and the repository runtime import dependencies.
The dedicated CI workflow records the bounded dependency set used in tests.

Provide an unmodified native clip of at most 120 seconds, the approved reference,
validated profile, SHA-pinned original Gambler sprite and independently annotated
gold. Keep original source identity/clip offset and annotation taxonomy in gold.
Freeze gold and its digest before inspecting either recognizer output. This
runner records that declaration and hash before starting children; it does not
prove the annotator was blind. Gold contents and even its path are not passed to
either recognition child.

Manifest structure (replace every placeholder with an actual absolute path/hash):

    {
      "schema": "recognizer-comparison-v1",
      "case_id": "lesson-first-clip",
      "source_offset_ms": 0,
      "gold_frozen_before_outputs": true,
      "baseline": {
        "root": "/work/recognizer-baseline",
        "sha": "546f52d6bab7f836dcb4f6325a6caa79c982f16e"
      },
      "candidate": {
        "root": "/work/recognizer-candidate",
        "sha": "74c98215d433c0383a8cc1021b4a24f9caade8fe"
      },
      "inputs": {
        "video": {"path": "/approved/clip.mp4", "sha256": "<SHA256>"},
        "reference": {"path": "/approved/reference.png", "sha256": "<SHA256>"},
        "profile": {"path": "/approved/profile.json", "sha256": "<SHA256>"},
        "sprite": {"path": "/approved/all-v5.png", "sha256": "<PINNED_SHA256>"},
        "gold": {"path": "/approved/gold.jsonl", "sha256": "<FROZEN_SHA256>"}
      }
    }

After the input/gold manifest is sealed, retain its SHA256 out of band:

    python -I -B /work/recognizer-runner/tools/recognizer_compare.py compare \
      --manifest /approved/manifest.json \
      --manifest-sha256 <SEALED_MANIFEST_SHA256> \
      --output /private-results/lesson-first-comparison \
      --timeout 300

The output directory must not exist. The parent checks clean exact-SHA
checkouts and input hashes before and after each child. Child processes have
different job IDs and output directories. Each calls the actual revision's
install function once, then only recognize_video_primary with the production
adapter settings (scan 1000 ms, attempt gap 15000 ms, card size 109x147).
The historical r26.3 geometry and retry patches are therefore included.
Credential requests and Python socket/exec/spawn calls in a child are refused
by audit hooks. This is not an OS network sandbox for native extensions;
no production run/process_job entry point is called.

For a no-media runtime import/install inspection:

    python -I -B /work/recognizer-runner/tools/recognizer_compare.py inspect \
      --root /work/recognizer-baseline \
      --sha 546f52d6bab7f836dcb4f6325a6caa79c982f16e --variant baseline
    python -I -B /work/recognizer-runner/tools/recognizer_compare.py inspect \
      --root /work/recognizer-candidate \
      --sha 74c98215d433c0383a8cc1021b4a24f9caade8fe --variant candidate

## Evidence and interpretation

- seal.json: runner/source/input/gold digests, pre-output seal time and scope.
- baseline/<job>/ and candidate/<job>/: separate process logs, worker status,
  result.json on return, recognizer screenshots and retained evidence.
- evidence/decoded/: every frame actually decoded by the scan/pair reader,
  including frames later rejected by geometry or discarded from the queue.
- evidence/attempts/<n>/: byte-identical copies of both actual backend input PNGs
  before their temporary directory disappears; file and decoded-pixel hashes,
  observation timestamps, backend result or recorded exception.
- evidence/events.jsonl: durable frame, selector, geometry, backend and acceptance
  events; logical scan time, preceding attempt time and queue depth when available.
  Requested timestamps plus original offset and OpenCV's reported decoder
  position are distinct. The source offset is supplied by the manifest author;
  verify the clip-to-original mapping separately. OpenCV position is NOT
  independently verified source PTS.
- comparison.json: both process outcomes, never an automatic accuracy PASS.
  CAPTURED_UNSCORED means both calls returned, even if neither accepted a deal.
  REPLAY_ERROR retains the other version's run and all evidence already written.

The observer copies arrays/files without replacing recognizer inputs or results.
A recorder failure aborts rather than being swallowed by historical broad catches.
Limits are 512 frame writes, an accumulated PNG counter of 512 MiB, and
120 seconds of media. The PNG limit is checked after each write (one PNG can
overshoot before the replay aborts); logs and JSON are not included in that
counter, so this is not a total disk quota. The
per-child timeout is 300 seconds by default (maximum 900). Reaching a limit is
an incomplete replay, never an accuracy pass. Observer I/O changes wall time:
elapsed_seconds_with_observer is not a production performance benchmark.

The direct primary pass can expose a baseline exception that the full historical
production adapter would catch as UNAVAILABLE. Treat that as primary-pass evidence,
not a full application/job result. This runner does not exercise cross-job wrapper
state, ASR, teacher roles or PDF output; their existing separate tests and the
acceptance rubric remain required. Visual auction extraction can run within the
primary pass with candidate3 and an embedded calibration profile. Its raw result
and recognizer-owned evidence remain in the private output. The recorder's PNG
counter does not include auction-owned evidence; candidate3's own observation
budget still applies.

Minimum useful real sample: one native continuous 60–90 second segment with two
distinct complete layouts and a partial/no-card interval, plus six independently
chosen gold frames. Keep 20–30 seconds of relevant speech separately if needed.
The short-lived-board witness requires an eligible stable event during an actual
preceding attempt's cooldown and disappearance before dispatch is possible.
Six selected gold frames alone cannot establish this; use the actual trace.
Natural duplicate pixels must remain unchanged. Missing triggers are NOT_COVERED.

Score TP/FP/FN per annotated board occurrence, not repeatedly per supporting
frame; wrong seat contributes FP and FN. Require zero wrong emitted card/seat and
false-complete cases in the smoke sample. A claimed complete layout must match
all 52 unique observed cards, 13 per seat. Abstention on partial/unsupported
inputs can pass safety but still loses recall. Never use DDS to repair cards.
No automatic percentages are produced here. The existing frozen 24-case /
4-session holdout gate, precision >=0.995, recall >=0.95, zero seat errors and
false completes remains separate and unchanged.

Keep real clips, frames, transcripts and results in the approved private output
location. The review/CI contribution contains only code, docs and tiny generated
control-flow fixtures; it never uploads user media.


## Candidate3 regression gates

Test launch hooks patch only `runner.launch_worker`. They leave the shared
`subprocess.run` unchanged, including calls made internally by
`subprocess.check_output` during Git checkout validation. A separate control
installs a rejecting worker hook and exercises a real `git --version` probe.

The ordinary runner suite includes revision allow/reject checks and a compare
control with an embedded auction profile. That control uses fake recognition and
launch ports while exercising the real loader, worker, recorder and receipt
persistence; it is not a pixel recognition test.

The additional test below uses actual runtime installation in two isolated
offline worker processes, a three-frame lossless synthetic video, the real raw
scan and AuctionObserver, and the real compare/seal/result path. It substitutes
only card calibration/assets and card event selection to isolate auction wiring.
It checks a complete 1S XX auction without accepted hands and retains/hash-checks
its frame evidence. It does not test production cards, real lesson accuracy or
the full job pipeline.

Provide all four variables for separately prepared clean checkouts:
RECOGNIZER_BASELINE_ROOT, RECOGNIZER_BASELINE_SHA,
RECOGNIZER_AUCTION_CANDIDATE_ROOT, RECOGNIZER_AUCTION_CANDIDATE_SHA.
The candidate checkout must contain the reviewed main auction patch, including
its synthetic fixture helpers. Both SHAs must be exact commit IDs. Then run:

    python -B -m pytest -q -ra -p no:cacheprovider tests/test_recognizer_compare_auction_integration.py

Without the explicit candidate3 root this gate skips; a skip is NOT compatibility
evidence. The existing pinned candidate2 CI job does not execute this additional
file. Before any compatibility claim, require this gate to execute with zero
skips and retain its checkout SHAs and test receipt. These tests are authored but
not executed in the restricted source-handoff session.
