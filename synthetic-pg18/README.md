# Generic synthetic PostgreSQL protocol rehearsal

This stand-alone fixture contains no production schema, data, identifiers,
endpoints, credentials or project imports. A successful run proves only this
model. It cannot authorize a real migration or certify a production restore.

The official PostgreSQL 18.6 bookworm linux/amd64 image is pinned by platform
digest. Two disposable containers use network=none, no host mounts, read-only
root filesystems, synthetic tmpfs databases, dropped capabilities and bounded
resources. SQL clients connect through container-local Unix sockets. The host
pulls the image before execution; the test containers have no network access.

The test checks full custom dump/restore; per-table counts and SHA-256 digests;
owners, table/function/schema/default ACL and role flags; FK and trigger state;
sequence last_value/is_called and next values; denied operations; two claimers;
late/duplicate/conflicting/NULL receipts; uncertain send-intent retry HOLD;
source/target writer fences; and reconciliation of a known synthetic delta.

It does not simulate actual external delivery, kill a client during COMMIT,
implement general reverse delta capture, restore a real source schema, test
production login authentication/RLS/TLS, or measure production performance.
The row-lock test has a launch barrier, not a proof of every possible interleaving.

The workflow runs twice on the same immutable image to check repeatability.
Random archive hashes are not expected to match across independent clusters.
It prints a bounded synthetic result to logs; it uploads no artifacts or caches.
