# School lesson integration boundary

The regular school application entry point in this repository is `app.py`.
It now registers `bridge_school_api.school_spot_lesson.router` at
`/school/lessons/spot-cycle/`; its browser transport is
`POST /school/lessons/spot-cycle/transport`. The page reuses the accepted
teacher-client and store-transport assets and the pinned
`TeacherSessionStore`. No new progress database or journal is introduced.

This checkout does **not** contain a learner-facing school frontend or a
learner-authenticated HTTP principal. `require_api_token` in
`bridge_school_api.main` checks one shared service token, not a student or
school role. The SQL auth/actor-context migrations do not implement the
missing HTTP identity here. Consequently `resolve_verified_lesson_binding`
fails closed with 503 without an explicitly installed host resolver,
including page assets and transport.

`bridge_school_api.school_spot_repository.SchoolSpotReadRepository` is a
read-only adapter prepared for the future verifier. It is not connected to the
route. Its caller must supply a `VerifiedProviderIdentity(provider_key,
provider_subject, csrf_token, origin)` **after** independently verifying the
browser's existing school session, including expiry and revocation. Constructing
the dataclass from unverified browser input is forbidden. The caller also
supplies a read-only database connection and a `TrustedJournalSource` backed by
server-owned records. That source must return an existing
`TrustedJournalRecord(school_id, student_id, session_id, journal_path)`; none of
these values may come from request parameters, headers, or browser storage.

The adapter checks an active auth identity, person, student, and school, plus
an active, currently valid school-scoped `member` role as required by
`0102_identity_controlled_onboarding.sql`. It also checks the student's
participation in a session in that same school. It checks the
trusted journal's existing file and session header. A missing or conflicting
record raises `SchoolLessonOwnershipRejected`. On success it returns the
`VerifiedSchoolSession` and `LearnerLessonBinding` expected by the current
admission code. The real host should call the verifier once per request, then
resolve this pair and pass the same pair into the route dependency. It must
not call the verifier and lookup as unrelated operations that could refer to
different browser sessions. No new provider, schema, or production database
operation is part of this adapter.

`database/tests/401_school_spot_repository_ownership.sql` is a rollback-only
PostgreSQL test of the role interval and participation predicates. Run it only
later on an authorized disposable database with migrations through 0102; the
synthetic Python connection does not execute SQL and cannot establish those
predicate semantics by itself.

Local preview remains separately gated on loopback and is not this school
route.

Before enabling this route for website visitors, install one callable
`app.state.school_spot_binding_resolver(request)` returning the verified pair
from the existing browser-session verifier and server-owned lesson lookup.
The verifier/lookup must:

1. Authenticates the current browser session using the school's existing
   login mechanism; verifies school membership and that this learner owns
   the requested *pre-existing* teacher session.
2. Supply the verified session's CSRF token and canonical origin. The local
   admission contract checks both for writes, injects the token into the
   authenticated lesson page, and rejects browser-supplied learner/school
   identity headers. Browser-supplied IDs and paths are never authority.
3. Return `LearnerLessonBinding(school_id, learner_id, session_id,
   journal_path)` from server-side records. The journal must already exist in
   trusted private storage. Its header is replay-bound to `session_id` by
   `TeacherSessionStore`; the pinned source SHA remains required.
4. Adds the lesson link in the actual school frontend, which is not present
   in this repository. Do this only after the identity provider and enrollment
   check are reviewed and enabled together.

The transport exposes only `resume`, `execute`, and `acknowledge`. A separate
read-only `./saved-answer` route projects the last acknowledged learner answer
after the same ownership check. Neither route creates or resets sessions,
exposes teacher snapshots, activates assessment, or writes to a production
school database. HTTP responses are not cacheable.

The pinned store, routing code, and hash-bound fixtures are tracked in this
checkout. Focused tests need `pip install -e ".[test]"` on Python 3.12 and
run with `python -m pytest -q tests/test_school_spot_lesson.py
tests/test_local_spot_session.py tests/test_local_spot_preview.py`. They have
no external absolute-path fixture dependency and never silently skip.

Change record (2026-10-14): the school route remains closed while the
existing browser-session verifier and ownership lookup are absent. The
admission contract rejects mismatched school/learner identity, spoofed
browser headers, mismatched journal headers, and missing or incorrect
CSRF/origin values. Its evidence is the pinned source/fixture hashes and
the focused FastAPI tests. This local change is review-pending; rollback is
to revert the integration commit. No production data or service is changed.

Change record (2026-10-16): the two placeholders are replaced with the explicit
typed resolver seam. Ordinary production still fails closed without a resolver.
The separate `LOCAL_SINGLE_USER` launcher uses a private file manifest and
loopback context, not website authentication. It preserves owner/CSRF/replay
checks. See `site_integration/yan_spot_cycle/README_RU.md` for fresh assignment
creation and the real HTTP/restart verification. The pinned store is unchanged.
