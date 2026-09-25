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
an aggregate fingerprint of all autopilot functions. The aggregate includes
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
