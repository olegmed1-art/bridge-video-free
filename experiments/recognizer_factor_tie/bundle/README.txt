Code-only recognizer source bundle builder

This is an offline source packaging stage. It does not install, select, launch,
dispatch or authorize a recognizer. Geometry90 stays disabled and release HOLD
stays in force. The candidate flag for the factor tie correction describes the
prepared source format; it is not a statement that a live release was changed.

Inputs
- Exact saved Margin RELEASE.json, raw SHA af7b1e91f30e34943e3ae61e8d8e2c8ed8a6ee00929406862b735b538217f21c,
  and all nine files named by that saved source manifest.
- Exact base vendor tree plus CARD_WORKER_PINS.json, raw SHA
  00f35fa9afba2c6f6fd3f90fbb4531dd4ad493f48dddcb4655919a10c082a061.
- Explicit override manifest and its independently supplied raw SHA. Schema:
  recognizer-source-overrides/v1; files entries have target, absolute source,
  sha256 and bytes. Exactly five permitted targets are required: fixed factor,
  identity helper, compatibility helper, scorer and Margin candidate.

The builder checks raw SHA before JSON decoding or source parsing. It copies
only authenticated code/profile metadata, pins the two new vendor modules,
updates worker FROZEN and HELPERS atomically in source, and prepares matching
single-version IPC pins. It never adds an accept-both-old-and-new allowlist.
The source layer rejects null/invalid positive cards and original TIE results
before adding effective cards. HTML states that the diagnostic correction is
present in a candidate and that installation is not attested.

Build one new directory and archive (all paths are caller-supplied):
  python3 -B build_source_bundle.py --base-release SAVED_RELEASE \
    --base-vendor SAVED_VENDOR --base-pins SAVED_PINS \
    --overrides CHECKED_OVERRIDES --overrides-sha256 EXACT_RAW_SHA \
    --output NEW_OUTPUT_DIRECTORY

The archive has deterministic ordering, metadata and timestamps. The source
manifest has content SHA/size for every payload file. The output and archive
must both be absent; neither is overwritten. No sprite, frame, video, audio,
private labels, credentials, installer, ACTIVE entry or RERUN script is copied.

Verify without constructing a worker, observers, banks, sprites or transport:
  python3 -I -B verify_source_bundle.py --bundle NEW_OUTPUT_DIRECTORY \
    --manifest-sha256 SHA_FROM_BUILD_RECEIPT --receipt NEW_RECEIPT.json

This checks every source pin, actual strict vendor import closure and the
frozen observer's with_receipts API. Network is blocked in this verifier.
It does not run GeometryWorker.__init__, observe, pixel inference or ASR.

Compatibility boundaries
- A matching new IPC source pins only the new worker and vendor inventory.
- Old worker/client + new pins must reject. New worker/client + old pins must
  reject. This protects old artifact identity rather than claiming drop-in ABI
  compatibility with an old component manifest.
- A CLI command construction check is not real transport or owner authority.
- A matching caller/component descriptor and genuine owner bridge are still
  required before any later runtime integration; none is fabricated here.
- The sprite remains an external pinned prerequisite. Media, quality, human
  ground truth, live-state freshness and production E2E are not qualified by
  code import or packaging checks.

Rollback is to keep the existing selected release. This source package has no
installation or activation action to undo. Historical source trees and prior
candidate archives are retained unchanged.
