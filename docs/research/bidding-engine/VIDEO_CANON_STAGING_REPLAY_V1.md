# Offline video-to-canon staging replay

Status: IMPLEMENTED / LOCAL VALIDATION / INDEPENDENT REVIEW. Governance: ASSURED.

This adapter closes the silent NOT_REQUESTED case for explicitly requested
analysis or incomplete video_canon inputs. It does not extract missing semantic
content, run media jobs, call the network, evaluate verification bundles, prepare
activation commands or write an authoritative database.

## Inputs and boundary

`replay_result_bundle(result)` accepts an existing JSON result artifact. Legacy
longitudinal quality artifacts are accepted but produce BLOCKED staging records
when the required source-bound observation is absent. A COMPLETED job,
METHODOLOGY_READY status, role label, topic match or high speaker coverage does
not satisfy the learning/evidence contracts.

An upstream producer can supply `learning_observation` with exactly the nine
evidence fields listed in `OBSERVATION_FIELDS` and `teacher_assertions` using
the existing video-canon-evidence-v2 assertion schema. The adapter adds only
the research envelope, deterministic identity and deny-write authority fields.
Alternatively it accepts the existing `video_canon_learning_candidate` and
`video_canon_assertions` keys. Conflicting aliases and normalized assertion IDs
fail closed. It delegates evidence validation to the existing
contracts and requires explanation clauses to be literal quotes in the cited
teacher span. This intentionally restrictive first version cannot synthesize
why/purpose, consequences, normalized rules or bridge test cases.

An input JSON cannot attest its own provenance or teacher identity. Accepted
assertions additionally require an out-of-band `evidence_resolver` callable,
supplied only by embedding Python code, and `replay_provenance` containing
`parent_artifact_sha256` and `parent_hash_domain: RAW_BYTES_SHA256`. No resolver
is installed by default. The explicit local evidence reader described below
rechecks bytes; its CLI and Diana environment configuration cannot establish
independent source origin, teacher identity, role or policy authorization.

The resolver receives the parent raw-artifact digest and returns a mapping with:

- `schema: video-canon-replay-parent-evidence-v1`, `evidence_class: REAL`;
- matching `parent_artifact_sha256` and `parent_hash_domain`;
- `source`, exactly matching the normalized learning source;
- `transcripts`, with one unique matching locator, all transcript contract
  fields, `speaker_role: teacher`, verified identity, and exact `frame_evidence`;
- `source_authorizations`, containing the exact assertion source authorization
  when the assertion claims APPROVED status.

The trusted reader must recompute the immutable parent bytes digest, verify its
source chain and underlying transcript/frame bytes, resolve identity and teacher
role against the independent evidence store, and obtain any authorization from
the trusted policy store. Returning caller-supplied manifest labels is not a
valid implementation. The adapter validates this reader's response; it cannot
make an untrusted reader trustworthy. Resolver failures, synthetic/unknown
evidence classes and malformed/inconsistent manifests produce gaps. Raw parent
bytes hashes, embedded JSON bytes hashes and canonical JSON hashes are different
domains and must never be equated without recomputing both from the same bytes.

## Local evidence reader opt-in (2026-10-11)

`bridge_contracts.video_local_evidence.LocalEvidenceReader` reads only explicitly
named local files using the existing full-master `source`, `transcript` and
`screenshots` layout. It does not discover files, parse arbitrary report formats,
download media, run ASR, load Python modules from configuration or write evidence.
An inventory is pinned by its raw SHA256 and is **not a trust certificate**:

```json
{
  "schema": "video-local-evidence-index-v1",
  "parent": {"path": "master.raw.json", "raw_sha256": "<64 lowercase hex>",
             "canonical_sha256": "<64 lowercase hex>"},
  "source": {"path": null, "video_file_id": "<exact source ID>",
             "sha256": "<64 lowercase hex>"},
  "frames": [{"path": "frame.jpg", "evidence_id": "<existing evidence ID>",
              "sha256": "<64 lowercase hex>"}]
}
```

Paths are relative to the inventory directory, confined after resolution;
absolute paths, traversal, Windows alternate streams and output aliases are
rejected. Null media path explicitly means missing bytes. Every replay rehashes
the parent raw bytes, canonical JSON, configured media and available frame bytes.
Raw and canonical hashes are separate domains. Master source ID/hash and each
frame's unique inventory ID/hash must match. A listed frame without its original
bytes stays missing; a similar image or a re-encoding cannot replace it.

```sh
python -m tools.replay_video_canon private-result.json --output receipt-1.json \
  --evidence-index /private/evidence/index.json \
  --evidence-index-sha256 <raw-index-sha256> --local-staging-db disposable.sqlite
```

The options can be combined with the existing pinned `--selection` and fresh
`--prepared-output` route. Inspection rows contain checks and explicit gaps.
Bounded full-master selections also retain an exact quoted artifact observation,
its source offsets, parent hashes and only byte-verified selected frames.
`SOURCE_OBSERVATION / ARTIFACT_BYTES_VERIFIED_MEDIA_UNVERIFIED` permits reviewing
the source artifact with uncertainty. It is **not VERIFIED_SOURCE_OBSERVATION**:
audio accuracy and audiovisual/source-origin relation still require independent
media evidence. Named speaker and role remain UNKNOWN. A frame outside the
half-open speech interval is explicitly CONTEXT_ONLY_OUTSIDE_SPEECH_INTERVAL.
None of this requires a named identity or manufactures a complete interaction.
Canon/teacher assertions retain their original VERIFIED identity requirement.

Embedding code may provide `trusted_resolver=` using the existing out-of-band
parent-manifest contract, independently resolving origin, audio/frame relation,
roles, identity and policy. The reader additionally binds every returned
transcript locator to an exact unique source `segment_id`, text digest and
interval, and its frame to actual rehashed bytes and the half-open interval.
The file-only CLI/environment never supplies this callback. A generated manifest,
role label, identity map from another lesson, or high confidence is insufficient.
There is no independent trusted store adapter in this stage.

Diana opt-in requires **both** `BRIDGE_VIDEO_REPLAY_EVIDENCE_INDEX_PATH` and
`BRIDGE_VIDEO_REPLAY_EVIDENCE_INDEX_SHA256`. It binds the exact original loaded
master bytes before derived visual fields are added. `replay_evidence_reader=`
routes quality strictly to staging even for complete legacy auto-pipeline inputs;
the replay hash joins the quality fingerprint. No workflow enables the variables.
Removing them restores the prior routing. Real postprocessor runs still have
their preexisting persistence/upload effects and are not authorized by these
local tests. Production writes remain separately gated by fresh backup/restore.

Local validation uses explicit private fixtures when
`BRIDGE_VIDEO_REAL_FIXTURE_DIR` is set; without it the real test is skipped and
must not be reported as e2e. Unit positives are synthetic external-store doubles.
The retained real fixture has no source media/PDF bytes or independent identity
authorization. It must stay BLOCKED while yielding inspectable artifact records.

The narrow prerequisite in `universal_video/speaker_structure.py` rejects
producer/source interval or optional ID misalignment before copying labels.
Ambiguous duplicate intervals are rejected without distinct IDs. This fixes
positional transfer of annotations; it does not verify role semantics or identity.

### Next acceptance boundary (design only)

The proposed `/v1/knowledge/teacher/video-candidate` belongs in the existing
knowledge router after ownership/auth contracts are fixed. Bind exact source,
run, school, UUID, replay hash and receipt; return cited quote/time/frame with
SOURCE_OBSERVATION or CANDIDATE_RESEARCH and explicit allowed use. Anonymous or
cross-school requests must not disclose content. No endpoint, permission,
credential, canon/world/profile writer or migration is implemented here.

Production media acceptance must use the single existing IBM video controller,
return artifacts and confirm Stop within the shared USD20 project budget.
Legacy GitHub output is reference evidence, not an IBM execution receipt.
Before that run, retain the **original bytes of cited frames** in existing
approved durable storage with source/run IDs, original offsets, hash domains,
manifest, retention owner and retrieval check. A master PDF is not a complete
frame archive. New storage or spending requires separate agreement.

A separate `video_canon_replay_receipt` staging payload binds the bundle digest,
parent raw-artifact digest, trusted manifest digest and candidate payload digest,
so a persister retaining only row payloads preserves provenance. This binding
does not modify the existing sealed canon-candidate schema.

The output contains validated `video_canon_inputs` only for accepted assertions.
Its verification-bundle map is always empty. STAGING_READY describes structural
staging readiness, not teacher truth, source authorization, semantic verification,
semantic I2 review, or Canon eligibility. An accepted candidate can remain EVIDENCE_ONLY.
Failures produce a `video_canon_replay_gap` staging record with required paths,
reasons, input digest and deterministic payload hash. Identical assertions dedupe;
conflicting versions of one assertion ID fail closed. Input JSON must be finite.

In Diana v4.2, `video_canon_analysis_requested: true` selects this staging-only
route, including when a caller supplied a full verification bundle. Partial
video_canon inputs also route here. Existing complete legacy auto-pipeline calls
without a replay flag, bounded packet schema or source constraints retain their
prior behavior. Bounded packets and constraint annotations always select the
staging route before the legacy complete-input branch. All replay rows
enter the shared candidate staging collection. Unrequested archive processing
retains NOT_REQUESTED.

## Local operation

From the repository root:

```sh
python -m tools.replay_video_canon private-result.json --output private-receipt-1.json --local-staging-db disposable.sqlite
python -m tools.replay_video_canon private-result.json --output private-receipt-2.json --local-staging-db disposable.sqlite
python -m unittest tests.test_video_canon_replay tests.test_diana_longitudinal_quality_v4_2 -v
```

Store private source files and receipts outside the public checkout. The CLI
rejects output/source/database path collisions and existing receipt paths.
SQLite staging is an explicitly disposable local model with immutable rows and
a unique stable key; it does not test the production PostgreSQL persister or
prove production permissions. No DSN or environment credentials are consumed.
Result hashes exclude clock time and SQLite execution counters. The complete
input object is hashed, so changing any source field, even metadata, creates a
different replay identity; there is no cross-version semantic deduplication.

## Assurance and remaining work

Synthetic tests cover contract revalidation, source/frame/identity/text
tampering, absent evidence, invented explanations, unsafe rule fields,
conflicting IDs, repeat hashes, SQLite deduplication, and Diana integration.
They are not real video receipts. Real replay observations and private Drive
identifiers belong in a separate private handoff, not this repository.

An independent I2 code review is required for the exact release SHA; its private
handoff records findings and re-review separately. Local hash checks and SQLite
constraints alone establish only bounded integrity properties. Test resolver
stubs simulate a trusted reader using explicitly synthetic fixtures; they are
not real evidence, operational resolvers or verification/PASS receipts. A full source
master with independently recomputed raw and canonical digest bindings, exact transcript span, source/frame SHA binding,
identity evidence and source-backed explanation remains necessary for a real
accepted teacher assertion. This change does not activate that producer or
install a trusted source resolver. Independent code assurance does not certify
any lesson statement or confer canonical authority.

Rollback: revert this isolated change. It introduces no migrations, production
writes, policies, permissions, native issuer changes or service activation.
## Bounded source draft normalization

The existing `video-bounded-source-inspection-v1` inspection packet is also an
explicit replay input. It produces a `video-source-knowledge-draft-v1` payload in
a `video_source_knowledge_draft` staging row. This is a filled research candidate,
not the completed-learning contract or an accepted `video_canon_input`.

Normalization retains exact transcript text, timestamps, text digest, source and
parent claims, episode identifiers, role/identity claims, anchor references and
frame inventory. It recomputes the text digest and checks internal source links;
unavailable PDF, media and frame bytes are never treated as rehashed. Unknown
context, actor verification, outcome, authorization and applicability stay UNKNOWN.

The deliberately narrow extractor recognizes short affirmative English or Russian
statements about developing a named suit with an explicit causal clause claiming
one to thirteen tricks. It preserves exact action/reason/causal quotes and
character offsets. This is a CARD_PLAY source claim, not a generalized rule or a
BIDDING rule. Only full utterances matching the small grammar are extracted;
longer or unsupported text, questions, conditions, corrections and contradictory
continuations yield EXTRACTION_AMBIGUOUS with source retained. Fixtures are
synthetic; no private transcript wording is embedded in the implementation.

The generic source packet schema does not name a lesson or participant. Earlier
private inspection packets require an explicit caller-side schema conversion;
unknown schemas follow the ordinary missing-evidence path. The source and draft
artifacts themselves are never changed by this adapter.

Optional `source_constraints` preserve private boundary/overlap annotations.
Recognized fields include `example_end`, `excluded_next_segment_refs`,
`missing_episode_refs`, `overlap`, `identity_lineage`, and
`frame_boundary_conflicts`. Any supplied constraint requires independent review;
empty/malformed constraints also fail closed. Claimed RESOLVED or PASS labels
cannot release blockers. This applies both to source drafts and pre-normalized
contract inputs, even with an injected resolver. Complete annotations are retained
in the content-addressed staging payload for later review. No automatic trimming,
neighbor-example joins, overlap resolution or biometric identification occurs.

Every bounded draft remains BLOCKED on identity, audio, original media/frame
verification, speech/frame linkage, context/outcome, source authorization and
independent verification. Input PASS labels cannot remove these gaps; this draft
path never calls even an injected trusted resolver. An operating evidence reader
and later validated contract construction are separate work. No promotion command
is emitted. Deterministic payload hashes support existing staging deduplication.

## Pinned file-to-local-staging entry point

`bridge_contracts.video_replay_input.build_replay_input` is a pure byte-input
builder. It supports full existing master JSON and existing bounded inspection
packets. It does not download artifacts, extract media, execute DDS, query a
database or install a trusted resolver. Matching a supplied digest proves file
integrity against that manifest, never teacher identity or source authority.

The selection manifest is `video-replay-selection-v1` with `input_kind` equal to
`full_master` or `bounded_packet`, and mandatory `source_raw_sha256`. Full-master
selection uses exact `segment_id`, `episode_id`, optional `frame_ids`, and
`source_document_drive_id`. The source layout is `source.driveId/sha256`,
`transcript[].segment_id/start/end/text/speaker_cluster`, `episodes[].episode_id/
start/end/segment_ids`, and `screenshots[].evidence_id/time/sha256`. Unknown
source fields are not inferred. Optional existing speaker-map bytes require
`speaker_map_raw_sha256`; canonical master digest, job, document and interval
bindings must agree. Missing maps remain unverified.

Bounded inputs require `source_schema` matching the original packet exactly.
The builder explicitly converts that schema to the generic bounded schema,
retains source claims and records the original schema and byte hash in lineage.
It cannot replace an existing map or overwrite conflicting embedded constraints.
Duplicate JSON keys, non-finite numbers, bad pins and ambiguous selectors fail
before output or staging. Source overlaps, omitted episode segments, map conflicts
and boundary frames become retained builder conflicts, not corrected source data.

An optional constraints sidecar requires `constraints_raw_sha256` in the manifest;
its full JSON value is retained unchanged as `source_constraints`. It can only
add blockers. No selected source text, teacher claim or sidecar becomes trusted.

```sh
python -m tools.replay_video_canon /private/master.json \
  --selection /private/selection.json --speaker-map /private/map.json \
  --constraints /private/constraints.json --prepared-output /private/input-1.json \
  --output /private/receipt-1.json --local-staging-db /private/disposable.sqlite
```

Repeat with fresh prepared-output and receipt paths and the same SQLite file.
For bounded-packet input use the same command without `--speaker-map`, with a
bounded selection manifest. Inputs are not modified; path aliases and existing
outputs are rejected. Prepared-input and replay hashes are deterministic; execution
counts and filenames do not enter replay identity. Files and SQLite are separate
resources, not a distributed transaction: after an output I/O failure, retry with
fresh output paths; stable staging keys prevent duplicate rows.

The executable regression uses only synthetic full-master files and sidecars.
Separate private bounded-packet and actual full-master CLI smoke receipts retain
BLOCKED status, empty accepted inputs and no promotion commands. Those receipts
validate data handling, not media correctness, identity or canon acceptance.
The CLI terminates at disposable local SQLite and requires no new credentials,
permissions or database migrations.

## Opt-in postprocessor preparation

The existing v4.2 postprocessor can prepare one source draft before its quality
layer when `BRIDGE_VIDEO_SOURCE_DRAFT_SELECTION_PATH` names a local selection
manifest. Optional `BRIDGE_VIDEO_SOURCE_DRAFT_CONSTRAINTS_PATH` and
`BRIDGE_VIDEO_SOURCE_DRAFT_SPEAKER_MAP_PATH` name already available private
sidecars; each supplied sidecar requires its manifest pin. Sidecar configuration
without a selection is an error. No workflow enables these variables here.

Optional means a file is not required to produce a blocked inspection record.
Without a speaker map, the existing normalizer reports `DIGEST_INVALID` and
`PARENT_CANONICAL_DIGEST_MISMATCH` for the absent map-to-master binding, marks
source integrity `INVALID`, and withholds extracted knowledge (`knowledge=null`).
The selected transcript and candidate record are retained for inspection. A
supplied map also does not prove teacher authority or permit canon promotion.

The loader retains the original embedded master JSON bytes after the existing
PDF/master digest checks. The builder requires `input_kind=full_master`, the
actual loaded document ID, the actual raw master digest, and an exact match to
the parsed master. It runs before visual reconstruction adds derived fields.
It never fabricates source bytes by serializing the working master. Invalid pins,
selectors or local files fail before quality, staging persistence or artifact
upload; incomplete evidence with valid file bindings produces a BLOCKED draft.

Quality receives the prepared packet through a separate `source_draft_input`
argument restricted to the untrusted bounded schema. This route takes precedence
over preexisting canon contract aliases and cannot call the canon promotion
pipeline. Existing statement extraction limits and authority denials remain in
force. The packet digest joins the quality input fingerprint so sidecar or
selection changes produce a different content identity. Repeating identical
inputs preserves candidate keys and artifact generation identity.

Draft rows join the existing `candidate_staging_records` collection. If an
operator later runs the real postprocessor with its existing credentials, its
existing staging persistence and private Drive artifact upload behavior applies.
This change does not start such a run or configure any credential. With no new
local selection, replay preparation remains opt-in; an explicit analysis request
without evidence still receives the existing blocked replay gaps. The current
candidate writer additionally requires the exact preceding `persisted_result`
handoff; without it, persistence is explicitly BLOCKED even in a standalone
postprocessor call. See the [internal staging rollout contract](VIDEO_STAGING_PERSISTENCE_REHEARSAL_V1.md).

Synthetic integration tests exercise PDF byte retention, file pins, raw/parsed
master binding, overlap and boundary blockers, quality routing, and two full
postprocessor invocations with mocked external adapters and disposable SQLite.
All six generated artifacts remain byte-identical; the source draft occupies
one local staging row. This is not a production PostgreSQL or private full-master
postprocessor execution receipt. Operational rollout remains separately gated.
