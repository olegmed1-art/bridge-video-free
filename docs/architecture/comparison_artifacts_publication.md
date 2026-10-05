# Typed comparison evidence publication

Scope: source-only opt-in integration, no IBM start, existing auth changes,
Drive folder creation, cleanup execution or canon activation. This is an ASSURED
publication/retention change. Rollback is revert of this isolated PR; retained
original source/results are never deleted by the builder.

The reviewed offline runner is PR #2120 at
1243f2feab9998ca62dfa77a3452881f440657c7. It is an external input producer,
not assumed merged or installed. Its primary-visual schema is recognized
explicitly; auction/ASR/other future result families need their own versioned
validator. No auction algorithm is added here.

## Bound and lossless transport

A first 60–90-second replay has no measured size receipt yet. Runner limits
are per-child PNG counters, not a bound on the complete two-child tree. Thus
neither a guaranteed fit nor actual throughput is claimed.

The total existing compact publication cap remains **256 MiB combined across
transcript, frames, server review, comparison and completion marker**. No per-file upload limit is
raised. Original comparison bytes are concatenated in canonical filename order
and split into deterministic **4 MiB** binary parts, each below the existing
5 MiB multipart cap. This supports a large PNG without modifying, recompressing,
dropping or truncating it. The index is at most 1 MiB, counts at most 4096 files
and 64 parts, and binds complete byte offsets, sizes and SHA-256.
An attached package reserves 1 MiB of that same cap for the completion marker;
core plus package is capped at 255 MiB, marker at 1 MiB. Over quota fails closed
and retains all original comparison evidence.
Parts are opaque; no archive extraction or execution is needed.

Only a declared package at comparison/index.json and exactly numbered parts
are accepted. No general allow-list is expanded. Undeclared, extra, missing,
symlinked, hardlinked or traversing paths fail. Duplicate JSON keys,
nonfinite values and ambiguous names are rejected. Original file hashes and
every part hash are independently checked again when collecting the package,
including publication and cleanup proof matching. Decoded JSON keys and string
values are screened directly, including Unicode escapes and escaped whitespace.

The schema requires actual PAIR_CAPTURED events, both exact attempted PNGs,
decoded-frame inventories and backend outcomes. Rejected pairs are retained.
Runner capture occurs before backend/temp cleanup; the packaging validator
cannot reconstruct evidence a producer has already removed.

## Inputs and composition

Call universal_video.comparison_artifacts.build_comparison_package with:

- comparison_dir: complete private output tree produced by the pinned runner;
- destination: result_dir / "comparison", outside the input tree and nonexistent;
- sealed_manifest_path and SHA: frozen pre-output runner manifest;
- runner_commit and runner_sha256: actual installed source and script bytes;
- job_id/job_hash: the exact parent Universal job;
- source_file_id/source_version/source_sha256: actual immutable original pin;
- clip_binding_path and clip_binding_sha256: independently reviewed source-to-clip receipt.

The builder returns exactly:

    {"schema": "universal-video-comparison-artifacts-v1",
     "directory": "comparison",
     "manifest_sha256": "<actual index SHA256>"}

Attach this as manifest.comparison_artifacts **before** generation conformance
and build_server_review. The existing collector and conformance then include
index/parts in the same inventory and server-review digest. Attaching to an
already reviewed/terminal bundle invalidates its old review and receipt; it
is not an activation instruction or a safe substitute for the normal admission
and recovery path.

The clip-binding receipt is a separate prerequisite; this module does not
invent, produce or approve it. Required schema:

    {
      "schema": "universal-video-source-clip-binding-v1",
      "status": "PASS",
      "source_file_id": "<pinned original ID>",
      "source_version": "<pinned version>",
      "source_sha256": "<actual full original SHA256>",
      "clip_sha256": "<actual clip SHA256>",
      "source_offset_ms": "<actual integer offset>",
      "duration_ms": "<actual integer 1..120000>",
      "verification_receipt_sha256": "<independently obtained mapping evidence digest>"
    }

Supply concrete integer values, not the explanatory strings above. The receipt
and its digest must come from the separately qualified original-to-clip
verification process. Author-declared offset or OpenCV decoder position alone
does not establish this proof. The original video SHA, clip SHA and runner
script SHA are different bindings. This module checks linkage; actual evidence
and independent review remain an IBM runtime/intake prerequisite.

## Logs, destinations and durable gates

Any **nonempty raw process.log blocks packaging**, even a newline or ordinary
diagnostic text. It is retained unchanged locally, never uploaded or silently
redacted. This makes secret safety explicit rather than relying on a token
regex for arbitrary logs. JSON is additionally scanned for credential-like
fields/strings; this is a conservative refusal check, not a secret classifier
guarantee. Run the comparison in the already qualified isolated offline
environment without ambient credentials. A producer that writes nonempty logs
needs a separately reviewed typed diagnostic output, not a widened log policy.

Existing protected Drive binding and source verification remain mandatory.
Parts route to the existing analysis role; index routes to checks. This code
creates no folders or grants. The separate folder owner must confirm those
bindings; no real IDs appear here.

The legacy publish CLI rejects all comparison packages, including dry-run
readiness, before any Drive authorization or folder creation. Only the protected
durable route may publish them.

The durable finalizer performs create/reuse plus actual remote-byte readback
for every part/index and the final marker, with stable original-source checks.
Comparison bytes participate in the complete artifact-set digest, so retry
reuses identical names and a modified/missing pair blocks cleanup.
A stale PASS is demoted during revalidation. Done receipt/cleanup intent gates
remain unchanged; no new cleanup authority is introduced.

CAPTURED_UNSCORED is an unscored execution state, not recognition accuracy,
teacher verification, pedagogical benefit or canon PASS. REPLAY_ERROR,
ERROR, TIMEOUT and PROCESS_ERROR output cannot establish a complete evidence
package: packaging/publication/cleanup fail closed and retain the whole local
tree. A separately reviewed diagnostic/quarantine transport may preserve failed
output in Drive without granting cleanup; it is not introduced here. Visual source evidence
is preserved even when the recognizer rejects all pairs. ASR, auction,
cross-job wrapper-state tests and the full frozen holdout remain separate.

## Validation boundary

Only synthetic generic fixtures are public. Tests cover lossless multi-part
roundtrip, rejected pairs, source/job/version/gold linkage, exact inventories,
traversal/links, secret-like JSON and nonempty logs, combined quota, conformance/
server-review binding, idempotent mocked Drive publication, readback interruption
and cleanup invalidation. Existing Linux CI is bounded to 20 minutes.
Local execution is unavailable due executor ACL initialization; no alternate
desktop/server route is used. CI and independent audit receipts must be reported
with exact head SHA. None of these tests establishes real IBM auth, a source
replay, Drive publication or canonical activation.
