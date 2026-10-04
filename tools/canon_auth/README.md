# Bounded canon acceptance on the existing production origin

This module verifies the already deployed teacher at main
`08cd157b32a3e34974c4d24706882ef31681891d`, READY deployment
`dpl_Dt7d8TupCi3owHDMK7MFfwLj9Lr7`, through the owner-authorized official origin
https://bridge-video-free.vercel.app. It never uses the protected immutable URL,
a protection bypass, a new credential, or credential export.

## Explicit validation-only build

The FastAPI `tool.vercel.scripts.build` hook invokes `build_once`. Ordinary commit
messages return immediately without credential lookup. Only the exact designated
merge subject `CANON_ACCEPTANCE_20261004_AUTHORIZED_RETRY1` and body containing `observed_at`
and the pinned `base` can start validation, on production/main in the pinned
project. An incomplete designated message fails closed. Observation age must be
at most 300 seconds and execution must start before 2026-10-04 18:00 UTC.

The marked build ALWAYS exits nonzero, including after successful HTTP checks.
It is validation-only and never promotes a deployment: the existing READY stays
live. Its expected Vercel ERROR is not an acceptance PASS or a runtime failure.
A later ordinary commit remains deployable. Only three build modules are included
by `.vercelignore`; the SQL compiler, tests, source package and diagnostics are
excluded from the deployment bundle. Application routes and authorization are
unchanged.

The coordinator must hold concurrent release changes, freshly reconcile the
alias/READY/SHA immediately before merge, and permit only one marked build. Do
not redeploy/retry the marked commit. The exclusive temp claim is NOT a durable
cross-build ledger. The fixed initial position-404 probe rejects later attempts
after baseline creation, but cannot alone exclude parallel builds before that.

## HTTP phases and controller gates

The earlier ONCE build completed with a timeout and zero database writes after
automatic approval review rejected the baseline transaction. The new marker is
one separately authorized fresh invocation following explicit owner permission
evidence. It is not a redeploy/retry of the expired merge commit. Reconcile the
existing READY again and provide a new observation timestamp. The SQL plan and
40/42-row scope are unchanged; retry authorization does not broaden that scope.

The resident existing API token is used only in memory and only on this origin.
Redirects and proxies are disabled. Limits: 40 requests, 8-second socket timeout,
420-second process deadline, ten baseline polls at 16-second intervals and eight
polls at 8-second intervals for each later phase (at most 39 total requests).
Every waiting-poll receipt is emitted immediately, including on a later timeout.
No SQL or DB credential is
used in the build. Overview payloads and raw response bodies are never logged.
Output contains only fixed statuses, public canon hashes and request receipts.

1. Credential-free health 200, authenticated overview 200, synthetic absent
   position typed 404. `verify_binding.verify` checks the three receipts.
2. The external authorized controller creates only source + synthetic position
   after that proof. Baseline must be ABSTAIN before import.
3. After correlated baseline, the controller imports/reviews the two approved
   meanings and activates their existing SQL gates. The API must report 3H
   SUPPORTED and 3S CONTRADICTED for the fixed 3-1-4-5 hand.
4. After correlated active evidence, revoke only owned bindings. API: ABSTAIN.
5. After correlated revoked evidence, create new owned activation rows using the
   original expiry. API: SUPPORTED / CONTRADICTED again.

Every request ID is checked for uniqueness before publishing its phase.
`verify_phase` checks phase contracts and a cross-phase receipt ledger. Preserve
original authenticated metadata/log tool responses and request-ID/deployment
query filters; normalized JSON is not self-authenticating. Correlate every phase
before the next write, then every request including waiting polls via
`verify_receipts` (at most 420-second bracketing window). No alias-only inference.
HTTP output stays pending until this independent control-plane correlation.

`pilot_sql.plan(school_id, reviewed_code_sha)` is a pure compiler, not a runner.
The actual school ID and live plan stay outside the public repository. Its SQL
must first pass `rehearse_sql` against the fixed disposable PostgreSQL 18 service.
Do not run the disposable rehearsal on production. Each returned stage is one
transaction, bounded by a 15-second statement timeout and advisory lock. SQL
constraints and activation gates remain enforced. Original source excerpts and
TDEC-20261003-002 are preserved. No HCP threshold is inferred from points.

Normal budget: 40 new rows, including five append-only history events and both
activation generations. Only one synthetic position, no teacher output, search,
queue or final-decision writes. Expiry is 24 hours from the initial activation
transaction; reactivation cannot extend it. Emergency revoke updates only owned
activation IDs and retains history with at most two extra rows (42 total).
Repeated emergency calls do not create additional audit rows. If a required phase
or correlation fails, stop progression and revoke any active owned bindings.

## Separate legacy GitHub diagnostic

`db_diagnostic` consumes the existing GitHub DB secret only in its separately
scoped first-run diagnostic workflow. It checks fixed target metadata and performs
one read-only connection with rollback; it does not rotate or expose credentials.
A failed legacy credential is a separate blocker and is never reported as green
because Vercel acceptance passes. No new diagnostic workflow is installed here.

Validation: `python -m pytest -q tools/canon_auth` plus the isolated PostgreSQL
workflow. These tests and build receipts are not production acceptance until the
actual controller stages and pinned-deployment correlations have completed.
