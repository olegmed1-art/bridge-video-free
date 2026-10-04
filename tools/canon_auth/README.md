# One-shot canon authentication diagnostics

These tools are dormant: no application imports, public routes, deployment hook,
project settings, credentials, permissions or production data changes.

`db_diagnostic` runs only in the uniquely named GitHub diagnostic workflow, on its
first run and first attempt, on the exact test branch before 2026-10-04 18:00 UTC.
The workflow checks out a reviewed literal code SHA. It consumes the existing
`BRIDGE_APP_DATABASE_URL` in that runner and preserves the original smoke
environment (no VERCEL_ENV rewrite). It reports only fixed target classifications,
role/database/TLS booleans and a fixed outcome. One connection, read-only session,
5-second statement timeout, explicit rollback. No secret bytes or fingerprints.
An authentication failure is a useful diagnostic result, not a reason to rotate.

`vercel_validator` is prepared but NOT wired into a build. Its fixed target is the
official production API origin https://bridge-video-free.vercel.app, already used
by the existing client. The owner-authorized assessment confirmed that this origin
is allowed by the configured Protection scope. This does not authorize access to
the protected immutable URL, a protection bypass, or API-token extraction.

The reconciled target is main cf6091f09fa70afc4b25162fbbb2fea0dc36898a and READY
deployment dpl_9tC4HDYrX6WLxgQXpcMm32te8ue3. Both pins were read from the
authenticated control plane after retirement PR #2086. A new fresh observation
is still mandatory at invocation time. No live execution is currently enabled.

Before any future explicit invocation, an authorized operator must verify and
record current control-plane READY metadata binding the pinned project,
deployment, production alias and exact SHA. The script accepts that attestation
for at most 300 seconds; it cannot independently query the control plane. Its
CANON_* intent/attestation values are per-invocation non-secret inputs and must
never be added as project-wide env or a persistent build hook.

In a permitted build context it makes at most three requests with redirects and
proxies disabled, eight-second socket timeouts and a 35-second process deadline:
credential-free health, authenticated overview (payload never logged), and the
fixed canon request for a synthetic nonexistent position. It emits only
`pending_deployment_correlation`, never an acceptance PASS. Responses must include
a valid x-vercel-id. Its suffix after the last `::` is the runtime-log request ID;
this mapping was checked with a real credential-free production health request.

The operator must read alias/deployment metadata again after the request batch,
and query runtime logs for each receipt using both its request-ID filter and the
expected deployment ID. Preserve original authenticated tool responses and query
arguments. `verify_binding.verify` rejects missing/ambiguous matches, changed
SHA/deployment/origin, mismatched path/status/time or an observation window over
300 seconds. It validates normalized evidence but cannot authenticate supplied
JSON. A caller-created JSON assertion is not evidence. The release coordinator
must prevent concurrent alias changes; do not rely solely on bracketing alias
reads. The final result proves only an authenticated typed refusal, NOT baseline
ABSTAIN or active canon meaning. It does not declare legacy GitHub CI green.

The exclusive temporary claim prevents a second attempt in the same build's temp
filesystem, including after failure. It is NOT a cross-build one-shot ledger.
The lack of a build hook and expiring explicit intent prevent automatic checks
on future builds. A different build would require a separately authorized explicit
invocation and fresh attestation. No normal deployment is changed to invoke it.

Both tools stop on unexpected metadata or response contracts, and never dump raw
exceptions, bodies, environment, credentials, lengths, hashes or fingerprints of
credentials. Public canon payload hashes elsewhere are semantic evidence, not
credential fingerprints. The disposable PostgreSQL rehearsal must not be used
against production.

Validation: `python -m pytest -q tools/canon_auth/test_auth.py`.
