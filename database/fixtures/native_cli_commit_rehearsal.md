# Native grant cross-commit rehearsal

Preparation for #1946 / #1963 only. This script is not a production apply package.
It accepts only the literal localhost disposable CI DSN and verifies database,
session and current identities before any mutation. It creates two previously
absent NOLOGIN fixture roles, grants inherited schema USAGE, and finally removes
only those fixture roles and privileges. Original metadata must be restored.

Run after the existing native ACL fixtures:
`python database/fixtures/native_cli_commit_rehearsal.py`

The existing CI environment supplies psycopg and ADMIN_DATABASE_URL. No native
RPC, external provider call, live-server command or production connection occurs.

Verified scenarios:
- The non-superuser migration owner issues exactly six owner-granted EXECUTE
  entries, with no grant option. Function identities, owners and definitions,
  the private helper, non-target ACL entries, memberships, table/column ACLs,
  schema and config must stay unchanged.
- A postcheck exception rolls back the grant transaction; a new backend verifies
  no partial grants survived.
- Successful grants COMMIT and are visible from a fresh connection.
- A separate privileged connection adds helper EXECUTE to the inherited parent.
  Rollback refuses the changed manifest and preserves the independent change.
  After explicit removal of that test change, a new transaction revokes the six
  grants and commits; another connection verifies the original fixture state.
- A second connection changes the helper ACL while the first holds table locks.
  The first connection detects baseline drift. This intentionally proves that
  those table locks **do not exclude privileged function ACL writers**.
- Final cleanup removes test identities and exactly restores the pre-setup
  snapshot. Cleanup is not a production recovery procedure.

Expected markers:
`NATIVE_GRANT_CROSS_COMMIT_REHEARSAL_PASS`
`PRIVILEGED_ACL_WRITER_NOT_EXCLUDED_BY_TABLE_LOCKS_CONFIRMED`

This closes the cross-commit/backend evidence gap of native_cli_grant_rollback.sql.
It does not prove exclusion between a final precheck and COMMIT, cover all
production inherited-role graphs, authenticate the live Light process, or
authorize grants, activation, deployment or a pilot. The production package
still needs a protected manifest and reviewed maintenance exclusion. A
cooperative advisory lock or successful sequential drift test is insufficient.
