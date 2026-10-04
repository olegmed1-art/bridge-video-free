# Owner qualification: inactive public review proposal

This branch contains sanitized code, synthetic tests and INACTIVE templates.
The actual protected owner workflow and actual publication preflight are unchanged.
The review CI has no secret injection, protected environment, database, live owner
probe, workflow dispatch, build, deployment, SQL mutation, or role/ACL change.

This is a stacked draft review against the existing canon test branch at
2c7445aecdfe7e657797fbc0ccc63bf89920a8b4. No merge into main is proposed.
The original proposed source guard stays pinned to main
711ddd648fa74f2b903f9d7127dadc412f94b277 and refuses changed main BEFORE reading
the owner credential or connecting. Book cleanup/current-main reconciliation must
finish before any future target update; this publication does not change live pins.

## Proposed supported route, subject to later parent review

The existing native-maintenance-owner-attest workflow already has workflow_dispatch
on default main. GitHub supports selecting another branch for its manual run:
[GitHub manual workflow documentation](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow).
The child has no dispatch tool; an authorized parent UI can supply that interface.
Do not use an unrelated workflow rerun or migration workflow as a substitute.

workflow.patch and native-maintenance-owner-attest.yml.template propose a separate
manual canon-readonly job using the EXISTING protected owner credential expression.
Only credential NAMES appear in these inactive templates; no values, new keys,
connection strings, tenant identifiers, endpoints or private rows are published.
Actual secret consumption would remain inside that existing protected runner.
The new code imports the existing strict owner parser instead of copying private
target parameters into this package or substituting another principal.

Future inputs: probe_scope=canon-readonly, expected_main_sha=the frozen reviewed
main, expected_probe_sha=the full separately reviewed published executable SHA.
That future SHA is NOT the review branch SHA: its new mode is not installed here.
The proposal additionally binds exact branch, workflow ref, checkout SHA, owner
actor and triggering actor, main ancestry, and current main before/after inventory.
Do not dispatch this review branch. Actual execution remains unapproved here.

## Why this grants no additional authority

No repository/environment permissions, branch policies, DB roles, grants, secrets,
old maintenance manifests or clients change. The proposed job uses the existing
database-production protection and its current approvals. Branch eligibility and
credential validity are UNKNOWN; if protection rejects the branch, STOP. Do not
change that policy or move credentials to make the probe run.

It consumes the existing owner credential only to establish a dedicated READ ONLY
connection, with no caller SQL. It checks TLS, exact actual owner identity,
immutable server routing tags and necessary catalog privileges. The server
transaction is read-only before catalog queries. The business activation function
is never called: only its EXECUTE privilege is inspected. All SQL is SELECT or
transaction-local SET. No pilot, revoke, activation, queue or replay functions are
imported by the production probe. Output is fixed booleans and source SHAs; errors
never serialize raw connection details. PASS explicitly has write_admission=false.

Using existing owner authentication is still sensitive. These technical limits
do not replace parent review, environment approval or independent review. This
proposal cannot prove permission to mutate or successful persistent revoke.

## Why the proposed publication gate remains strict

publication-preflight.patch (apply with git apply --unidiff-zero) and
preflight.py.template are INACTIVE. They propose
one extra workflow path only if its normalized content hash equals the exact
reviewed manual-only workflow. Other workflow paths, expanded permissions or even
an unreviewed comment are refused. Existing secret scanning and checks against
extra push/create/workflow_run triggers remain intact. No wildcard or disabled
security check is introduced. The exact hash protects the proposed mode's actor,
scope, protected-environment and no-side-effect boundaries.

proposal_checks verifies the manifest, sanitized scope, unchanged actual protected
files, literal read-only SQL, exact workflow hash, and negative unreviewed-workflow
cases in a temporary tree. Only git's changed-file list is mocked in that scope
smoke test; the actual hash/YAML/workflow audit executes. The proposed gate must
be reviewed separately before any activation. Do not merge all review artifacts
into the older canon test branch; only the separately approved executable files
and exact patches belong in a later activation candidate.

## Independent evidence and metadata SQL

I2_REVIEW.md records the earlier independent offline review. This public package
gets an additional review of publication boundaries and synthetic Ubuntu CI.
The tests use synthetic transports, roles, secrets and server tags. Existing Linux
owner-parser tests also mock connection/snapshot functions and make no DB call.

qualification.sql is a single bounded catalog SELECT: three schemas, thirteen
relations, nine UPDATE columns and one activation-gate signature. It reads no
application rows and calls only built-in metadata/privilege functions. It does NOT
execute a school function. It may help an independently verified read-only browser
metadata channel; output with private server context must remain in that private
channel, not this repository. Missing objects/permissions remain false, with no
self-elevation.

The SQL-editor gateway's pg_stat_ssl is not proof of deployment/client TLS.
An independently observed ssl=false on that backend does not establish that user
HTTPS was unencrypted; it also does not satisfy the resident verified-TLS gate.
Role/ACL metadata is supplementary evidence, not qualified runtime connection proof.

Checks: python -m tools.canon_auth.proposal_checks and the synthetic-only CI job.
Manifest content hashes use normalized UTF-8 text, making them stable across LF/CRLF.
Rollback: discard this branch or revert these exact review files. No DB rollback
is required, because this publication and CI have no live mutation path.
