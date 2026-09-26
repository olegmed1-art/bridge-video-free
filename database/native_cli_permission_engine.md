# Native CLI permission transaction engine

Preparation only for #1946 and the plan in #1963. This is a transaction component,
not an enabled production command. No production CLI, workflow dispatch, owner
credential binding, automatic apply, or real maintenance coordinator is supplied.

The production caller still must obtain fresh authenticated Neon control-plane
endpoint-to-branch mapping and verify the reviewed code revision, protected reference provenance, live Light HOLD/process identity,
complete privileged-writer coverage, external operator freeze and both required
GitHub concurrency groups. Database/session/current-role equality alone cannot authenticate a Neon branch.
The engine now also enforces the connection/server binding described below. The base MaintenanceGuard refuses every change.
The fixture's CIGuard is a test stub, never evidence of production exclusion.

API:
- snapshot: read catalog metadata and dormant-state counts under the expected
  database and owner identities. The caller obtains independent review of the
  exact snapshot digest; simply hashing an unreviewed current database is not
  approval.
- prepare: require that approved digest, disabled single config, zero receipts
  and nonterminal tasks, six effective EXECUTE denials, denied helper/native
  tables/columns, and schema USAGE. Create a private non-overwriting manifest
  containing before and the exact expected six-entry after state. Return its
  SHA256 for the caller's separately trusted record.
- change: verify file identity/hash/target/delta, external guard and dormant state;
  acquire bounded table locks, compare the full captured state, perform exactly
  six grants or revokes, verify the expected state and guard before COMMIT.
- inspect: use a fresh owner connection to classify BEFORE, AFTER or DRIFT against
  the retained manifest. This supports recovery when the outcome of COMMIT is
  unknown; never treat a network exception as evidence that COMMIT failed.

Manifest creation requires an owner-private directory. Files are mode0600,
created with O_EXCL/O_NOFOLLOW and fsynced with their directory. Reads reject
symlinks, public permissions, wrong owner and wrong trusted SHA. Manifests contain
private catalog/role metadata, never passwords or raw function bodies. Do not
commit or print them; retain them in the protected operator evidence store.

The snapshot includes all ordinary autopilot functions, selected catalog metadata
for autopilot relations/columns/triggers, schema ACL, role attributes/memberships,
native config and receipt/queue counts. It does not claim complete transitive
coverage of external functions, policies, connection routing or all data. The
production review must resolve that coverage before supplying a reference.
OID values are intentionally bound to the same database catalog, not portable
between reconstructed databases.

The manifest must be durable before writes. A second apply against AFTER is
refused, as is rollback against DRIFT. An expired/lost guard before commit rolls
back the transaction. A lost commit response requires inspect from a new
connection while real maintenance exclusion is retained. No blind grant retry,
blind revoke, admission transition or task recovery is implemented.

The exact-engine disposable PostgreSQL fixture tests wrong identity/reference,
unavailable maintenance, lost guard after GRANT, altered manifest, enabled config,
committed visibility, rejected apply replay, preservation of intervening ACL
drift, committed revoke and complete cleanup. It deliberately discards a
successful apply return value; this is outcome reconciliation, not a real
network-fault injection. Existing-receipt, actual transport interruption and the
production maintenance implementation remain separate outstanding cases.

The fixed localhost fixture guard is reused from #1966, and actual mutations are
restricted to that disposable PostgreSQL job. The component itself can accept a
privileged connection, so production callers must not wire it before the above
requirements are reviewed and met.

## Connection and server identity binding

A Target carries an immutable NeonBinding: independently selected project ID,
branch ID, endpoint ID and exact direct AWS Neon hostname. These values are part
of both the snapshot digest and private manifest; a copied catalog on a different
branch cannot satisfy the same target merely by preserving database/role names.
Every identity check (including fresh-connection inspect) verifies libpq's actual
host and port, single configured host, sslmode=verify-full and gssencmode=disable.
Startup options and explicit hostaddr are refused. Pooler endpoints are excluded.
Connection parameters are inspected in memory and never logged.

The same SQL connection reads pg_catalog.pg_settings for exactly neon.project_id,
neon.branch_id and neon.endpoint_id. Values must match the target. Project and
branch must have postmaster context; endpoint must have superuser context. All
three must originate in configuration file, match reset_val and have no pending
restart. Missing extension settings, session overrides and user-defined custom
GUC placeholders are refused. This policy is deliberately strict: provider
changes require a reviewed policy update. Compute ID is not pinned because
compute replacement does not itself change the authorized branch.

Read-only discovery on 2026-09-26 confirmed these contexts and sources on the
selected production branch. This is server identity evidence within the managed
provider trust boundary, not protection against a compromised provider or
privileged server operator. Fresh authenticated control-plane mapping remains
required in the future production coordinator, particularly after a restore or
endpoint rebind; this module does not implement an API client or operator freeze.
No live positive psycopg TLS integration test or production permission change
was performed for this addition.

Unbound targets are accepted only for the exact existing localhost:5432
bridge_school_ci / postgres / bridge_ci_owner / native_commit_login regression
fixture, with actual loopback hostaddr and no configured address/startup-option
override. The trusted local CI host must not proxy localhost to an external server.
This is a test allowance, not production authorization. Manifest format
remains version 1 but old targets lack the binding field and are rejected; prepare
a new independently reviewed manifest instead of editing an old one.

Tests cover synthetic valid metadata and mismatched IDs, contexts, setting
sources/reset values, pending restart, missing settings, routing overrides and
weaker TLS settings. Actual disposable PostgreSQL also rejects custom Neon GUC
placeholders, refuses a bound target on localhost, and exercises the complete
existing grant/commit/inspect/drift/rollback lifecycle. The synthetic positive
case verifies validation logic only, not Neon connectivity or certificate trust.
