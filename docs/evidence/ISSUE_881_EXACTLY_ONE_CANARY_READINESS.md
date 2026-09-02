# Issue #881 — exactly-one-canary readiness contract

This change prepares one bounded media canary and intentionally does not run it.

## Fixed source and destinations

- Source file ID: `198-2v3JBlNQobdsPYQQWzrrCqQ1zBZOI`
- Source name: `Диана 13.mp4`
- Source MIME: `video/mp4`
- Source size: `696237577` bytes
- Source parent: `1Fr-H2NgBKEpp3q_H4FzNmQwCV6bj2x6b`
- Result folder: `1bWSCt1-dao-CiWrm14M0NsWJNjXJthhP`
- Work folder: `1wuePp9ka9yR3pyTNc7KvUoi5wzvavs7m`

The readiness workflow obtains the provider checksum through a metadata-only
`files.get`. It does not download source bytes. The generated
`exact_single_canary_request.json` binds all six source identity fields to one
runtime SHA and one local image digest.

## Exactly-one isolation

The request is not built by listing the source folder. It materializes a
one-item manifest from the exact source metadata and uses the isolated queue
profile `bridge_3_1_free_exact_canary`. The resident worker claims only
`bridge_3_1_free`, so it cannot race for this job. A successful one-item canary
must report `released_jobs=0` and terminal batch state `REVIEW`.

After a separate Director GO, the bounded execution sequence is:

1. Verify the image by immutable `sha256:` digest and the matching source-commit label.
2. Re-read source identity and enqueue the already generated one-item request.
3. Invoke `python -m universal_video.exact_canary_worker` once in that exact image.
4. Stop. No resident loop and no remaining batch are released.

No request is enqueued by this pull request or its readiness workflow.

## Result PASS contract

`REVIEW_READY` is impossible unless the worker has:

1. routed the master PDF and three terminal JSON receipts to the exact result folder;
2. fetched fresh metadata for each file;
3. performed an actual `alt=media` byte readback for each file;
4. matched size, provider checksum, and the expected report SHA-256;
5. validated the source/job/revision links inside all receipts;
6. constructed a hash-bound artifact manifest and terminal receipt.

Unreadable, missing, duplicate, moved, malformed, or checksum-mismatched output
raises `UV_DRIVE_READBACK_FAILED`; the leased queue item is retried or fails and
cannot be finished as `REVIEW_READY`.

## Recovery evidence

The exact-head tests cover timeout, retry, stale lease reclaim, fencing-token
rejection, duplicate enqueue, interrupted-worker exhaustion, failed Drive
readback, unreadable JSON, duplicate artifact locators, and the one-item
`released_jobs=0` invariant.

## Safety state

- `canonical_promotion_allowed=false`
- `database_persistence_allowed=false`
- `publication_state=NOT_PUBLISHED`
- no real media canary in readiness workflow
- no batch release
- no Diana 14+, 250, or 254 scheduling
- no ASR/OCR/training/DDS3/BEN execution during readiness
- no merge or production routing
