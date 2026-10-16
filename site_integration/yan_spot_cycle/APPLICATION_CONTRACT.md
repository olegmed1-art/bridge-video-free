# Exact application adapter boundary

This is an adapter candidate for Yan's PHP application, not a production
activation. The only available original application sources are the hash-pinned
`source-public/index.php.txt` and `source-public/htaccess.txt`. The full CMS,
database configuration, renderer and learner-session implementation are absent.

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
It stays in trusted PHP and is never sent to the browser. No learner identity is
inferred from `session_start()`, `$cms->admin()` or a browser identity header.

`dispatch` receives `{method,path,headers,body}` and returns
`{status:int,headers:array,body:string}`. It must use the existing application's
verified learner session, expiry/revocation, assignment ownership and backend
connection. Backend paths are confined to `/school/lessons/diana19/`; Cookie,
Origin and X-CSRF-Token pass through the adapter. The backend remains responsible
for admission, write CSRF/origin, persistence and post-save reveal. A shared
service key is not a learner. The current Python local staging resolver is
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
answer writes do not acquire a new anonymous or admin path.

## One unresolved blocker

The missing **application bootstrap/session/view contract from
`cms/bootstrap.inc.php`** prevents binding these callbacks to the actual school.
Provide its code and the referenced learner verifier and view renderer contract,
with configuration values and credentials omitted. From the available index.php
we can establish only CMS admin login and `includeView(scriptName())`; neither
establishes a learner or tells us the document/content-slot interface.

## Validation and rollback

The source patch is checked byte-for-byte against the original source hashes.
The targeted Linux check uses a clearly marked CMS **contract fixture**, a real
PHP entrypoint, an active native PHP session and the already authorized HTTP
backend on neutral data. It checks the host-shell marker, fragment, one saved
answer, explanation/timecode, and restart readback. It does not establish the
missing real CMS contract or production identity.

Rollback removes the inserted branch and the six new overlay files. No original
CMS configuration, session settings, school data or live site is changed.
