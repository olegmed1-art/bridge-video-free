# Native CLI grant and rollback rehearsal

Preparation only for Oracle Light recovery (#1946, plan #1963). This fixture is
not a production apply/rollback package and is never run by the migration runner.
Run only in the existing disposable PostgreSQL 18 Database CI job after the
migrations, invariant tests and native runtime lifecycle fixture.

The CI job pins the disposable localhost DSN. The SQL additionally requires the
`bridge_school_ci` database, postgres session/current identity, and absent test
roles. All test roles, temporary routines and grants are rolled back at the end.
The fixture invokes no native RPC and contacts no cloud provider.

Checks performed:

- Exact six EXECUTE grants to a test role with inherited schema USAGE; no direct
  table access or private authority-helper EXECUTE.
- Successful grant and explicit REVOKE restore captured function/table/schema
  ACLs, definitions, membership, config and zero receipt count.
- Enabled configuration, changed function definition, preexisting direct or
  inherited grant and changed membership reject apply.
- An injected failure after apply rolls back all mutations of that subtransaction.
- Unexpected intervening ACL changes reject rollback rather than erasing them.
- Final outer rollback removes the test roles.

The output includes `NATIVE_GRANT_ROLLBACK_REHEARSAL_PASS` and seven
`NATIVE_DEFINITION_SHA256` rows from the complete migrated CI catalog. Compare
these with owner-observed fingerprints before concluding migration equivalence.
Hash equality covers the represented definitions, not all dependency semantics.

Limitations: apply and explicit revoke run within one outer transaction. This
does not test cross-commit recovery, production owner permissions, arbitrary
inherited role graphs, database/role negative paths, existing receipt faults,
or real concurrent operators. Drift injection is sequential; it is not proof
of serialization. Protected production manifests, dependency/trigger audit,
reviewed maintenance exclusion, fresh live HOLD checks and a production-specific
apply package remain required. No config activation, deployment or pilot is
authorized by this rehearsal.
