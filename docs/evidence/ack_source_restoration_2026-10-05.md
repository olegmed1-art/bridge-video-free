# Restore canonical ACK migration source

Prepared 2026-10-05; review only. No production execution is part of this PR.

Migration 0401 was published on test/ack-null-fingerprint-20261003 in commit
60aed1154e0a55612708c44b67841e373a359f9f. This change restores its exact
1,401 bytes to the canonical migration directory. SHA256:
bea4b407a63256f83f5eecbd8b220406070046edb34d87f849d645b39134dcc7.

The migration-source hash is distinct from the separately reviewed guarded
execution fragment. Original execution evidence is retained outside this public
repository. Restoring source and reconciling the registry now must not be
described as historical execution of the whole repository file.

The migration runner records canonical source hashes. Its existing NULL
bootstrap behavior explains the repair contract, but the general runner must
not be used for targeted operational repair because it can apply unrelated
pending migrations.

reconcile_0401_checksum.sql changes only the checksum of the exact reviewed
0401 row. Every expected target, timestamp, function OID, definition and ACL
digest, and owner must be supplied from a fresh separately reviewed snapshot.
The script rejects target/catalog/registry drift, rewrite rules, inheritance,
RLS and unreviewed triggers. A SHARE ROW EXCLUSIVE lock temporarily blocks
other registry writers/DDL, bounded by a two-second lock timeout and ten-second
statement timeout. This is a write operation requiring the serialized window.
An already populated checksum is never overwritten by repair. The rollback
action restores NULL only when the value is the exact canonical digest.
Both actions preserve the applied timestamp and function definition.

Promotion requires independent review, passing isolated tests, canonical
source publication, and a fresh operational guard check. Run the targeted
script only within a separately approved serialized maintenance window.
Record the reconciliation execution time and original applied time separately.
Do not call ACK, resume admission, change HOLD, or replay an issuer as part of
checksum reconciliation.

Validation: fresh PostgreSQL 18 migrations and SQL invariants; original ACK
NULL/replay/concurrency regression; exact artifact digest; target, OID,
definition, ACL, timestamp, non-NULL conflict and repeated-execution negative
controls; inverse checksum restoration; unchanged delivery state and catalog.
All fixtures use disposable PostgreSQL and no production credentials.
