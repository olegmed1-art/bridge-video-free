# Stage 1 Foundation — evidence checkpoint

Date: 2026-08-17
Status: TESTED candidate, not production OPERATIONAL

## Scope

This checkpoint closes the repository/test exit gate for the first reliability foundation stage. It does not promote candidate database migrations to production and does not change any bridge bidding or teaching methodology.

## Reliability event incorporated before this checkpoint

Main advanced concurrently through PR #106 and intentionally rolled the Bridge Video production route back to confirmed `3.1-free-r25.6`. The reason recorded in that merge is that r25.11 inherited the proven r25.10 ASR hallucination-to-`NO_SPEECH` normalization failure. r25.10/r25.11 were quarantined from the production path rather than treated as improvements merely because they were newer.

This rollback is consistent with the META rules used in Stage 1: preserve the last confirmed stable route, retain failed candidate knowledge, and require evidence before promotion. AlgorithmVersion registration of r25.10/r25.11 in the Truth Layer is identity/provenance only and must not be interpreted as Stable/Operational quality status.

The video rollback did not change the Neon production database migration state.

## Implemented foundation capabilities

### A27 — checkpoint / resume foundation

Migrations `0046–0048` add generic run checkpoint history for `analysis`, `ingestion`, and `projection` runs.

- checkpoint history is append-only;
- sequence assignment is serialized per run;
- the latest event is projected through `latest_run_checkpoint`;
- the canonical run snapshot is synchronized through the guarded `record_run_checkpoint(...)` function;
- runtime workers cannot directly rewrite checkpoint history.

### A28 — corrections / regression knowledge

The reliability core records correction target/class, materiality/severity, regression cases, append-only regression executions, evidence/run provenance and resolution state.

A material correction cannot opt out of regression. Resolution requires actual `pass` regression evidence, not merely a named regression case.

Methodology-class corrections are protected automatically. They default to pending teacher approval, runtime workers cannot forge approval fields, and they cannot resolve until both teacher approval and passed regression evidence exist.

### A38 — provenance / rights / ACL observation

`source_rights_snapshot` stores structured source-rights and ACL observations with provenance. Runtime ingestion may append constrained source-metadata observations but cannot read or rewrite protected snapshot history. These are evidence observations, not autonomous legal conclusions.

The Truth Layer candidate separately supplies canonical Algorithm/AlgorithmVersion identity and append-only StorageVerification evidence.

### A39 — recovery / restore evidence

`recovery_checkpoint` and append-only `recovery_verification` provide structured recovery identifiers/fingerprints without storing secrets.

An actual Neon restore test was completed independently of the schema test:

1. disposable branch `br-weathered-math-b151ckm7` was created from current production;
2. a temporary marker table was created only on that branch;
3. the branch was reset from its parent;
4. the marker disappeared;
5. the restored baseline again showed migration count 19, latest migration `0019_default_function_acl_fix`, 15 health signals `ok`, 11 Bridge Video analysis runs and 15 asset locations;
6. the disposable restore-test branch was deleted.

A separate persistent pre-promotion recovery branch remains preserved:

- name: `recovery-prod-0019-20260817`
- branch ID: `br-bitter-term-b1gkg284`

It must not be modified or deleted before a future reviewed production promotion and its observation period complete.

## Stage-1 deterministic E2E exit gate

Database regression `035_foundation_e2e_provenance.sql` validates the complete canonical chain:

`Source -> SourceAsset/Evidence -> Deal -> AnalysisRun/Input -> RunCheckpoint -> OutputAsset -> Artifact -> ArtifactVersion -> AnalysisRunOutput`

The test asserts exact IDs rather than names/folders, durable completed checkpoint state, exact run-generated artifact linkage and retained SourceID/DealID/EvidenceID/AnalysisRunID provenance. All test facts roll back after verification.

## Migration-sequence reliability

A real concurrent numbering collision was discovered during Stage 1 and corrected before production promotion:

- instructor education scope retains migration `0044`;
- Truth Layer is `0045_truth_storage_provenance`;
- META reliability core is `0046–0048`;
- Truth regression is test `032`;
- META reliability regressions are tests `033–035`.

`database/scripts/migrate.sh` now fails closed before any migration if duplicate four-digit sequence prefixes exist among migrations or database SQL tests.

## CI evidence

PR #105 final candidate run:

- GitHub Actions run `32002663474`
- PostgreSQL 18
- migration installation: success
- all invariant/adversarial database tests: success
- migration idempotence: success
- checksum-tamper rejection: success
- migration registry verification: success

Post-merge Stage-1 main run:

- GitHub Actions run `32002751695`
- Stage-1 merge commit `ad15ecfaae96f5e36a8829fe9ab69d839f8da033`
- all the same gates: success

The subsequent Bridge Video rollback commit `46cdb7830b1e6effbb71422b3f2eb293cd582680` is a separate reliability correction to the video production route, not a rollback of the Stage-1 database foundation.

## Production isolation verified after Stage-1 merge

Direct production Neon verification after the Stage-1 merge still reported:

- migration count: 19
- latest migration: `0019_default_function_acl_fix`
- missing migration checksums: 0
- operational health: 15 `ok`, 0 warning, 0 critical
- Bridge Video analysis runs: 11
- linked AlgorithmVersion rows in production: 0, expected until Truth migration is promoted
- StorageVerification rows in production: 0, expected until Truth migration is promoted

Therefore candidate development did not silently change production.

## Stage-1 exit decision

Repository/test exit gate: **PASS**.

Evidence-backed TESTED candidates now exist for checkpoint/resume, correction -> regression -> passed execution, protected methodology authority, source rights/ACL observations, recovery evidence, actual restore procedure, deterministic Source -> Deal -> Run -> Artifact provenance and duplicate migration/test sequence protection.

`TESTED` does not mean `OPERATIONAL`. Candidate migrations `0020–0048` remain unpromoted to production. Production promotion remains separately blocked by release/recovery/security gates.

## Next ordered stage

Proceed to Stage 2 DDS gate:

1. verify live DDS code/workflow rather than relying on inventory claims;
2. require deterministic replay of the same DealID;
3. verify repeated solve output identity;
4. verify context/transposition reuse evidence rather than merely asserting reuse;
5. keep mass DDS training blocked until the deterministic DDS gate is green.
