# Video Learning Record v1 — staging contract

Дата: 2026-08-22

## Назначение

Video Learning Record (VLR) связывает доказанный фрагмент видеоурока с последующим методическим разбором, не превращая автоматически извлечённый материал в каноническое знание школы.

Пайплайн:

`source video -> transcript -> speaker/segment evidence -> learning interaction -> optional board/decision evidence -> optional DDS3 analysis -> methodology candidate -> teacher review -> canon/curriculum decision`

## Обязательные поля записи

- `record_id` — детерминированный идентификатор записи.
- `source_identity` — provider/source identity исходного видео.
- `job_id`, `algorithm_revision`.
- `evidence_refs` — ссылки на transcript segment / decision window / visual evidence.
- `time_range` — начало и конец эпизода, если доказаны.
- `speaker` — только доказанная/разрешённая атрибуция; иначе `UNKNOWN`.
- `interaction_status` — `COMPLETE` или `PARTIAL` согласно quality layer.
- `board_evidence_status` — `NONE`, `PARTIAL`, `VERIFIED_FULL_BOARD`.
- `dds3_status` — `NOT_APPLICABLE`, `PENDING`, `EVIDENCE_READY`, `BLOCKED`.
- `methodology_candidate` — извлечённый тезис/ошибка/объяснение как кандидат на review.
- `authority_status` — всегда начинается с `STAGING_REVIEW_REQUIRED`.
- `provenance` — версии, hashes/locators производных артефактов и evidence IDs.

## Authority Gate

Автоматическая обработка НЕ имеет права переводить VLR напрямую в canon, curriculum или student profile. Допустимый автоматический результат — только `STAGING_REVIEW_REQUIRED`.

Повышение возможно только после teacher/owner review с сохранённым evidence reference. Отсутствие решения преподавателя не является технической ошибкой и не должно обходиться автоматикой.

## Evidence Gate

1. Ненадёжные ASR segments не используются для derived methodology evidence.
2. `METHODOLOGY_READY` означает достаточность доказанных эпизодов для анализа, а не полноту урока.
3. `VERIFIED_FULL_BOARD` требует 52 уникальные доказанные карты. Скрытые руки не достраиваются дополнением колоды.
4. DDS3 запускается только когда позиция/раздача имеет достаточное доказательное представление. DDS-результат хранится отдельно от преподавательской интерпретации.
5. Агрегированные метрики не заменяют provenance конкретного learning interaction.

## Типы methodology candidate

- `STUDENT_ERROR_CANDIDATE`
- `STUDENT_SUCCESS_CANDIDATE`
- `TEACHER_EXPLANATION_CANDIDATE`
- `EXERCISE_CANDIDATE`
- `COURSE_GAP_CANDIDATE`
- `CANON_CANDIDATE`

Название типа не означает подтверждения содержания или разрешения на публикацию.

## Минимальный критерий готовности v1

VLR считается технически сформированным, если есть source identity, evidence refs, interaction status, provenance и authority=`STAGING_REVIEW_REQUIRED`. Для PARTIAL interaction допускается создание записи, но запрещено выдавать её за полную реконструкцию эпизода.

## Совместимость с текущим Video 3.1

Контракт использует уже существующие границы: SourceIdentity/provenance, transcript decision windows, COMPLETE/PARTIAL learning interactions, quality/readiness semantics и fail-closed board reconstruction. Он не требует тяжёлого повторного ASR и не изменяет текущие canon/curriculum/profile gates.

## Следующий инженерный срез

1. Добавить machine-readable schema VLR v1.
2. Построить адаптер из текущего quality payload в VLR staging records.
3. Добавить regression tests: unreliable ASR exclusion, PARTIAL preservation, no authority escalation, exact evidence refs.
4. Только после прохождения CI подключать optional DDS3 enrichment для `VERIFIED_FULL_BOARD`/доказанной позиции.
