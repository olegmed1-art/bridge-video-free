# Autonomous Drive processing: durable finalization

This is an opt-in worker capability, not an enabled deployment or live IBM
credential receipt. Oracle owns the serialized control queue; the heavy worker
pulls a copy directly from Drive, processes locally, then creates separate Drive
outputs. Video bytes never pass through LLM context or require chat attachment.
Existing resident authorization must be independently verified before launch.
No new key, OAuth grant, scope, instance or production activation is introduced.

## Source and storage contracts

The original is immutable: no overwrite, PATCH, DELETE, move or rename. Intake
pins ID, name, parents, version, modified time, size and computed SHA-256 in
`SOURCE_INTEGRITY.json` before exposing the final staged copy. Complete cache
reuse requires this pin plus matching current metadata and local SHA. Transfers
stream in 8 MiB chunks; 400–600 MB and 2 GB inputs fit existing code ceilings,
subject to per-job byte/time/disk budgets. An interrupted private `.part` starts
again at byte zero. There is no unbound Range resume or implicit larger budget.

Output routing is supplied by an operator-owned, non-group/world-writable file
named by `UNIVERSAL_VIDEO_DRIVE_BINDINGS_FILE`. Queue input cannot grant upload
authority. With no registry, the worker retains local evidence and does not
upload or delete its Drive input copy. No real folder IDs belong in Git.

```json
{
  "schema": "universal-video-drive-bindings-v1",
  "jobs": [{
    "job_id": "synthetic-job",
    "source_file_id": "synthetic_source_001",
    "job_hash": "<exact canonical job SHA-256>",
    "processing_upload_authorized": true,
    "folders": {
      "school_root": "synthetic_school_root",
      "processing": "synthetic_processing",
      "video": "synthetic_video",
      "source_job": "synthetic_source_job",
      "transcript": "synthetic_transcript",
      "frames": "synthetic_frames",
      "analysis": "synthetic_analysis",
      "checks": "synthetic_checks"
    }
  }]
}
```

All folder IDs must be distinct and exclude the original ID. The worker checks
the exact parent chain, private ACL and output-folder write capability. It uses
these existing folders without creating sibling duplicates. Names contain the
job ID and complete artifact-set hash; retry reuses identical files and rejects
duplicates or conflicting bytes. Only the compact transcript/frames/analysis/
quality allow-list is uploaded, not raw media, secrets or internal notes.
Each file is at most 5 MiB, total at most 256 MiB. Large result files block
publication rather than silently raising caps or truncating evidence.

## Commit and recovery sequence

1. Hold an exclusive source/container worker fence. Verify the pinned original
   with full remote-byte SHA and metadata before/after the read, before writes.
2. Verify the completed local result and server review with existing conformance
   gates. Technical completion does not establish teacher identity or canon.
3. Upload/create or reuse exact output items. Check ID, name, parent, private ACL,
   size, provider MD5 and SHA property, then stream-read actual bytes and compute
   SHA independently. Recheck metadata around each read; client SHA properties
   alone cannot prove content. Recheck all files after the entire transfer set.
4. Re-read/hash the unchanged original, persist an idempotent per-job marker in
   the exact checks folder, read back its bytes, reverify all output items and
   the original, then atomically fsync `DRIVE_FINALIZATION.json` locally.
5. Commit/fsync the worker's terminal done receipt. Only then may `finally`
   remove this job's staged input copy when the source/output proof matches.
   No success is inferred from an upload response or a half-written receipt.

On retry, a previous local PASS becomes `REVALIDATING` while its prior receipt is
preserved. A failed revalidation cannot leave a stale PASS authorizing cleanup.
Network/timeout failures receive at most three finalization attempts, at least
60 seconds apart, with a durable attempt record written before publication.
Authorization failures, hash/identity conflicts and exhausted retries retain
evidence and fail closed. Crashed running jobs are recovered through the existing
orphan mechanism. Completed source-bound packages enter finalization only, never
ASR again. A changed runtime or partial/conflicting package cannot erase the sole
local evidence: use a separately reviewed new job ID for recomputation of a
conflicting complete package. A pre-manifest compute crash preserves its entire
partial tree under the job's recovery directory and permits one bounded recovery.
A durable PREPARED quarantine transition survives interruption before/after
rename. STARTED without a completed package is an unknown compute outcome;
retain both trees and fail closed rather than repeating ASR without a bound.

Generic maintenance cannot TTL-delete managed Drive input directories or
unpersisted partial results. The finite outer compute/stop deadline remains
mandatory even if persistence fails; retain disk evidence and report the gap.
No server power or cleanup is performed by this code change itself.

## Validation boundary

Protocol/error tests use a fake Drive and generated fixed chunks, including
400/600/2048 MiB hash streams. They are synthetic tests, not throughput, resident
auth, real teacher, actual media replay, or publication receipts. Live readiness
still requires fresh IBM auth/runtime/storage attestation, a reviewed finite job,
source evidence and independent assurance. Canon activation remains disabled.
