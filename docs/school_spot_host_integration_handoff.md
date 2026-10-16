# School spot lesson: exact host handoff

## Local evidence and current boundary

The production lesson entrypoint in this checkout is `app.py`, which
registers `bridge_school_api.school_spot_lesson.router` at
`/school/lessons/spot-cycle/`. The request stops at
`school_spot_lesson.resolve_verified_lesson_binding()` with HTTP 503 unless
the host explicitly installs `app.state.school_spot_binding_resolver`. No
learner-facing frontend, browser login module, or authenticated learner HTTP
principal is tracked here (`git ls-files` has no root `package.json`, Next
configuration, `src/` or `frontend/`).

`bridge_school_api.main.require_api_token()` checks a shared service bearer
token, not a learner. The `VERCEL_OIDC_TOKEN` middleware in `app.py` forwards a
deployment-scoped token for a remote service, not a browser identity. SQL
`auth_identity` and `bridge_establish_verified_actor_context()` explicitly
require an already verified external session. The other local backend checkout
`2026-10-12/task-2/bridge-video-free` and the `course-release-staging` copy
also expose the FastAPI service-token entrypoint; no school browser verifier
was identified there. No env values, credential files, or live service were
read to reach this conclusion. The actual identity provider is **unknown**.

## Minimal integration diff after the existing site is inspected

The owner clarified that there is a website, but no learner cabinet or login.
Source files previously supplied by Yan are being located separately. Until
their exact architecture is verified, the existing-site integration point and
future human-session provider are undetermined. The lesson lookup uses the
existing `TrustedJournalSource` contract and server-owned records; it is a
local integration task. No token, secret, OAuth grant, new frontend, or new
provider is needed for this handoff.

1. Once a learner login exists, verify its browser session once, including expiry
   and revocation. Return only a typed
   `VerifiedProviderIdentity(provider_key, provider_subject, csrf_token,
   canonical_origin)`. Do not construct it from browser-supplied identity
   headers or request parameters.
2. Implement `TrustedJournalSource.find_existing(identity)` against the host's
   private, server-owned lesson records. It must return an existing
   `TrustedJournalRecord(school_id, student_id, session_id, journal_path)`;
   the browser chooses none of these values. The journal must remain in
   private storage and match its `TeacherSessionStore` session header.
3. Install **one** request-scoped resolver on
   `app.state.school_spot_binding_resolver`. Its minimal body is:

   ```python
   identity = existing_school_host.verify_browser_session(request)
   with existing_school_host.read_only_connection() as connection:
       verified, binding = SchoolSpotReadRepository(
           connection, existing_school_host.trusted_journals
       ).resolve(identity)
   ```

   Return `(verified, binding)`. The route already consumes this typed pair;
   no route dependency replacement or production fallback is necessary.

   Feed that pair into the existing origin, CSRF, learner/school and journal
   checks in `require_learner_lesson_binding()`. Do not verify the session in
   one function and look up the journal against a separate request snapshot.
   Until all three host dependencies are present, retain HTTP 503 for page,
   assets and transport.
4. Add the lesson link only in the actual school frontend after its own host
   session and ownership boundary are reviewed. No route activation or
   production database operation is authorized by this handoff.

`SchoolSpotReadRepository` already queries active
`auth_identity → person → student → school`, an active school-scoped `member`
role, and `session_participation` for the same school/session; it rejects a
mismatched trusted journal. Synthetic tests live in
`tests/test_school_spot_repository.py` and
`tests/test_school_spot_lesson.py`. The rollback-only PostgreSQL test
`database/tests/401_school_spot_repository_ownership.sql` remains unrun
pending a separately authorized disposable database. None of those synthetic
tests establishes production login or journal ownership.

Update (2026-10-16): the Yan PHP overlay and explicit persistent single-user
launcher are in `site_integration/yan_spot_cycle/README_RU.md`. The dedicated
launcher installs `LocalSingleUserResolver` and `FileTrustedJournalSource`,
creates two new assignments through the standard store API, and never claims
to authenticate a website visitor. It is absent from normal `app.py`.
The real HTTP/restart check writes new automated verification responses and
retains replay-validated journals separately; it does not import learner history.
