# Dormant main-only owner inventory proposal

Scope: source review only, based on main 7ae456e543ae584112a31dda3e63a8a8cab64d63.
PR2104 targets main and remains draft. No installation, merge or dispatch is
authorized by this package. The active protected workflow and publication gate
remain unchanged. The earlier feature-branch proposal and its publication-gate
exception are superseded and removed.

External boundary confirmed by the owner on 2026-10-04 at 20:09:33 UTC:
database-production permits exactly main (one branch, zero tags). Feature branches
are ineligible. No settings, reviewers, waits, bypasses or secrets are changed here.

The inactive workflow template adds a separate canon-readonly choice while
preserving the maintenance job's repository, main, actor, manual event and exact
expected_main_sha checks. The new job also requires the triggering actor to be the
owner, reviewed source and probe inputs to equal github.sha, and the existing
database-production environment. Default remains maintenance. Existing credential
names and credential parser are reused without role or principal substitution.

There is no static pin to an obsolete main, nor a self-referential installation
hash. After separately reviewed installation on main and release coordination,
the operator must supply the exact reviewed installation SHA in both inputs.
Code requires that SHA to equal GITHUB_SHA, checked-out HEAD and the live main
ref before and after inventory. Moving main causes refusal before credential
access or suppresses the PASS result. Every feature ref is rejected before
credential access. This does not authorize installation or live execution now.

owner_probe uses only fixed read-only SQL and existing strict TLS/target binding.
It exports booleans and exact public code SHAs, never credentials, host IDs,
snapshots or application rows. Catalog privilege checks do not execute the
school gate and do not constitute write admission, teacher-runtime qualification
or production readiness.

The bounded qualification.sql catalog check was externally completed and rolled
back on 2026-10-04 at 19:32:00 UTC. Schema/relation/column/function privileges
were present. SQL-editor backend ssl=false does not qualify a real protected
runner's verified TLS connection. Credential validity, actual runner binding and
teacher-role/gate behavior remain unproven.

Review CI runs only synthetic transports and mocked existing Linux parser
contracts: contents:read, no environment, secrets, database or owner entrypoint;
one ten-minute job. The new read-only tests have no rehearsal/write imports.
Content hashes normalize UTF-8 newlines. No private registry is included.
Rollback: remove the exact source additions; no database rollback is needed.
