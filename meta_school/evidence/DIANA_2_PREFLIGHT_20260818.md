# Диана 2 — preflight перед запуском

Дата проверки: 2026-08-18
Статус: `HOLD_FOR_PRODUCTION_STAGING_MIGRATION`
Видеообработка не запускалась.

## MASTER и источник

- MASTER folder: `Полный видеокурс Дианы`, Drive ID `1aQRFIlKrGjePmrDUaohppmygXp4MwAMo`.
- MASTER video: `Диана 2..mp4`, Drive ID `1ACfY5ksRaYPKsUfd23cvcoSQbCe6GJr8`.
- MASTER size: `680323194` bytes.
- Historical original: Drive ID `18l8AvNDUeX4VF_E0SXjl6st6EizWglZ-`.
- Original remains in its historical parent and was not renamed, modified, moved or deleted.
- MASTER copy was created through Drive-native copy; no download/upload relay was used.
- Additional Drive quota consumed by the MASTER copy: approximately 680 MB.

## Isolated result routing

- Result folder for lesson 2: `1-N-OGCsrrZuk4gsFoe89KwVF7VegMNGP`.
- Work/receipt folder for lesson 2: `1n2hLtQ3XlZbvG2mCGbaMrR4Tt4-Oh6m7`.
- MASTER folder is not used for derived files.

## Identity and chronology

- Deterministic Job ID: `41daa4ca6e09d13e366c578b7c53ae31`.
- No prior terminal receipt/result for this Job ID was found.
- `lesson_number = 2`.
- Proposed `lesson_date = 2021-02-28`.
- Independent date evidence found: Zoom invitation for a lesson titled `Техника`, Zoom cloud-recording-ready message dated 2021-02-28, and the original file modified timestamp on 2021-02-28.
- The topic label from the invitation remains auxiliary source metadata until video content confirms it.

## Quality-first code promotion

The prior claim that quality-first R26 was already fully deployed was premature: preflight found the operational workflow still on r25.6 while improvements remained in `candidate/diana-video-analysis-v2`.

Corrective deployment completed:

- PR `#125` promoted the quality-first route.
- PR head before merge: `23fc5411b9064b51a7e650ac18fa1cfc4b0d1ae5`.
- Merge commit in `main`: `880d9b4cbfa678fadf8665bc208245b38bee5ff1`.
- Production workflow now requests `3.1-free-r25.7` and enables conservative local diarization.
- r25.7 explicitly installs the proven r25.6 media/ASR/semantic/source-integrity path before adding readiness-v2 and diarization.

## Regression evidence before merge

All required PR checks passed on final head:

- Diana Longitudinal Regression: run `32114938150` — SUCCESS.
- Bridge Video Production Evidence Contract: run `32114938076` — SUCCESS.
- Bridge Video r23 Selftest: run `32114938102` — SUCCESS.
- Bridge School Database CI: run `32114938133` — SUCCESS, including migration application, invariants, idempotence, checksum guard and registry verification on ephemeral PostgreSQL 18.

The evidence contract was updated rather than weakened: it now requires r25.7 to inherit r25.6 and verifies that the master canon-link producer remains unchanged.

## Remaining production blocker

- Production Neon did not contain `public.analysis_candidate` at preflight time.
- Forward migration `0049_analysis_candidate_staging.sql` is now in `main` and passed database CI.
- It creates only the non-authoritative candidate staging layer and grants no automatic authority escalation.
- A safe migration-preparation attempt through the Neon migration helper failed in that helper's SQL parser on an apostrophe inside a COMMENT string. No production SQL was executed and production Neon was not changed.

The video pipeline can technically preserve Drive artifacts and raw Evidence even when staging is unavailable, and staging can be backfilled without retranscribing the video. For a complete one-pass closed loop, the recommended state remains HOLD until migration 0049 is applied and read back.

## Cost Gate

- Paid AI/API calls during preflight: `0`.
- Heavy video analysis: not started.
- Paid cloud/billing fallback remains disabled in workflow configuration.
- Persistent extra video copy created for MASTER: one Drive-native copy, approximately 680 MB.
- No second working-video archive was created.

## Launch decision

`DIANA_2_FULL_LOOP_READY = NO`

Reason: code and source preparation are ready, but the production candidate-staging migration is not yet applied. The next owner-authorized action is production migration 0049, followed by schema/read-write/authority read-back. After that the job may be launched with lesson 2 date evidence and isolated result folders.
