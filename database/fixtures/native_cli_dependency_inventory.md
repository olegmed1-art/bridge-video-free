# Native dependency inventory

Read-only input to the pending Light production permission package (#1946,
#1963). This does not grant permissions or prove production apply readiness.

Run `native_cli_dependency_inventory.sql` as one complete block through the
already authorized owner SQL console. Inspect identity: intended database,
current/session owner and read_only=on. Keep the output in the private operator
record; it contains catalog metadata, never function bodies or credentials.
The block uses a repeatable-read read-only transaction and ends with ROLLBACK.
The same file is executed in disposable Database CI for reference evidence.
It also lists the known Light login's transitive membership edges/options and
role attributes (without passwords), and visible relevant owner/admin sessions
(PID and role only, no query text, application names or addresses). The Light
role is absent in disposable CI, so its role arrays there are empty. Production
must contain the expected login. Session visibility is incomplete evidence and
does not prevent a new privileged session from starting. `visible_selected_sessions`
selects only same-database client sessions of superusers, CREATEROLE roles,
the current user, or direct owners of autopilot functions. It omits, among
others, roles able to SET ROLE to an owner, function grant-option holders and
membership ADMIN OPTION holders without CREATEROLE. It cannot establish an
exclusive maintenance window. The upward runtime membership graph is not a
complete inventory of all operators or their authority.

It reports five named native dependency families (including overloads), the
user triggers of seven affected tables and their function fingerprints, plus
per-function fingerprints and an aggregate fingerprint of all autopilot functions. The aggregate includes
exact signatures and definition hashes in C collation order, not database OIDs
or owners, so independent catalogs can be compared. It intentionally fails to
establish completeness on its own: PL/pgSQL calls may be dynamic or indirect;
functions outside autopilot appear with null trigger-function metadata, and
missing functions/tables or an empty catalog must be treated as missing evidence.
Deparser output (including pg_get_functiondef/pg_get_triggerdef) is version-sensitive.
Require a reference reconstructed on the same PostgreSQL major version; prefer
the exact server_version_num and pinned container version. The report supplies
server_version_num for this check. Across versions, do not treat a mismatch as
proven source drift or equality as proof of semantic compatibility; reconstruct
the matching reference and review the source/semantics first.
Compare the inventory with that pinned CI reference, investigate any differences,
and review dependency semantics rather than treating a matching digest as
authority to grant privileges. Owner/ACL/role closure and migration registry
validation remain separate inputs.

Table locks can exclude conflicting DML while an apply transaction is open;
they do not establish exclusion of all concurrent owner function/role/ACL
changes. PostgreSQL advisory locks require all participants to cooperate.
The production procedure therefore still requires a bounded maintenance window
that excludes other privileged schema/role operators and migration runners,
plus current pre/post metadata and full live HOLD checks. No superuser catalog
locks, privilege escalation, session termination or protection bypass is proposed.

Reference: https://www.postgresql.org/docs/18/explicit-locking.html

## Catalog reconciliation — 2026-09-26

Evidence is diagnostic only; production remains unchanged and this does not
authorize privileges, migration 0372, task admission, or HOLD release.

Reference code: `f76edc655417099ee779c3163eca3ea9ce9f255f`.
Database CI run:
https://github.com/olegmed1-art/bridge-video-free/actions/runs/36211840622
(job `108319842269`). The inventory runs immediately after migrations and again
after invariant tests and the two ACL rehearsals. Both snapshots contain 93
functions and 11 selected triggers. Per-function fingerprints and trigger
records are identical before/after tests; both catalog digests are
`8d576df89b330c6e3dc5ab4fce156f50ca83102ab76bfce0b6cc3d6e56711860`.
The suspected test-induced definition drift was therefore not observed.

The owner inventory contains 96 functions and 12 selected triggers. All 93
reference function signatures are present; 84 definition hashes match exactly.
The nine differing definitions were classified using read-only catalog reads,
source comparison, and SELECT-only reconstruction of expected definition
hashes. No reconstructed definition was executed.

| Difference | Evidence and interpretation |
| --- | --- |
| Seven formatting differences | `blocker_remediation_action`, `blocker_repository_repair_allowed`, `project_work_blocker_action`, `project_work_progress_token`, `provider_circuit_decision`, `materialize_blocker_remediation`, `on_role_dispatch_provider_success`. Body token sequences match source after excluding comments/whitespace while preserving string literals; replacing only the body text in a SELECT reproduces the exact CI definition hash. Sources: migrations 0347, 0348, 0349 and 0361; materializer source includes the mailbox rotation to 1703 established by 0357. |
| Known retired-fence version | Live `claim_next_task` matches `RETIRED_FENCE_SHA` in `ops/oracle_light_resume_probe.py`. Applying the literal anchor/replacement from migration 0355 to the definition string in a SELECT reproduces the exact CI hash. This explains the definition difference; it is not a new authorization to remove or restore a fence. |
| Missing 0372 | Migration registry lacks 0372. Applying its two `AUDIT_PASS` alias replacements to the live `on_project_work_task_terminal` definition string in a SELECT reproduces the exact CI hash. Applying the migration is a separate production action. |
| Three extra recovery functions | `light_1867_admin_input_valid`, `light_1867_admin_recover`, `light_1867_admin_snapshot` matched the historical recovery installer in the preceding review. Owner-only ACLs and no Light-login EXECUTE were observed then; their old-branch guards must not be treated as current recovery readiness. |
| One extra trigger — unresolved provenance | `role_dispatch_outbox_enabled_role` calls `enforce_enabled_role()` before INSERT or UPDATE OF role on `autopilot.role_dispatch_outbox`. The guard rejects disabled/unknown roles. Its exact installation source has not been located; retain it and explicitly account for it in any future rehearsal. |

A fresh read-only state check also observed native CLI configuration disabled,
zero native receipts, and 0372 absent. These facts do not attest current host
HOLD, prove transitive dependency completeness, or exclude concurrent privileged
DDL. The production permission package still needs the existing maintenance,
ACL, identity, rollback and live-host evidence requirements.

The workflow change received an independent review pass. SQL hash reconstruction
provides a separate exact-definition check; neither check promotes this diagnostic
to production readiness. The full owner output and function bodies remain private.
