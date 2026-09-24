# Video 3.1-test-r5 — field evidence «Диана 14»

Дата: 2026-08-29  
Issue: #840  
Governance: `ASSURED`, `SHADOW_ONLY`

## Решение

Итог field-проверки диаризации и ролей: **INCONCLUSIVE**.

Это допустимый fail-closed результат. Он не разрешает `MAPPED`, production
activation, canonical promotion или автоматический запуск следующего видео.

## Первичное evidence

- GitHub Actions run: `33260549844`
- runtime commit: `3c1434c952b120045da5456bbc18dddd47c0368f`
- artifact: `diana14-video31-test-r5-open-set-field-evidence`
- artifact digest: `sha256:484c0eb836770e5897d8c4d9f36e8c121e7305d40129121a1ade4dd0caa22978`
- exact source: `Диана 14.mp4`
- source size: `655885284` bytes
- source fingerprint: `51ac509e6084b92619f469f49af604efab0e76089e428475c6600c536cd47560`
- transcript: `725` segments, `8876` words; transcript QC PASS

## Независимый пересчёт speaker-role verifier

- total segments: `725`
- total speech duration: `6798.44 s`
- labeled segments: `0`
- labeled speech duration: `0 s`
- segment coverage: `0.0`
- speech-duration coverage: `0.0`
- minimum required coverage: `0.8` for both metrics
- observed speaker count: `0`
- real speaker count: `UNPROVED`
- unmapped share by segments: `1.0`
- role map: `{}`

Blockers:

- `SEGMENT_COVERAGE_BELOW_0_80`
- `DURATION_COVERAGE_BELOW_0_80`
- `REAL_SPEAKER_COUNT_AND_MIXING_UNPROVED`
- `SPEAKER_COLLAPSE`
- `PRODUCER_STATUS_NOT_MAPPED`
- `PRODUCER_ROLE_EVIDENCE_UNAVAILABLE`
- `TEACHER_STUDENT_ROLES_NOT_SEPARATELY_PROVED`

## Oracle inventory cross-check

Read-only pinned-SSH run `33271444552` inspected the 50 most recent result
directories and found 12 completed transcript artifacts. All 12 have zero
speaker-labeled segments; none has open-set speaker-count proof or supported
teacher/student attribution. No additional server artifact can upgrade the
«Диана 14» result.

## Assurance and boundaries

- adversarial and contract suite: 67 PASS
- wider Universal Video / Bridge regression suite: 299 PASS
- production evidence, ASR isolation, PDF, r29 identity and compile gates: PASS
- no SCHOOL CANON change
- no production or canonical promotion from this field result
- no new real-video run in the Oracle inventory step
- next-video auto-start remains forbidden

The production implementation is safe to merge as an evidence-preserving
fail-closed gate. Issue #840's field outcome remains **INCONCLUSIVE**, not PASS.
