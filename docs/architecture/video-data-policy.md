# Heavy video data and cost policy

## Principle

Large media must be processed by the compute layer without turning Vercel or Neon into a video host.

Canonical flow:

`Drive/object storage -> compute worker -> compact structured result -> Neon -> web UI`

## Source media

- Keep raw video/audio in Google Drive or dedicated object storage.
- Do not store raw media bytes in Neon.
- Do not route bulk video upload/download through Vercel Functions when a direct storage path is available.
- Preserve source identity, checksum, size, duration and storage reference as provenance.

## Processing

- Download/read source media from the compute layer.
- Keep temporary frames, chunks, extracted audio and intermediate model files on compute-local scratch storage when practical.
- Temporary derivatives must have an explicit cleanup lifecycle; they are not durable artifacts by default.
- Checkpoint long jobs with compact state so failed processing can resume without preserving every temporary binary.

## Neon

Neon stores compact structured data: job state, source references, checksums, timestamps/timecodes, transcript metadata/text where appropriate, extracted deal data, analysis results, validation state and artifact references.

Large binary media, frame archives and video copies are forbidden as normal database payloads.

## Vercel

Vercel provides UI/API orchestration and result presentation. It must not be the execution environment for heavy DDS3/BEN/video batch processing and must not become an unnecessary media proxy.

## Artifacts

Classify outputs as:

- **source**: original media, retained in Drive/object storage;
- **durable derivative**: approved transcript/report/export that merits retention;
- **ephemeral derivative**: frames/chunks/cache/intermediate files, cleaned automatically;
- **structured evidence**: compact JSON/database result with provenance and checksum.

Prefer one durable source plus compact reproducible evidence over multiple large duplicate media copies.

## Completed-result cleanup proof

A `COMPLETED` Universal Video result directory is not an ephemeral derivative by default. It may be selected for cleanup only after durable publication proof exists.

Accepted proof forms:

- a completed-job receipt in `spool/done` or an explicitly configured external receipt proof root;
- a local `DURABLE_PUBLICATION_PROOF.json` sidecar in the result directory.

The local sidecar is valid only when it matches the result manifest `job_id` and `job_hash`, records a non-empty Drive folder id, includes the artifact-set and publication-marker SHA-256 values, and records the remote verification mode `SIZE_MD5_SHA256_PROPERTY_MATCH`.

The publisher writes this sidecar only after the Drive completion marker, full remote inventory verification, and final folder readback have succeeded. Do not modify `manifest.json` after publication merely to record cleanup state, because that would invalidate the published manifest checksum.

## Cost guardrails

1. Heavy compute cost belongs to the compute provider, not Vercel.
2. Avoid duplicate egress loops between storage, Vercel and compute.
3. Avoid database storage of binaries.
4. Do not create Vercel deployments for compute-only changes.
5. Keep intermediate video derivatives bounded by retention/cleanup rules.
6. Track storage references and checksums so duplicate inputs can be detected before reprocessing.
