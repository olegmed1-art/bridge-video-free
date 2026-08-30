# Bridge School database

PostgreSQL 18 migration package for the School of Sports Bridge.

## Production target

- Managed PostgreSQL: Neon
- Project: `bridge-school-core`
- PostgreSQL: 18
- Neon branch: `production`
- Protected GitHub secret: `NEON_DATABASE_URL`
- Deployment gate branch: `database-production`

## Safety model

1. Ordinary CI never connects to Neon. It uses a disposable PostgreSQL 18 service database inside GitHub Actions.
2. CI and production preflight run migrations as a managed-owner-compatible non-superuser role with `CREATEROLE`, so Neon privilege differences are tested before production.
3. Production migration is triggered only when a tested commit is explicitly promoted to the `database-production` branch. A normal push to `main` cannot modify Neon.
4. Historical migration files are immutable after application. `database/scripts/migrate.sh` records SHA-256 checksums and fails if an applied migration is edited or disappears.
5. A migration must register itself in `schema_migration` before it is considered valid.
6. Production receives only unapplied migrations; already-applied migrations are skipped after checksum verification.
7. Source files in Google Drive are never moved, renamed, deleted, overwritten, or permission-changed by database migration code.
8. Raw/source observations and runtime event facts are append-only. Corrections create new observations/events.
9. Partial analysis output remains staging until an explicit publication generation is activated.
10. Student identity is scoped by `(school_id, person_id)` and external identity decisions remain reversible.
11. Runtime services use NOLOGIN capability roles; credentials are provisioned separately and never receive owner rights.
12. Runtime roles receive no persistent-schema CREATE and no DELETE on school data.
13. Tournament source identities remain source-scoped until an explicit `EntityResolutionDecision` is recorded; a name match alone cannot attach an external result to a Student.
14. Tournament `TableResult` rows are append-only source facts. Exact redelivery is deduplicated, while provider corrections create new rows linked to the previous result.
15. Knowledge identity and knowledge content are separated. A worker may create candidate/versioned knowledge, but it cannot activate school canon.
16. Canon activation is an explicit administrative record; overlapping active versions for the same knowledge item and scope are rejected.
17. Media/transcript/evidence provenance is preserved: corrected transcripts are new Transcript objects, evidence locators and transcript segments are append-only runtime facts, and generated Artifact versions preserve their source/knowledge dependencies.
18. Student profile observations, profile snapshots, profile components, inferences and recommendations are derived append-only history. The current profile is a selected projection generation, not a row that is continually overwritten.
19. Every selected profile input is recorded explicitly. An observation produced by an `AnalysisRun` can enter a profile only when that exact output has been explicitly published; staging/partial analytical output cannot leak into the current profile.
20. A tournament-derived Error/SuccessObservation must retain the exact `TournamentIdentityAttribution` and `EntityResolutionDecision` that justified associating the external result with the Student.
21. Projection generation switching is atomic and guarded. Runtime workers cannot edit the current-generation pointer directly; they can activate only a completed successful `ProjectionRun` through `activate_projection_generation()`.
22. Recommendations are not facts. Recommendation content and lifecycle are separate append-only records, preserving created/accepted/applied/superseded/expired/rejected/invalidated history.
23. Derived-object dependencies are explicit DAG edges. New profile observations, profile inputs, recommendations and session-plan usage automatically register causal `derived_from` / `depends_on` edges instead of relying on hidden application memory.
24. Invalidation is causal and append-only. One `invalidation_batch` records the root cause and every affected descendant receives an `invalidation_record` with its dependency depth; existing historical generations are never deleted or rewritten.
25. A current profile may remain readable but explicitly `stale` while recomputation is pending. This avoids silently serving an old projection as if it were fresh and avoids deleting the last usable profile before its replacement exists.
26. Only current projection generations are queued for recomputation. Historical generations keep their invalidation history but do not consume worker capacity.
27. Repeated invalidations of the same active projection scope coalesce into one pending/running recompute request while every causal invalidation is preserved in `projection_recompute_cause`.
28. Recompute claiming uses a durable queue and `FOR UPDATE SKIP LOCKED`; claim/fail/retry/complete transitions are guarded and also preserved as append-only state events.
29. A recompute request can succeed only after a matching successful `ProjectionRun` has been completed and its generation has already been atomically activated as current.
30. Runtime workers cannot directly mutate invalidation/recompute tables; they receive only guarded functions for invalidation and queue lifecycle transitions.
31. Operational health thresholds are explicit administrative configuration, separate from application code and separate from bridge pedagogy. Runtime roles can inspect them but cannot relax them.
32. Operational health read models expose only technical metadata/counts/ages; they do not expose database passwords, source payloads or Drive file contents.
33. Repository-to-database migration checksum drift remains enforced by `migrate.sh`, because PostgreSQL cannot inspect repository bytes. Database health separately reports missing checksums and the runtime migration fingerprint.
34. An `unknown` asset-location state is not treated as a failure merely because verification has not yet been deployed. Only an explicit `availability_status='unavailable'` is classified as an unavailable-storage fault.
35. A health-read-model defect is corrected forward by a new migration rather than hidden. `0015_operational_health_checksum_fix` corrects the checksum signal introduced in `0014` and keeps the failed CI diagnosis reproducible in repository history.

## Runtime capability roles

- `bridge_school_reader` — SELECT access across the school schema; no writes.
- `bridge_school_app` — inherits reader and may INSERT/UPDATE only interactive operational state such as students, groups, partnerships, sessions and student work.
- `bridge_school_worker` — inherits app and may additionally write ingestion, analysis, projection, generated learning content, homework/tournament state, candidate knowledge, artifacts, transcripts, evidence, assessments, profile generations and recommendations.
- `domain_event` and `source_observation` are INSERT-only for runtime worker access.
- Event publication is exposed to the worker only through guarded `publish_outbox_event()`; direct `allocate_event_position()` execution is not available to runtime roles.
- Exercise-attempt assessments, tournament result facts, tournament identity-attribution history, transcript segments, evidence/evidence links, quality assessments, profile observations/snapshots/components/inferences and recommendation history are append-only for the worker.
- KnowledgeVersion content is not runtime-rewritable; only lifecycle/review columns may be updated by the worker.
- CanonActivation, Algorithm/AlgorithmVersion and ProjectionPolicyVersion remain administrative/owner-write state.
- Projection generation activation is available to the worker only through `activate_projection_generation()`; direct writes to current/activation tables are denied.
- Dependency registration for profile/recommendation objects is done by internal trigger functions; those trigger functions are not directly executable by runtime roles.
- `invalidation_record`, recompute queue rows, causal links and queue-state history cannot be mutated directly by the worker. The worker can only call `invalidate_dependency_subgraph()`, `claim_projection_recompute()`, `complete_projection_recompute()`, `fail_projection_recompute()` and `retry_projection_recompute()`.
- Readers can inspect `current_student_profile_status`, which exposes the selected current generation together with its latest validated/stale state.
- Readers can inspect `database_runtime_fingerprint`, `operational_health_signal`, `operational_health_issue` and `operational_health_summary`; no runtime capability can edit `operational_health_policy`.
- Migration history and selected administration/configuration state remain owner-only for writes.
- Runtime principals are created as NOLOGIN roles. After an external credential is explicitly provisioned, the same narrowly scoped principal may be changed to LOGIN without receiving administrative attributes. No database password is stored in this repository.

## Migration order in the repository

- `0001_global_registry.sql` — School, Source, Asset, locations, ChangeSet, DomainEvent, outbox, ingestion.
- `0002_learning_core.sql` — Person/Student, SourceIdentity, learning/bridge core, assessments, analysis and projections.
- `0003_event_publication_order.sql` — partition-local replay cursor assigned after publication.
- `0004_integrity_guards.sql` — agreement overlap guard, dependency DAG, atomic outbox publication helper.
- `0005_runtime_roles.sql` — least-privilege reader/app/worker database capabilities.
- `0006_event_immutability_hardening.sql` — append-only event/source facts and guarded publication boundary.
- `0007_learning_context.sql` — groups, partnerships, course versions, sessions, participation, plans and semantic episodes.
- `0008_exercises_homework.sql` — versioned exercises, assignments, recipients, submissions, attempts and separate append-only attempt assessments.
- `0009_tournament_data.sql` — tournaments, entries, source-scoped participant identities, explicit identity attribution, boards and append-only table results with correction lineage.
- `0010_knowledge_media.sql` — knowledge/version/canon graph, gaps, artifacts, media/transcripts, evidence/quality, algorithm registry and explicit analysis inputs/outputs.
- `0011_student_profile_projections.sql` — SkillAssessment/MetricObservation/Error/Success observations, immutable profile snapshot components and exact inputs, guarded projection-generation activation, inferences and recommendation history.
- `0012_tournament_profile_identity_guard.sql` — requires explicit tournament identity attribution/resolution for student-facing learning observations derived from TableResult.
- `0013_projection_invalidation_recompute.sql` — automatic derived-object dependency registration, causal invalidation batches, stale-profile state, current-generation-only recompute scheduling, durable/coalescing recompute queue and guarded claim/fail/retry/complete lifecycle.
- `0014_operational_health.sql` — technical health policy plus read-only database fingerprint/signals/issue/summary views for migration integrity, stuck work, outbox, ingestion/analysis/projection/publication, recompute backlog, stale profiles, pending references and explicit storage unavailability.
- `0015_operational_health_checksum_fix.sql` — forward correction of the migration-checksum health signal so it counts only rows whose checksum is actually missing; public issue/summary views are rebound to the corrected signal view.
- `0056_universal_video_queue.sql` — project-neutral Drive video queue with atomic bulk intake, canary release, fenced leases and permanent SHADOW/REVIEW-only result guards.
- `0300_autopilot_oracle_shadow.sql` — Oracle-resident Autopilot shadow queue with
  allow-listed tasks, event dedupe, leases/fencing, external waits, evidence,
  budget stops and least-privilege RPCs.
- `0301_autopilot_github_pr_read_only.sql` — first real external capability:
  an exact-head, draft-only, zero-cost public GitHub PR snapshot executed by the
  Oracle worker without credentials or mutation.
- `rollbacks/0056_universal_video_queue.sql` — fail-closed rollback; refuses to remove a non-empty queue.

The exact production state is the `schema_migration` registry in Neon, protected by migration checksums.

## Operational health read models

`database_runtime_fingerprint` reports the actual PostgreSQL server version, database name, migration count/latest migration and whether any registered migration is missing a checksum.

`operational_health_signal` returns one row per technical signal with `severity` (`ok`, `warning`, `critical`), numeric current value, thresholds, oldest affected timestamp and compact diagnostic details. Age values are expressed in seconds. The initial technical thresholds are stored in `operational_health_policy` and can later be tuned by the database owner after real production behavior is observed.

`operational_health_issue` contains only non-OK signals. `operational_health_summary` rolls them up to one school-level status. Reading these views is safe for a future read-only health endpoint; no source content or credentials are returned.

The first CI execution of `0014` exposed a defect in the migration-checksum signal: the CTE used the total migration count instead of counting only `checksum IS NULL`. `database_runtime_fingerprint` itself was correct. The fix is intentionally recorded as `0015_operational_health_checksum_fix` and is covered by `009_operational_health.sql` before production promotion.

This layer deliberately does **not** create a scheduled production monitor using the owner connection string. Continuous monitoring should receive a dedicated read-only LOGIN credential when the real backend/worker credential layer is provisioned. The existing production migration job may query these views while it is already connected, but the owner secret is not broadened into a general monitoring credential.

## Automated tests

- `001_invariants.sql` — core identity, event, ingestion and dependency invariants.
- `002_runtime_permissions.sql` — least-privilege and append-only runtime permission boundaries, including guarded invalidation access.
- `003_learning_context.sql` — groups, partnerships, sessions, participation, plans and episode constraints.
- `004_exercises_homework.sql` — exercise/homework relationships, recipient/submission consistency, selected attempts, assessment history and permissions.
- `005_tournament_data.sql` — tournament/source scope, explicit identity resolution, exact-result dedupe, correction lineage, NS/EW entry scope and runtime permissions.
- `006_knowledge_media.sql` — knowledge-source scope, canon overlap, artifact/media scope, transcript/evidence provenance, analysis input typing and runtime/admin boundaries.
- `007_student_profile_projections.sql` — student/metric/skill/topic scope, exact profile inputs, analysis-publication barrier, tournament identity provenance, immutable snapshots, atomic generation activation, recommendation provenance and runtime boundaries.
- `008_projection_invalidation_recompute.sql` — automatic dependency registration, recursive invalidation depth, stale profile state, recommendation/plan invalidation, active-scope queue coalescing, worker claim/fail/retry, activation-before-completion requirement and current-profile read-model switch.
- `009_operational_health.sql` — runtime fingerprint, baseline signal registry including corrected migration-checksum status, critical classification for stuck changesets/analysis/recompute/pending references/explicit unavailable storage, roll-up summary and read-only runtime permissions.
- `041_universal_video_queue.sql` — idempotent intake, canary gating, independent claims, fencing, heartbeat, REVIEW terminalization and capability isolation.
- `300_autopilot_oracle_shadow.sql` — task/event idempotency, fencing,
  wait/resume/dedupe/expiry, stale recovery, evidence, budget stop and runtime
  ACL boundaries.
- `301_autopilot_github_pr_read_only.sql` — strict GitHub target/payload gates,
  zero-cost enforcement, exact capability mapping and retained evidence.

All tests execute inside transactions and finish with `ROLLBACK`; they leave no test records in production-style databases.

## Development flow

1. Create a new numbered migration; never edit an already-applied migration.
2. Open a pull request to `main`.
3. `database-ci.yml` builds a clean PostgreSQL 18 instance, applies all migrations, runs invariant and permission tests, re-runs migrations for idempotence, and tests checksum tamper detection.
4. Merge only after CI succeeds.
5. Promote that exact tested commit to `database-production`.
6. `database-production.yml` repeats the managed-owner-compatible preflight on a disposable PostgreSQL 18 instance and only then applies unapplied migrations to Neon.

## Secrets

No database password or connection string is stored in the repository or printed by the workflows. Production owner access is supplied only through the GitHub Actions secret `NEON_DATABASE_URL`. Runtime login credentials will be separate from the owner connection and will be created only when an application/worker actually needs them.
