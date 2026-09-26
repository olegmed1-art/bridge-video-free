# Native CLI permission transaction engine

Preparation only for #1946 and the plan in #1963. This is a transaction component,
not an enabled production command. No production CLI, workflow dispatch, owner
credential binding, automatic apply, or real maintenance coordinator is supplied.

The production caller still must verify the exact Neon branch/endpoint, reviewed
code revision, protected reference provenance, live Light HOLD/process identity,
complete privileged-writer coverage, external operator freeze and both required
GitHub concurrency groups. Database/session/current-role equality alone cannot
authenticate a Neon branch. The base MaintenanceGuard refuses every change.
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
