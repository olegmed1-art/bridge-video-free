# Main-only owner inventory installation candidate

Installation base: 4f2c7a98d0b7e1e5f48ac781332442becea302dd.
This separate source PR applies the maintained canon-readonly extension to the
actual native-maintenance-owner-attest workflow. It is not another template merge.
Draft publication and isolated synthetic CI are authorized; main merge and live
dispatch remain blocked until parent release checks give GO.

Only the protected owner workflow changes among existing files. Publication
preflight and existing owner credential parser are unchanged. No publication-gate
exception is introduced. Original maintenance job, environment and exact main/
owner/manual/source checks are preserved; scope defaults to maintenance.

The new mode requires main, repository owner actor and triggering actor,
workflow_dispatch, canon-readonly scope, and externally reviewed exact main/probe
inputs equal to event SHA. Code checks exact checkout and live main before/after
inventory. There is no hardcoded runtime main pin or installation self-hash.
After separately approved installation, the operator must review the resulting
installed main SHA and supply it in both inputs. Source review does not authorize
that invocation.

External owner policy confirmed at 2026-10-04 20:09:33 UTC: database-production
selected exactly main (one branch, zero tags). Feature branches remain ineligible.
No environment settings, permission grants, secrets or identities change here.
Existing credential names/parser are reused; no credential value is exported.

Both inventory transactions pin and verify pg_catalog before SELECTs, qualify
builtins and types, and request force_rollback=True. Rollback failure cannot produce
PASS. Statements only read fixed metadata or constrain their own transaction.
No school gate function or application rows are read/executed. No hostile live
search_path was observed. Parent independent review of c3cd5b0 passed these changes.

Installation checks validate the actual changed workflow and its manifest, compare
maintenance behavior to the exact installation base, require publication preflight
and owner parser equality, and reject extra source/workflow scope. They report the
active workflow as changed and validated, not unchanged. Installed YAML tests
exercise positive/default and negative event/ref/actor/SHA/scope choices; source
guards are independently exercised through the actual Python entrypoint with
synthetic adapters. Synthetic CI has no environment/secrets/database service and
never invokes the owner entrypoint. PR owner jobs are gated off; contract job uses
synthetic transports and existing mocked Linux parser contracts only.

Live credential validity, verified runner TLS, owner target and teacher/gate runtime
behavior remain unqualified. Main merge alone will not establish production
readiness. The previous catalog qualification is supplementary evidence only.

Rollback: separately revert the exact source installation commit under release
coordination; no production database rollback is needed for source-only installation.
Local Windows helper cannot start commands (apply deny-read ACLs); no bypass is used.
Other worktrees remain untouched.
