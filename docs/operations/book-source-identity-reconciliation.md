# Reviewed book source identity reconciliation

Preparation only; no production write or target admission follows from this code.

The registry's observed drive:<file-ID> form is compared to the public HTTPS
Drive view URL. Other legacy schemes, mismatched IDs, query strings, fragments
and credentials are rejected. Public bundle/teacher citations stay HTTPS.
Existing source UUIDs and locators are never updated.

A drive: alias additionally requires a source-linked, same-school immutable PDF
asset with the reviewed SHA-256 and byte size. The explicit operator reconciliation
uses an existing admitted connection and a fully bound source-review receipt.
It inserts at most one asset and one source link, preserves a compatible existing
asset ID, and rejects conflicting metadata. Unknown enabled user triggers or
rewrite rules on identity tables stop before inserts; do not disable them.
Dry-run executes the same path with transaction force_rollback. Interruption
rolls back all identity rows. Exact replay creates no new identity rows.

## Row accounting and admission

For one new atom the direct INSERT budget is two identity rows, six staging
rows, and three publication audit rows: eleven new rows total. It is not a
total-write budget: staging also updates one changeset; publication updates
one version and one changeset, giving fourteen direct affected-row operations.
Current staging/publication replay may update statuses even with zero new IDs.
For N atoms, first-run direct inserts are 3*N+8 and direct affected rows 4*N+10.

Foreign-key checks and locks are separate from writes. Identity-table unknown
user triggers/rules are rejected in code. Knowledge/audit triggers and the
existing outbox allocator, delivery workers and catalog synchronization may
have additional writes; their definitions, qualified role, readback counters
and separately approved bounds must be established before live admission.
Do not claim eleven as the end-to-end total or emulate fixture outbox UPDATE
in production. Dry-run calls on production are also not authorized by this PR.

Keep selected-key/source snapshots, exact content/review/anchor hashes,
recoverability evidence and an independently qualified writer. Exact-key
absence does not prove semantic dedup. Final source review, internal rights,
hash/asset reconciliation, delivery proof and parent serialization are required.
No new credentials or grants, source replacement, canon activation or blanket
book publication are permitted.

After successful publication, visibility rollback archives only the selected
WORLD version through retire_book_world, preserving source/asset/links and
audit evidence. It does not delete shared identity records, undo deliveries,
or restore retired versions. Restoration and delivery reconciliation require
their own verified operator procedure.
