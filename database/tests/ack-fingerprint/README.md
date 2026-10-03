# ACK fingerprint regression

The ACK RPC requires a lowercase 64-hex payload fingerprint, including for an
existing receipt. SQL NULL made both the regex and duplicate comparison UNKNOWN,
allowing an invalid replay to return `accepted=false, duplicate=true, SENT`.
That path does not insert a receipt, mutate the outbox, or send a command. The
normal Python `CodexAck.payload_fingerprint` property always computes SHA-256.

Migration 0401 adds one explicit NULL predicate before duplicate lookup. It
preserves the error family for malformed bodies, the signature, owner, ACL,
search path, security-definer flag and existing valid replay semantics. It is
source-guarded and transactional; reapplication fails closed. Do not apply it
to a working database as part of this preparation branch.

The isolated test extracts the actual ACK function from public migration 0332
and applies the ACK-only context substitutions already public in 0345/0362.
Its minimal synthetic tables are not a production schema. No private catalog,
data, endpoints, credentials or external provider calls are used. The official
PostgreSQL image is pinned; the database container has no network or host mount.

Tests reproduce the pre-fix NULL duplicate, apply the actual migration, test
normal ACK/replay, NULL/empty/malformed/conflicting fingerprints, retry after
discarding a committed response, same-delivery concurrency, NULL/valid races,
conflicting races, metadata preservation and fail-closed migration reapply.
Discarding a result is not an actual network-loss injection. Full migration,
production authentication, all callers and delivery behavior require separate
acceptance. Rolling back this predicate would restore the known validation
defect; prefer fixing forward and keep admission frozen if deployment fails.
