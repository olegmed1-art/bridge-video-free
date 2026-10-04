# Existing teacher API integration (offline target only)

## Established target and exact missing deployed binding

The source target is `app:app`, the root Vercel ASGI entrypoint. `app.py` imports
`bridge_school_api.ai_teacher.router` and includes it with `require_api_token`.
The existing route is `POST /v1/ai/positions/{position_id}/teacher-evidence`.
Its normal branch stores a supplied teacher answer in `ai.teacher_output`;
`bridge_school_api.ai.ai_position` reads these records in the existing GET
position response. This is an existing evidence intake/readback path, not a
previously existing canonical teacher generator. The integration adds generation
only to its explicit offline branch; normal evidence recording remains intact.

`bridge_school_api.ai_decision.finalize_position` deliberately refuses to finalize
from teacher evidence alone. `ai_orchestrator.process_position` routes to a cache
or queue and does not invoke the canon consumer. Neither is modified.

No deployed teacher caller, deployed application SHA, authenticated teacher
request trace, or production position/profile/version binding has been verified.
These are the missing conditions for claiming a deployed consumer connection.
The repository's Docker API target starts `bridge_school_api.main:app`, which
does not itself mount the teacher router; it cannot be silently treated as the
same deployment as root `app:app`. No deployment, DB or service was contacted.

## Request and response through the existing HTTP contract

Run locally without opening a listening socket:

```sh
python -m experiments.tournament_teacher.api_demo
python -m pytest -q experiments/tournament_teacher/test_api_integration.py
python -m experiments.tournament_teacher.api_manifest
```

The request retains `teacher_key`, `teacher_version`, `teacher_system` and adds
`test_request` (the previously proved consumer input) and `test_source_version`.
`api_harness.request_body` and `request_path` build a complete reproducible request.
The manifest's example contains the full synthetic hand, auction and position UUID.
Client-supplied action, scores, explanation and raw output are refused in this mode.

The response retains teacher action/explanation fields and reports status,
source bindings and the pinned source version. `ABSTAIN` has a null action and
specific context reasons in `raw_output_json`. Every response says
`persisted=false`, `queued=false`, `finalized=false`, and
`formal_db_activation_asserted=false`. Assessments do not become recommendations.

Default behavior is `404 TOURNAMENT_TEACHER_TEST_DISABLED`. Neither environment
variables nor a request flag enable the target: the local harness must explicitly
install an `OfflineTournamentTeacherTarget` in process on app state. The normal
entrypoint never installs it. The experiment is loaded lazily only after this
gate; a missing or changed snapshot refuses execution. Authentication and cache
security middleware are unchanged. The harness suppresses ambient tokens and
stubs only auth success; a separate test verifies real auth still refuses an
unconfigured request. DB connections and post-startup network connections are
blocked by test sentinels. Existing SQL/profile activation gates remain intact.

The legacy writer continues to accept its original fields. Reserved test keys,
test version prefixes and test-like extra fields are fenced into the offline
branch, so stale or misspelled test envelopes cannot fall through to DB writes.
Ordinary legacy extra fields remain ignored by SQL, as before.

## Manifest, UUIDs and rollback

[api_manifest.json](api_manifest.json) is scoped only to the established local
source target, not a deployment. The source version contains the SHA-256 of the
reviewed 26-rule snapshot. The teacher version identifies the consumer engine.
Rule binding UUIDs are deterministic UUID5 identities for this test snapshot.
Position UUIDs bind the complete canonical JSON input and snapshot version.
All database UUID fields are null; these synthetic UUIDs must never be used as
claimed database identities or activation records. Original rule and decision
IDs, source status and DRAFT labels remain preserved.

Rollback for this local target: leave the optional target unset (default), or
exit the harness, which restores app state and dependencies. To remove the code,
revert the integration commit on this test branch, including the two API files
and local harness/tests. No data restoration, activation reversal, queue cleanup,
key change or service restart is necessary because this path writes no state.
There is no production rollback plan without a verified deployed target.

CI tests the existing HTTP route for all 26 scenario groups plus disabled mode,
authentication, UUID/snapshot mismatch, output injection, DRAFT preservation,
legacy writer behavior with a transaction double, and finalizer refusal. The
HTTP demo output is retained with the synthetic decision evidence artifact.
Passing these checks demonstrates local integration, not production readiness.
