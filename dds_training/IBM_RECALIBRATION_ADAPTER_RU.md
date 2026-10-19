# Подготовка recalibration через OracleLight → IBM

Дата: 2026-10-19. База: `bd6eb76ae0d4e9ccc858c2761521acf1876b892b`.
Governance: ASSURED / I2. Принятая Learning v2.3 остаётся неизменной;
v2.5 Stage2B — candidate/testing.

`ibm_recalibration_prepare.py` реализует **offline preparation adapter**:
формирование точного запроса и read-only проверку отдельного TRAIN-only входа.
Команды dispatch и сетевого транспорта в этой ревизии нет. Адаптер не вызывает
fit, DDS, SSH, IBM API, старые launch wrappers и полную Stage2B preparation.
Даже успешная проверка всегда сохраняет `dispatch_allowed=false`.

Окно IBM закрыто до отдельного события координатора об окончании экспорта
кадров Diana20 и готовности IBM. Текущий экспорт, службы, ключи и права не
меняются. Тяжёлый GitHub compute остаётся запрещённым.

## Результат read-only сверки сохранённых фактов

Проверены репозиторий, метаданные Drive и маленький manifest; строки корпуса,
смешанная SQLite-база и validation/sealed payload не читались.

| Объект | Проверенное состояние / ограничение |
| --- | --- |
| TRAIN ZIP | `dds-main-train-progress-32060398288-a1.zip`, 240301101 bytes, объявленный SHA-256 `c46cda2fb127371cbc1ab836c74580b25f0a0bb8ab358dfaab97cf591dd8a246` |
| Drive manifest | ID `1xkcEclEi7PCWIBXFhM_stUDyeXnr0zZS`, 1063 bytes; доступен, содержит тот же размер и digest архива |
| TRAIN part001 / part002 / part003 | Метаданные доступны; размеры 83886080 / 83886080 / 72528941 совпадают с `artifact_locations/dds-main-train-32060398288.json`. Байты частей не скачивались, SHA-256 частей повторно не проверялся |
| Историческая база | В repo записан SHA-256 `7818b5dab8274c45c494e48ecd94b0979cd9461293e9862fe70b28398075f44d`; база содержит TRAIN и исторические holdout, поэтому не является входом нового scanner |
| Stage2B artifact | API для `9318545935` вернул 404. Это недоступность по текущему API, а не доказательство удаления всех копий |
| Compact OOF archive | Repo фиксирует `f4147f9f060c703b1b9e9d629e26ab65bf6454b3da9f68ce6e4480b3a4a43643`. Отдельного Drive locator нет; поиск по точному префиксу имени/номеру run не нашёл копию среди доступных результатов |
| OOF member | `stage2b-v25/oof_predictions_v03_refined.jsonl`; отдельный digest не включён в старый CURRENT_STAGE_MANIFEST |
| Старый writer | Не сохранял верхнеуровневые `split` и `out_of_fold`. Исправление PR2191 относится к новым экспортам; исторические маркеры не восстанавливаются догадкой |
| Семейства ранее использованного sealed | PR945 остаётся open; нормализованный manifest исключения ещё не принят |

Историческая квитанция `STAGE2B_V25_PREPARATION_EVIDENCE.json` сообщает 42000
TRAIN/OOF строк, пять folds и overlap 0. Она не заменяет проверку конкретных
строк и lineage каждой fold-модели. `candidate_model_v03_refined.json`, обученный
на всех TRAIN-строках, не является доказательством held-out обучения.

Текущий вывод: **пригодность реального входа для recalibration не подтверждена**.
Повторный DDS потенциально не нужен, но полнота score maps и OOF provenance
должны быть доказаны до fit. `source_prediction` нельзя подставлять вместо
кандидатного `prediction`. При недостатке provenance нельзя незаметно заменить
recalibration новым predictor fit или запуском полного Stage2B pipeline.

## Точные входы адаптера

Функции: `prepare_plan(spec)` и `check_train_artifact(spec, local_root)`.
CLI выводит только JSON в stdout; файлов результатов и серверных действий нет:

```text
python dds_training/ibm_recalibration_prepare.py --spec SPEC.json --plan-only
python dds_training/ibm_recalibration_prepare.py --spec SPEC.json --root TRAIN_ONLY_DIRECTORY
```

`SPEC.json` имеет schema `dds-ibm-recalibration-input-v1` и точные поля:

- `operation=train_recalibration_no_dds`, `scope=main_train`, `source_sha`;
- `runtime_profile=cpython-3.14-stdlib-v1` и SHA-256 трёх файлов:
  `ibm_recalibration_prepare.py`, `stage2b_v24.py`, `stage2b_v25.py`;
- `task_count`, `family_count`, `fold_count`, `excluded_family_count`;
- `source_archive_sha256`, `source_database_sha256`,
  `source_prediction_sha256`, `producer_sha`;
- `solver={version: 3.0.0, commit: 37c8a79f4c67c55d1a309ccb66dd00cb58af464a}`;
- `files`: ровно четыре роли ниже. Каждая задаёт простой filename без каталогов,
  точное число `bytes` и `sha256`. Хеш spec связывает весь запрос.

| Роль | Формат и обязательное содержимое |
| --- | --- |
| `tasks` | JSONL: task_id, family_id, task_type, strain 0–4, split=train, heldout_fold, legal_cards. Для opening_lead — все 13 легальных карт; для contract_tricks — пустой список. Без PBN и ответов |
| `oof` | JSONL: те же identity/type/strain/split/fold, out_of_fold=true, prediction, result. Без task/PBN/source_prediction. prediction locked=true, out_of_fold=true, raw_confidence_probability, model_evidence_count, model_backoff_level и выбранная card или tricks |
| `excluded_families` | JSON schema `dds-used-sealed-family-ids-v1`: family_ids ранее использованного sealed scope, только IDs, без ответов. Ожидаемое количество привязано к spec |
| `producer` | JSON schema `dds-oof-producer-lineage-v1`: producer_sha, oof_sha256, tasks_sha256, solver, три source digest как в spec; для каждого fold — model_sha256, полные training_families и heldout_families |

Producer manifest обязан связывать конкретные сохранённые модели и факты.
Проверяется полное разбиение семейств: heldout не пересекаются между folds,
training каждого fold точно равен общему множеству без его heldout. Все
выбранные семейства находятся в правильном heldout, исключённые sealed families
отсутствуют и среди выбранных, и во всём producer universe.

Это **проверка согласованности**, а не аутентификация производителя. Хеш можно
пересчитать для поддельного JSON. Поэтому вывод всегда содержит
`external_provenance_verified=false`; доверенный внешний admission должен
сопоставить digests и producer lineage с первичными неизменными квитанциями.

Исторический основной TRAIN — 28000 задач / 14000 семейств / пять folds,
prediction SHA-256 `ae946bec73f12bead7ebd6e54947bb6f2d2268798cf08b7b468495f943befe2b`.
Точный выбранный набор задаётся manifest, не количеством строк. Технический
предел scanner 42000 строк не расширяет этот scope и не разрешает полный
исторический TRAIN автоматически.

## Проверки и лимиты

Scanner использует один descriptor для хеширования и чтения каждого входа,
проверяет неизменность identity/размера/mtime и дополнительно ctime на POSIX.
Symlink/reparse point и файлы не обычного типа отклоняются. На Windows stat и
fstat могут различаться семантикой ctime; размер, mtime, identity и digest
сохраняются обязательными. Все descriptors закрываются и при раннем отказе.

Ограничения проверки: один процесс, 60 секунд monotonic deadline, максимум
256 MiB всех входов, 64 KiB на JSONL-строку, 16 MiB на metadata файл, 42000
строк. Нет скрытого sampling: несовпадение точных IDs, количества или хеша —
отказ. Дубликаты JSON keys, NaN/Infinity, неизвестные поля projection,
противоречия provenance, пропущенные задачи и неполные scores отклоняются.
Opening-lead scores должны покрывать все 13 легальных карт, содержать целые
взятки 0–13 и согласованный максимум. Старый source-card dd_regret не используется.

Запрос будущего IBM исполнения: **1 vCPU, MemoryMax=2 GiB, 900 секунд**, до
512 MiB новых выходов, heartbeat каждые 30 секунд. Это запрошенный envelope,
не подтверждение cgroup или свободных ресурсов. Реальный runtime/executor ещё
должен подтвердить эти ограничения; scanner не объявляет их установленными.

Коды результатов:

- `PREPARED_EXECUTION_BLOCKED`: корректный план, факты не прочитаны;
- `CONSISTENT_AWAITING_EXTERNAL_ADMISSION`: все перечисленные локальные проверки
  прошли; fit/dispatch всё равно запрещены;
- `BLOCKED`: компактный reason code без строк, сделок или ответов.

Exit 0 означает успешную подготовку/проверку, **не разрешение запуска**.

## Критерии приёмки и следующее событие

Software готов после малых тестов, независимого I2-ревью и CI конкретного head.
Нужны негативные проверки forged approval, подмены файлов/IDs, утечки семейств,
плохого JSON, неполных scores и отсутствующего provenance. Результат не должен
менять входы, вызывать fit/DDS/сеть или выводить payload.

Operational readiness остаётся false до всех следующих свидетельств:

1. Координатор через доверенный канал подтвердил завершение экспорта Diana20 и
   конкретное окно на существующем IBM; его нельзя вывести из QUIET host probe.
2. Внешне проверены source/input/producer digests, исключение использованных
   sealed families и точный разрешённый TRAIN набор.
3. Принят существующими владельцами фиксированный recalibration executor:
   доказаны 1 vCPU / 2 GiB / 900 s, output limit, независимый deadline и остановка
   только своей задачи. Существующий IBM controller на 600 s не расширяется молча.
4. Admission связан с instance, окном, ресурсами, source/input digests и nonce;
   однократность обеспечивается защищённым общим журналом. Обычный hash receipt
   из launch_authorization и переданный actor/approved=true не являются подписью.

Нынешняя ревизия намеренно не принимает coordinator receipt как параметр и не
содержит dispatch. Событие координатора разрешит перейти к отдельной приёмке
исполнения; оно не превратит этот adapter в произвольный shell.

Rollback: revert новых offline файлов и их test-mapping. Данные и серверы не
изменяются, административная блокировка GitHub preparation сохраняется.
DD-результаты описывают полную фиксированную раздачу; проверка этой цепочки не
доказывает качество человеческого решения при неполной информации или навык.
