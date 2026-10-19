# Native CMS application candidate — 2026-10-19

Status: prepared adapter; no live installation or student identity binding.
Governance: STANDARD, reversible staging code. No canon change or new service.

## Source and scope

The authorized static source report confirms the front-controller lifecycle:
`cms/bootstrap.inc.php`, `session_start()`, then `$cms->page()` and
`$cms->includeView($rout->scriptName())`. The CMS, router and database are
server-owned globals. The normal views use `$pageData`, `$db`, `$cms`, `$rout`,
`$lang`, `$headerData` and `SYS_BLOCK_DIR`.

Evidence: the sanitized report `libfile_0efcde949f848191a9c1be413b2de24b`,
179 lines, supplied from a static analysis of the newly authorized archive.
This supersedes the earlier missing-bootstrap hypothesis. This workspace has
not materialized or executed the full archive. Its private source, SQL contents,
configuration and teacher material are not part of public CI.

The existing hook remains immediately after bootstrap and native session start,
before the legacy admin controller and page rendering. Removing that hook
restores the pinned original index bytes. Other routes remain outside this hook.
The CMS source files and configuration are not modified by the builder.

## Prepared renderer

`include/diana19.cms-renderer.inc.php` exports the explicit factory
`bridge_diana_native_cms_renderer()`. It installs no dispatch, mapping or login.
After backend admission, it calls the confirmed `page()` and
`includeView('practice')` interfaces. The fixed server-owned key also works with
the existing query entry, whose REQUEST_URI may begin with `index.php`.

The resolved path must equal the new fixed
`include/page/diana19.cms-view.inc.php`. A default, login or other view is
rejected before inclusion. The view includes only existing `s-header`, `header`,
`footer`, `js`, `s-footer` blocks beneath `SYS_BLOCK_DIR`. It fills `$headerData`
and inserts the already admitted, scoped lesson fragment. It adds the verified
CSRF/endpoint head data and one same-origin stylesheet/controller before the
single closing head tag. Existing CMS blocks retain navigation and legacy
assets; the module adds no Bootstrap or jQuery copy.

The application supplies only a trusted internal view model:
`title`, `stage=STAGING`, `fragment_html`, `head_html`, `styles`, `scripts`.
Learner text is handled by the existing controller using textContent. The
renderer buffers its output, discards failed output, restores pageData and the
error handler, and converts non-suppressed CMS warnings into a closed 503 from
the caller. Missing dispatch/render bindings return 503 even with isadmin.
Backend rejection occurs before reading/rendering the fragment. This candidate
has no local loopback or administrator admission fallback.

## Unapplied route and runtime plan

1. In an authorized isolated copy, verify the **single** non-secret pages mapping
   entry for `practice`. The report deliberately did not read the YAML values.
   If unused, the proposed value is `diana19.cms-view.inc` (includeView appends
   `.php` under SYS_PAGE_DIR). If already occupied, stop and review a distinct
   module key and matching code change; never overwrite another module.
2. Confirm SYS_PAGE_DIR resolves to `include/page/`, and SYS_BLOCK_DIR to the
   existing blocks. Keep the existing language-aware navigation and headers.
3. Explicitly bind an already verified server-side dispatch callback and the
   renderer factory. The callable returns `{status:int, headers:map, body:str}`
   for a normalized `{method,path,headers,body}`. It must authenticate the
   native session and enforce journal ownership in the backend. A transport
   credential or an unverified cookie/header is not an authenticated learner.
4. In that isolated runtime, verify the exact template, response headers,
   ru/he navigation, cookie/CSRF behavior, allowed assets and denied routes.
   The current neutral fixture cannot establish these real-runtime properties.

No mapping, production file, runtime, permission or service is changed here.
Persistent access, new stores or grants require the separate plan below before
their own consent; this code preparation does not authorize them.

## Student authentication and journal contract

The report confirms `$_SESSION['isadmin']` is an administrator login. It is
never a learner ID. Legacy uFlex/users code is present but no active application
wiring was found; do not enable it automatically. The archive schema contains
no demonstrated learner journal/progress service. That is a source-snapshot
finding, not a claim about every live service.

The existing protected school route remains closed until its host supplies:

| Boundary | Required trusted value / operation |
| --- | --- |
| Session verifier | Existing provider validates its own live browser session, expiry/revocation and canonical origin; produces `VerifiedProviderIdentity(provider_key, provider_subject, csrf_token, origin)` inside trusted server code. No client-selected subject, school ID, role or path. |
| Journal lookup | `TrustedJournalSource.find_existing(identity) -> TrustedJournalRecord | None`, containing `school_id`, `student_id`, `session_id`, private existing `journal_path: Path`. Ownership and allowed activity are resolved server-side. |
| School ownership | `SchoolSpotReadRepository.resolve(identity)` requires an existing active same-school learner/lesson relation and pinned journal resume; returns `(VerifiedSchoolSession, LearnerLessonBinding)`. |
| Route installation | Explicit `app.state.school_spot_binding_resolver(request)` returns exactly that typed pair. No implicit installation in app.py. Missing/malformed resolver is 503; rejected ownership is 403. |
| Every write | Matching school/learner ownership, exact canonical Origin, matching `X-CSRF-Token`, existing journal and command allowlist/idempotency; spoofed `X-Learner-Id`/`X-School-Id` rejected. Secrets and session values stay out of logs/artifacts. |

`SchoolSpotReadRepository` targets the separate school schema (`auth_identity`,
`person`, `student`, `school`, `session`, `session_participation`,
`person_role_assignment`). **Do not run that SQL on the CMS SafeMySQL connection**
or pretend the CMS's nine-table schema implements it. If no such existing
school service is available, this protected route stays 503 pending a separately
reviewed persistence/identity design.

These existing typed bindings and TeacherSessionStore belong to the original
spot-cycle route. Diana19 uses DianaJournal/VisibleStageService and a distinct
explicit service resolver; its current school staging path is restricted to
LOCAL_SINGLE_USER. Do not label that path SCHOOL_VERIFIED or feed its SQLite
files to TeacherSessionStore. A future student-backed Diana19 factory must bind
the verified owner to its own existing service/journals and preserve the same
ownership/CSRF/reveal guards; that installation is not implemented here.

The optional private file manifest is a lookup, not authentication. It is fixed
by the host, bounded to 64 KiB and one verified owner, with allowlisted activity
IDs `spot.a7c2`/`spot.f3b8`. Relative journal paths stay under its private root;
symlinks/traversal and missing files are rejected. It does not create sessions
or journals. The browser can select an assigned activity, never an owner/path.

For a PHP-to-backend process boundary, the concrete verifier/broker is still
unsupplied. The native PHP session must be verified by that established service;
do not trust a browser's asserted user ID, shared API bearer, raw session ID or
the CI fixture's projection format as a production identity protocol. Request
only its code-only interface and deployment ownership, never full configs,
keys, tokens, passwords or student records.

## Anonymous practice is a separate binding

The optional explicit `AnonymousPracticeConfig` is disabled by default. Its
resolver returns `AnonymousPracticeBinding(owner_ref,csrf_token,origin,service,
kind='ANONYMOUS_PRACTICE')` only after native ownership verification. An opaque
practice owner represents one browser context, not a school student/account.
It must stay in a separate namespace from SCHOOL_VERIFIED. The adapter neither
creates an account, journal nor server, and app.py does not enable it.

The Linux fixture creates only new neutral automated journals. Its server-side
PHP session projection is test scaffolding. It is not the missing production
broker. No cross-device recovery is promised for anonymous sessions.

Save/readback and idempotent retry precede any explanation reveal. Guided
stage 2 remains locked until the independent first answer is saved. The
original spot cycle ends at AWAIT_TEACHER_REVIEW; SAVED_UNASSESSED and points
do not establish mastery. Native/Diana20 and source teacher files are unchanged.

## Persistent access plan — consent not exercised

Before requesting any new persistent permission, prepare these concrete inputs:

1. Existing session-provider owner and sanitized verifier interface; fixed HTTPS
   origin; expiry/revocation behavior. Reuse existing identity if available.
2. Existing private durable journal location/service and ownership lookup.
   Begin with narrowly scoped read-only lookup and only the admitted journal.
3. A finite grant proposal naming the exact principal, resources, read/write
   operations, retention, revocation and cost. If migration/provisioning is
   required, present schema change, tested recovery and rollback separately.
4. After explicit consent for that proposal, isolated identity/ownership tests,
   then a limited live canary with its own deployment authorization.

No grants, new identities, migrations, production journals or services are
created by this candidate. Do not use the CMS deal editor or admin CRUD as a
learner journal: those handlers write PHP content files and do not demonstrate
student ownership/attempt semantics.

## Validation and rollback

The source-exact builder checks pinned original index/htaccess bytes. Focused
PHP acceptance checks the confirmed page/includeView lifecycle, one document,
native shell/assets, denied admission, admin without bindings, wrong mapping,
warning cleanup and context restoration. The existing Linux HTTP test now
uses this renderer with a neutral CMS interface fixture, native PHP sessions,
two owners, four newly generated answers, staged reveal, lost-response retry
and restart readback. No private source archive is executed in either test.

Rollback: remove the inserted index hook and eight new overlay files; restore
any later operator-approved mapping from its own backup. The builder has not
applied such a mapping or modified production data.
