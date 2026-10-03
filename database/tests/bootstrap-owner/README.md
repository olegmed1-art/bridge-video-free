# Generic PostgreSQL owner/access rehearsal

Run `python3 database/tests/bootstrap-owner/run.py` with Docker and the pinned
official PostgreSQL image already pulled. The container is network-none with
only its own loopback, no host mounts or production credentials, bounded
memory/CPU/tmpfs, and mandatory cleanup. Two fresh CI repetitions are bounded
by a 20-minute standard public runner job. No artifacts are uploaded.

Fixtures reproduce database ACL versus superuser behavior, a temporary
database-specific HBA TCP rejection, local peer connection, closing admission
while the admitted session finishes, transactional role/schema rollback,
NOLOGIN owner and restricted function execution, owner-sensitive guard behavior,
unknown response reconciliation, and exact fixture HBA restoration.

The guard is a generic renamed predicate model with a positive reference and
negative target tests. This is not publication or validation of private DDL,
not production access, and not permission to modify any server.
