# Proposed recovery registry credential route

Status: draft for owner decision. This document grants no activation permission.

The current `NEON_DATABASE_URL` route fails authentication in registry scope
observations. The independent owner maintenance route reaches the expected Neon
database and role. A separate manual read-only candidate probe must validate the
maintenance credential against the registry's exact target, URI options, role,
read-only transaction and catalog checks before this cutover can be approved.

The proposed workflow change selects `LIGHT_MAINTENANCE_DATABASE_URL` explicitly
for both the registry writer and its owner-attestation observer. There is no
fallback, secret copying or rotation, role grant, or change to the record-writing
SQL. A registry-specific read-only preflight validates the exact workflow,
current main SHA, owner and triggering owner before and after checking the
database. It supports the existing push and workflow_dispatch triggers without
loosening the shared maintenance context checker. A failed check stops the job
before the record writer.

Automatic writes remain for owner-triggered main pushes changing recovery JSON
records or the record-writing Python script. Pushes triggered by another actor
now refuse. Manual recording still requires `RECORD` and a checked-in record.
The writer workflow's own path is removed from its push path filter, so merging
this credential-only patch cannot itself write a recovery record. None of this
patch's changed paths matches the remaining automatic record triggers.

Before activation: exact-head CI and I2 acceptance, a successful live read-only
candidate probe, and owner approval of this explicit credential route and the
remaining automatic writes. Fresh main and writer state must be checked again.
After activation: read-only owner/registry attestation must succeed before a
separately authorized recording operation. No recording is part of this draft's
validation; no pilot, permission grant or service restart is included.

The new preflight is an observation, not a durable admission lock. Maintenance
continues to pause and drain this writer. The registry's old secret and its
other consumers are untouched; this change does not claim that the old secret
has been repaired globally.

Rollback: revert the route and observer workflow changes. Because the old route
is known to fail authentication, reverting prevents successful registry writes
until credentials are reconciled; do not present rollback as restored service.
The failed native restore request and its journals remain quarantined separately.
