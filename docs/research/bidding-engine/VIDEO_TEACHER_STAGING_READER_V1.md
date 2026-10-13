# Personal teacher staging reader contract (not deployed)

This documents the personal-reader boundary originally reviewed at
`d7314c60644ccbbf2f269367810ccc3325b76d22`. It is separate from the subsequently
added fixed-school **internal service** route and exact-lineage staging writer
described in [the internal staging contract](VIDEO_STAGING_PERSISTENCE_REHEARSAL_V1.md).
The internal AI service uses the existing service token and server-selected school;
it does not require a personal instructor login or per-video disclosure receipt.
Neither lane grants canon acceptance or student delivery.

## Why the personal route remains absent

`/v1/knowledge/teacher/video-candidate` is intentionally NOT registered. The
existing application token is insufficient to establish teacher/school access:

* `bridge_school_api/main.py:require_api_token` compares a shared bearer token and
  returns `None`; it does not resolve an authenticated person or school.
* `bridge_school_api/db.py:connect` opens the generic application connection. It
  does not establish a signed actor context or independently authenticate an
  external identity. The token establishes internal service access, not a person.
* Migrations `0041`, `0043`, and `0104` define signed transaction-bound actor
  context and a restricted auth gateway. They are existing mechanisms, but this
  API does not integrate them. The reader must not manufacture their results,
  use caller school IDs, or infer teacher access from member access.
* Migration `0049` permits null candidate source/run IDs. The current writer now
  requires the exact preceding persistence result, independently checks same-school
  SQL lineage, and stores a full replay snapshot outside its own member list.
  Existing historical rows without that receipt are not automatically repaired.
* The valid video input relation is ASSET-form `analysis_run_input`, joined through
  `source_asset(relation_type='embodies')` with exact `metadata.source_id`.
  The internal reader validates that relation in one read-only snapshot.
* A technical source/run receipt does not establish a personal teacher's current
  entitlement or disclosure policy. `0329` promotion receipts are also not personal
  read grants. The personal adapter must still obtain those facts independently.

Consequently, exposing this personal endpoint without its adapter would rely on
invented authority. Its fail-closed interface is implemented in
`bridge_contracts/video_teacher_read.py` as a pure, fail-closed reader contract.

## Contract and trust boundary

`read_teacher_video_candidate(candidate_id, *, reader=None)` accepts only an
exact UUID as request input. With no server adapter it returns `BLOCKED` /
`TRUSTED_READER_ADAPTER_UNAVAILABLE`, with no excerpt or source metadata.

`TrustedReader` / `ScopedSnapshot` are interfaces, not installed implementations,
authentication capabilities, or JSON deserializers. A future server adapter must:

1. Authenticate using the existing trusted gateway and check the current
   identity, school assignment, and teacher read entitlement. Signed context
   validity alone does not prove a current role or an unrevoked identity.
2. Open read-only storage access; derive school exclusively from that context.
   `lookup(candidate_id=..., school_id=...)` must filter every candidate, source,
   run and receipt join by school **before** materialization. Never load an
   arbitrary candidate and then inspect its school, and never use latest-row
   fallbacks. Foreign-school and absent candidates must return the same absence.
3. Return the candidate row, exact source identity/media hash, exact run with its
   `analysis_run_input` source relation, full replay receipt, and an independently
   resolved current read/disclosure binding. Missing links remain missing.
4. Implement `revalidate(candidate_id=..., school_id=...)` as a fresh, atomic
   authorization and scoped-data check before disclosure. It must re-read the
   current candidate, source, run, binding and disclosure decision; omit data
   unless access is still valid. The pure reader revalidates that complete result
   and requires the projection to match its initial read. It measures the final
   clock after this I/O, so an expired lease cannot survive a slow revalidation.
   The contract requires aware `checked_at`/`valid_until`
   timestamps, status `active`, explicit `revoked=False`, and at most 60 seconds
   snapshot age. This ceiling is a local contract limit, not an established
   production policy or proof of real-time revocation. The adapter must not
   stamp cached policy as fresh. A repeatable-read DB snapshot alone cannot
   observe a revocation committed after its start. Integration must define and
   test the transaction/lease policy before the route can be enabled.

The normalized `binding` required by this Python interface is **not a new
database table or an existing production receipt**. It requires exact
`analysis_candidate_id`, `school_id`, `source_id`, `analysis_run_id`,
`payload_hash`, `replay_sha256`, `source_video_file_id`, `source_media_sha256`,
`parent_raw_sha256`, `parent_canonical_sha256`, freshness fields, and a disclosure
decision `TEACHER_EXCERPT_ALLOWED` bound to `display_sha256`. None may be copied
from request claims or granted by generated payload labels. Its absent
production origin is an explicit blocker, not a stub to fill with `True`.

The implementation recomputes candidate payload and full receipt canonical
SHA256, checks exact unique membership and metadata, and compares them to the
independent binding. The source-media hash remains a **claim** unless verified
through the separate media evidence process; equal hashes are not source rights,
identity, teacher authorization, or proof of a real video.

Only two existing payload forms are supported:

| Candidate type | Display stage | Projection |
| --- | --- | --- |
| `video_artifact_source_observation` | `SOURCE_OBSERVATION` | exact quote and original start/end |
| `video_source_knowledge_draft` | `CANDIDATE_RESEARCH` | exact quote and original start/end |

No generic parser, learning-model completion, RAG, or canon promotion is added.
The quote is exposed only after the independent disclosure decision matches the
exact projected text and times. Hash checking alone cannot establish that a
quote is safe to show. Images, full hands, native Drive IDs, speaker claims,
private metadata, raw payloads, raw receipts, and generated gap descriptions are
not returned. An authorized response still states UNKNOWN identity/role,
UNVERIFIED media, and empty accepted canon. Partial evidence remains staging.

## Local verification and limits

Run `python -m pytest -q tests/test_video_teacher_read.py`. The scoped mock
storage tests cover both stages, anonymous/nonteacher access before any lookup,
cross-school absence without materializing a foreign row, exact IDs and hashes,
receipt omission/duplication/tampering, stale/future/expired/revoked access,
revocation before disclosure and between calls, incomplete data, safe projection,
and repeatability without input mutation.

Set `BRIDGE_VIDEO_REAL_FIXTURE_DIR` to the separately supplied private fixture
directory to run the real artifact negative check. It rehashes the pinned raw
master, runs the stage-1 local replay, and confirms that a real observation cannot
create missing DB links, read authorization, or receipt binding. Without the
fixture that check explicitly skips. All positive access results use test
doubles; none is a real teacher HTTP/DB/media end-to-end result.

Next safe step: read-only audit of the actual existing authenticated teacher
entry point and exact candidate/source/run/receipt records by their owner, then
review a concrete adapter design using those proven contracts. Until those
bindings and current disclosure policy are available, keep the route absent.
No production deployment or publication is authorized by these tests.
