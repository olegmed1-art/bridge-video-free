# Закрытие контуров улучшения — 20.08.2026

Этот документ фиксирует фактический статус процессов после повторной проверки GitHub, Google Drive и production Neon. Он не меняет систему торговли, методику преподавания или канон школы.

## Закрыто / подтверждено

### BBO-100
- Публичный корпус зафиксирован: 100/100.
- TRAIN: 80; HOLDOUT: 20.
- Исходный исследовательский запуск: GitHub Actions `32259436953`, SUCCESS.
- Принято 105 уникальных сдач из 230 публичных candidate URL; `no_meaningful_play`: 24; HTTP 429: 0.
- Manifest/digest evidence сохранён в `dds_training/BBO100_CORPUS_EVIDENCE.json`.
- Исследовательский PR #163 закрыт без merge, как и требовал его контракт.
- Архив долговременно сохранён в private Drive: folder `1xfM2Ee3wJ3h8GH7h6gkAuHOlouiPU4I9`, archive file `1JM4VzdXJnFwkTKkPJ4XK_5NtDM9jYq8j`.
- HOLDOUT-20 пройден по blind-first Evidence Gate: до открытия DDS/скрытых рук отдельным коммитом `2426531ab1cc02bf94ee6a4178a56a6874abb1aa` зафиксированы 20 карточных решений GPT-5.6 Sol; task-packet gate — `5a6c0f015effc7a9a916591f52da100110fa7beb`.
- Post-blind run `32392200131` завершён: 17/20 решений имеют нулевой DD-regret (85%), средний regret 0.20 взятки, медиана 0, максимум 2; declarer continuation 7/7, defense continuation 6/6, opening lead 4/7.
- Safe machine evidence сохранён в private Drive `1Kllgriz9SFUyJX3ew1TmEfN59QkXRL_J`, отчёт — `1sAOO-1gaFaNgsbgfNIrvHigOChkrabco`.
- PR #207 прошёл общий fast architecture/test gate и DDS main preparation gate и смержен в `main` merge commit `b50a397888029974a464557f54b69ac956b3d5fb`.
- Одноразовый evaluator/workflow удалены из стабильного `main` после успешного evidence-run; их точный provenance сохранён в истории ветки/PR и в `dds_training/BBO100_BLIND_EVALUATION_EVIDENCE.json`.
- DDS здесь остаётся double-dummy oracle; N=20 — диагностическое transfer evidence, а не основание автоматически объявлять навык стабильным или менять канон/методику/Student Profile.

### DDS Stage 2C
- Старый preparation-only PR #138 закрыт как superseded.
- Доказательная цепочка уже продвинута через Stage 2C.2 (#159), Stage 2C.3 (#160) и Stage 2C.4 (#198).
- Validation/sealed gates не открываются автоматически; skill/methodology promotion не разрешён.

### META correction / regression infrastructure
- PR #156 интегрирован в `main` как fail-closed Shadow infrastructure.
- Зарегистрированы autonomous learning loops и `CORRECTION_COMPILER`.
- При недостаточной evidence/attribution/position binding правильное поведение остаётся `NO_INPUT / NO_CHANGE`; синтетические corrections запрещены.
- Canon, protected methodology и high-impact promotion требуют явного teacher approval.

### Checkpoint / resume / recovery evidence
- PR #105 уже содержит generic guarded checkpoint/resume history, recovery verification evidence и correction→regression contracts.
- Длинные analysis/ingestion/projection runs имеют инфраструктурную основу для возобновления без переписывания доказательств.

### Longitudinal learning interactions v4.1
- PR #203 прошёл deterministic CI и private completed-master Field Evidence Gate и смержен.
- Field gate: `FIELD_VALIDATION_PASS`, `diana-quality-v4.1`.
- Найдено 2 complete decision windows; correctness остаётся отдельно evidence-gated.
- Source untouched; raw ASR not mutated; heavy reprocessing = false; paid AI/cloud = 0.
- Canon/curriculum/methodology/person-specific/student-profile production writes остаются DENY; destination = STAGING_ONLY.

### Production Neon — актуализация старого статуса
- Production migration registry содержит 56 записей, включая последовательность 0001–0055 и внешний audited migration `2026-08-20-ai-decision-layer-v1`.
- Старый статус «production только 0001–0019» больше не действителен.

### Neon recovery smoke
20.08.2026 создана временная ветка `restore-smoke-20260820` (`br-orange-waterfall-b1twp6lh`) из production parent, выполнена read-only проверка и ветка удалена.

Совпало между production и branch snapshot:
- migration count: 56;
- public tables: 192;
- decisions: 610;
- evidence: 2044;
- evidence links: 4116;
- corrections/regression cases/regression executions: 0/0/0;
- public column schema fingerprint: `16c1544918217e0cc100a318eadd159c`.

Это подтверждает текущую Neon branch-recovery/clone integrity. Это не заменяет независимый внешний `pg_dump`/off-platform disaster-recovery backup, если такой отдельный уровень понадобится.

### Zoom → Drive / VTT
По действующему Drive-алгоритму:
- Server-to-Server OAuth работает;
- `syncZoomRecordings` работает по расписанию;
- transfer поддерживает resumable chunks и продолжение с сохранённого байта;
- готовый Zoom VTT имеет приоритет;
- при отсутствии VTT в `recording_files` предусмотрен transcript endpoint;
- первая запись >200 MB была успешно перенесена.

Следовательно, перенос/VTT/resume — не открытый дефект. Остаётся отдельная улучшательная задача классификации записей персонального зала, если нужна более точная маршрутизация по курсам.

### Канонические знания / мировой опыт
Drive-слой `LEARNING INTELLIGENCE — Реестр активов знаний v1.4` + `MATERIAL-RATING v2.16` уже задаёт provenance, M0/M1/M2 verification, compatibility/evidence classes и запрет автоматического импорта чужой системы в канон. Это operational review infrastructure; пополнение источников — постоянный процесс, а не незавершённая миграция.

### Drive ACL — рекурсивный read-only аудит
- Новый permission-level recursive audit завершён run `32415122049` со статусом SUCCESS; существующие Drive-объекты и разрешения не изменялись.
- Проверено 436 папок, 2690 файлов и 3124 объекта внутри корней `Школа спортивного бриджа` и `Записи Zoom`; shared-flag имеют 349 объектов.
- Обнаружено 63 `anyone`-объекта, все только `reader`; `anyone writer` = 0. Из них 31 разрешение прямое и 32 унаследованные.
- В дереве школы 61 public-link объект сосредоточен в `Курс для начинающих`; в Zoom 2 public-link объекта относятся к папке занятия 03.08.2026 и видео внутри неё.
- В дереве школы есть 321 объект с именованными collaborator permissions, 185 различных комбинаций получатель+роль и 138 объектов с внешним владельцем; это требует контекстного owner-review и не считается автоматически ошибкой.
- Канонический подробный отчёт хранится приватно в Drive `1eWsJxOuFikl_Gik1wU1MhpF06V3SoaLj`, safe summary — `1dHslxvaFcHOgwEFSxM1t1L3yrgfAFhg6`; публично адресаты не раскрываются.
- Старый статус «permission-level recursive audit недоступен» больше не действителен. Фактическая ревизия прав теперь технически подготовлена, но изменение ACL остаётся отдельным owner gate.

## Корректно заблокировано — не обходить

### Neon identity onboarding 0100–0104
Миграции подготовлены и проверены в controlled branch, но production execution требует отдельной owner-authorized границы `MIGRATE`. До явного подтверждения владельца не применять напрямую через SQL и не обходить workflow.

### Person-specific Student Profile / production skill state
v4.1 Field Gate намеренно сохраняет production writes = DENY. Продвижение разрешается только при достаточной identity/evidence binding и соответствующем authority gate.

### Pons / DDS adapters в Hybrid Cloud Decision Worker
BEN adapter реализован и fail-closed отделяет policy/teacher evidence от настоящих simulation metrics. В текущем worker `PONS_API_URL` зарезервирован, но Pons adapter не реализован; отдельного verified DDS service API contract для этого worker не обнаружено. Не придумывать endpoint/schema: эти адаптеры остаются `BLOCKED_BY_VERIFIED_INTERFACE`, пока не появится проверенный интерфейс.

### Курс новичков — занятия 15–16
По авторскому конспекту 1–13 = Done, 14–15 = In progress, 16 = Not Started. Инженерный контур не должен дописывать содержание занятия 15 («Игра в защите») или 16 без авторского материала преподавателя. Это методический input gate, а не ошибка автоматизации.

### Drive ACL remediation
Рекурсивный аудит завершён, однако фактическое изменение прав требует отдельного решения владельца. Не отзывать автоматически public-link или named collaborator права и не менять ownership. Безопасная ревизия должна начинаться с 31 прямого `anyone reader` источника, потому что часть 32 унаследованных public-link объектов закроется вместе с родительским разрешением. Именованные writer/reader и external-owner случаи требуют проверки назначения доступа по приватному детальному отчёту.

## Роботы как лабораторные эксперты
Текущий безопасный статус:
- BEN — подключаемый teacher/policy источник и источник simulation metrics только когда они явно возвращены движком;
- DDS — подтверждён как отдельное локальное вычислительное ядро для TRAIN/evidence контуров, но не выдумывается как сетевой service API для Hybrid Cloud worker;
- Pons — ждать проверенного интерфейса;
- никакой робот не получает authority менять авторскую систему торговли или методику школы.

## Итог
Инженерные процессы, которые можно было закрыть без нарушения evidence/owner/teacher gates, закрыты или подтверждены как уже operational. BBO-100, включая HOLDOUT-20 blind Evidence Gate, закрыт. Drive ACL permission-level audit также закрыт как read-only процесс; изменение прав остаётся отдельным owner gate. Остальные пункты — явные authority/input/interface gates, а не процессы, которые следует «добивать» обходом защиты.
