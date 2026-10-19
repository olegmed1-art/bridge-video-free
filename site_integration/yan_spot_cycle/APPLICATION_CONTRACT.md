# Exact application adapter boundary

This is an adapter candidate for Yan's PHP application, not a production
activation. The only available original application sources are the hash-pinned
`source-public/index.php.txt` and `source-public/htaccess.txt`. The original ZIP
was identified on Drive, but its bytes could not be transferred to this Windows
workspace (DNS 11001). The CMS is absent from the local handoff; absence from
the ZIP itself is **not established**. `audit_source_archive.py` checks every ZIP
path, nesting and case without reading bootstrap/config/credential contents.

`build_application_candidate` inserts the lesson branch **after** the existing
`require 'cms/bootstrap.inc.php';` and `session_start();`. It preserves the rest
of index.php and the exact .htaccess. The previous standalone local candidate
remains a different build target. This application target never launches or
selects a loopback backend.

The existing bootstrap must install this one request-scoped contract:

```
$GLOBALS['bridgeDianaApplication'] = array(
    'dispatch' => callable($request, $application),
    'render' => callable($view, $application)
);
```

`$application` contains the actual `$cms`, `$db`, `$rout` and native `$_SESSION`.
It stays in trusted PHP and is never sent to the browser. The owner explicitly
states that this is a website with no learner cabinet. Existing learner accounts
or a school role are **not prerequisites** for the current practice. CMS admin
is not the anonymous practice owner.

`dispatch` receives `{method,path,headers,body}` and returns
`{status:int,headers:array,body:string}`. It must use the existing application's
server-owned browser session, anonymous practice ownership and backend
connection. Backend paths are confined to `/school/lessons/diana19/`; Cookie,
Origin and X-CSRF-Token pass through the adapter. The backend remains responsible
for admission, write CSRF/origin, persistence and post-save reveal. A shared
service key is not a browser owner. The current Python local staging resolver is
**not** a production PHP-session verifier; it is not installed as a fallback.

`render` receives:

- `fragment_html`: the lesson section, with no document, header, navigation,
  footer, global theme, expected answers, hidden E/W or source evidence;
- `head_html`: the verified backend CSRF meta and inert endpoint JSON;
- `styles`: one same-origin, `.bridge-diana19`-scoped stylesheet;
- `scripts`: the existing same-origin lesson controller;
- `title` and `stage=STAGING`.

It must return the application's existing document/template with this fragment
inside the usual content slot, include the supplied head/assets, and preserve
the CMS navigation, document security headers and session behavior. Its template
API and file name are not guessed here. Backend admission succeeds before the
renderer is called. Missing dispatch/render bindings return 503; assets and
answer writes do not acquire an unbound or admin fallback.

## Minimal anonymous practice, with no account system

The public index already calls native `session_start()`. Subject to verifying
the actual bootstrap, this supports a first-party anonymous practice context:

1. Keep one opaque random practice owner and CSRF nonce in the server-side PHP
   session. This is browser ownership, not a student ID, school role or login.
2. Bind that owner to its private answer journal only on the server. Browser
   requests may choose one of the three allowed question IDs, never an owner ID,
   journal path or a revealed answer. Keep the existing CSRF/origin checks.
3. Use the existing `dispatch` hook for the lesson handler and `render` hook for
   the site's normal content slot. Save before revealing the accepted explanation
   and source/timecode. Keep STAGING/SAVED_UNASSESSED and no inferred mastery.
4. No account, cabinet, OAuth provider or new external service is required. Loss
   of the anonymous session means no promise of cross-device progress recovery.

The separate `build_anonymous_candidate` now prepares this adapter with an
explicit disabled `anonymous_config.example.py`. The backend factory is
`bridge_school_api.anonymous_practice_adapter.create_anonymous_practice_app`.
It reuses the existing lesson router, service, journal and UI. No account, key,
journal or server is created by installing the adapter. Empty/disabled config
or a missing session resolver returns 503. An explicitly supplied trusted
anonymous binding must match the journal owner and configured origin; write
CSRF/origin and spoofed owner-header checks remain enforced. Neither app.py nor
the old local mode installs this adapter implicitly.

This is ready integration code, not a deployed authentication mechanism or a
claim that the actual PHP session contract is established. Before a public
release, verify the actual CMS include/render paths and handler runtime, choose
the existing private durable storage, and confirm canonical HTTPS origin,
first-party cookie/CSRF behavior and journal ownership/lifetime. Deployment still
requires its own authorization; none is performed here.

## One unresolved blocker

The unresolved **archive path verification** currently precedes a missing-source
claim: the authenticated Drive download exists, but local transfer fails with
DNS 11001. Previous local task records were searched for an existing ZIP path
and full path listing; only the Drive locator/prior hash receipt was found.
The attempted Windows destination does not exist. No Oracle absolute path is
recorded, so one must not be invented. Do not ask the owner to resend the archive
at this stage. After the authorized Oracle/DC restart window is closed, inspect
the already existing copy through its established path if it is there, then run
the read-only path checker. No Oracle/DC call is made during the window.
If that check proves bootstrap absent everywhere,
the minimal source request is `cms/bootstrap.inc.php` as required by the exact
line `require 'cms/bootstrap.inc.php';`, plus the code-only renderer/bootstrap
interface it invokes, with configuration/credential values removed. No existing
learner verifier is demanded. If the file exists under another directory/case,
first report that exact path and resolve the original include relative to the
identified front controller; do not invent a new implementation.

## Validation and rollback

The source patch is checked byte-for-byte against the original source hashes.
The targeted Linux check uses a clearly marked CMS **contract fixture**, a real
PHP entrypoint, an active native PHP session and the already authorized HTTP
backend on neutral data. It checks the host-shell marker, fragment, one saved
answer, explanation/timecode, and restart readback. It does not establish the
missing real CMS contract or production identity.

Rollback removes the inserted branch and the six new overlay files. No original
CMS configuration, session settings, school data or live site is changed.
