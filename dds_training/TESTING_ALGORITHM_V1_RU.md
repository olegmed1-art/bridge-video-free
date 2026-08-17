# Алгоритм тестирования DDS Learning v1.1

## 1. Назначение

Документ определяет, как проверяется программная и методическая часть
`dds-learning-v2.3` до любого массового DDS-этапа.

Цель — не получить зелёный значок любой ценой, а доказать одновременно:

- корректность бриджевой логики;
- корректность интеграции с локальным DDS3;
- отсутствие утечки между семействами сдач, TRAIN и holdout;
- воспроизводимость корпуса и результатов;
- сохранность исходного кода и исторических фактов;
- невозможность случайного или скрытого массового запуска;
- проверяемость цепочки поставки solver-а и CI-компонентов;
- полноту тестового покрытия без скрытых исключений.

Тестовый контур не меняет систему торговли школы и не запускает массовое
обучение.

## 2. Иерархия доказательств

### 2.1. Чистая программная логика

Без DDS проверяются:

- PBN и частичные позиции;
- принадлежность карт, очередность хода и follow-suit;
- победитель взятки;
- lineage, family isolation и cross-fit folds;
- restartable shards;
- калибровка и abstention;
- provenance и append-only ограничения;
- правила promotion навыков;
- readiness и stage gates;
- data-bound authorization;
- блокировка direct evaluate до открытия SQLite;
- формирование английского и русского отчётов.

### 2.2. Реальный локальный DDS

После установки закреплённого DDS3 проверяются:

- фактическая граница пакетной DD-таблицы;
- повторное использование `SolverContext`;
- пошаговая DD-траектория;
- нормализация значения на одну шкалу;
- DD-regret выбранной карты;
- равнооптимальные альтернативы;
- continuation-решения разыгрывающего и защиты;
- инварианты поворота стола и перестановки мастей.

Mock-ответ DDS не заменяет этот уровень.

### 2.3. Независимый решатель

DDS3 сравнивается с отдельно скомпилированным bridge-solver на фиксированной
выборке. Сравниваются все 20 ячеек DD-таблицы каждой сдачи. Независимый solver
закреплён на точном commit.

### 2.4. Репетиция этапа без массового расчёта

CI может:

- создать 10 000 сдач;
- расширить их до 30 000;
- построить folds и shards;
- выполнить короткий технический full-play preflight;
- сформировать readiness report.

После репетиции в базе должно оставаться **0 массовых DDS-результатов**.
Validation, sealed test и утверждение устойчивых навыков должны оставаться
закрытыми до появления соответствующих доказательств.

## 3. Канонический реестр

Единственным реестром является `test_matrix.json`.

Для каждого теста фиксируются:

- уникальный `id`;
- файл;
- suite;
- timeout;
- один или несколько `PYTHONHASHSEED`;
- проверяемый контракт.

Новый `*_selftest.py`, не включённый в реестр, блокирует CI. Производственный
Python-модуль без связи с тестом также блокирует CI.

Текущий waiver budget равен **нулю**. Добавление waiver требует отдельного
изменения тестовой архитектуры и не может происходить незаметно.

## 4. Изоляция выполнения

Каждый тест запускается отдельным subprocess с:

- отдельными `HOME` и `TMP`;
- фиксированным `PYTHONHASHSEED`;
- UTC;
- фиксированной locale;
- `PYTHONDONTWRITEBYTECODE=1`;
- индивидуальным timeout;
- захватом stdout, stderr и return code.

Один сбой не скрывает результаты остальных тестов. Runner собирает полный список
ошибок, если явно не включён `--fail-fast`.

## 5. Запрет оптимизированного Python

Исторические self-tests используют обычные `assert`. Поэтому `python -O` и
`python -OO` запрещены: runner завершает работу до запуска тестов, если
`__debug__` выключен.

## 6. Детерминизм

Генераторы корпуса, folds, shards и выборки используют фиксированные seeds и
проверяемые SHA-256.

Для чувствительных компонентов применяются несколько фиксированных hash seeds.
Так выявляется зависимость от порядка set/dict без невоспроизводимой случайности.

## 7. Защита запуска

Наличие `DDS_TRAINING_CONFIRM=YES` и `--start` недостаточно.

Массовое выполнение требует:

- `dds-run-authorization-v1`;
- отдельного plaintext token во время запуска;
- совпадения token SHA-256;
- срока действия;
- совпадения версии алгоритма, stage и полного набора splits;
- совпадения corpus SHA-256 и prediction SHA-256;
- разрешения sealed test, если он открывается;
- ограничения максимального числа задач.

Manifest содержит `automatic_issuance_allowed: false`. Команда
`python run_stage.py evaluate ...` без контекста блокируется **до** создания
SQLite и файлов в рабочем каталоге. Solver entrypoints повторяют проверку.

В репозитории не хранится workflow массового запуска. Тестовые workflow не имеют
права вызывать `authorized_run_stage.py`.

## 8. Проверяемая цепочка поставки

`bootstrap_linux.sh` закрепляет:

- DDS source commit;
- Bazelisk version и SHA-256;
- Bazel version;
- версии pip, wheel, setuptools и packaging.

Все GitHub Actions закрепляются на полных 40-символьных commit SHA.

Кэш DDS считается пригодным только после установки wheel и полного preflight.
Пустой или повреждённый wheel удаляется. Heavy workflow обязан выполнить второй
bootstrap и доказать восстановление из тёплого проверенного wheel-cache.

## 9. Защита исходного дерева

Проверяются одновременно:

- изменения отслеживаемых файлов;
- новые неотслеживаемые файлы любого расширения;
- неожиданные игнорируемые файлы;
- чистый `git status --porcelain=v1 --untracked-files=all`.

Разрешены только явно перечисленные runtime-префиксы (`.venv`, `.build`,
`.wheel-cache`, `work` и другие служебные каталоги). Неожиданный `.pbn`,
`.sqlite3`, бинарный файл или файл вне разрешённого runtime-каталога проваливает
проверку.

## 10. Workflow-политика

DDS-workflow сведены к минимальному набору:

- быстрый manifest-driven контракт;
- тяжёлая интеграция с локальным solver-ом;
- существующий независимый golden smoke.

Они:

- работают с `contents: read`;
- не сохраняют checkout credentials;
- не делают commit/push;
- не изменяют канонический алгоритм;
- не содержат статический approval token;
- запускаются на pull request;
- сохраняют machine-readable artifacts.

Новый workflow с исполняемым mass-entry допускается только как manual-only
архитектурное изменение после отдельного решения владельца.

## 11. Fast и DDS suites

### Fast

Проверяет чистую логику, SQLite-контракты, immutable memory, отчёты, lineage,
калибровку, shards, авторизацию и архитектуру без сборки DDS.

### DDS

После pinned bootstrap проверяет preflight, full-play trajectory,
continuation-regret, инварианты преобразований и независимый solver.

Разделение уменьшает время до первой полезной ошибки и не допускает
skip-cascade после раннего сбоя.

## 12. Отчётность

Runner создаёт `dds-test-report-v1` с:

- версией Python;
- SHA-256 test manifest;
- suites и test IDs;
- hash seed;
- длительностью;
- timeout;
- return code;
- stdout/stderr;
- обнаруженными изменениями исходников;
- итоговыми счётчиками.

Heavy artifact дополнительно включает:

- bootstrap manifest;
- cold/warm bootstrap logs;
- независимый cross-check;
- source-integrity report;
- Stage 2 readiness report.

Зелёный workflow без доступного отчёта не является достаточным доказательством.

## 13. Обязательные негативные проверки

Тесты должны доказывать блокировку:

- незаконной карты и revoke;
- неверного следующего игрока или стороны решения;
- orphan self-test и duplicate test ID;
- модуля без покрытия;
- зависшего теста;
- tracked/untracked/ignored residue;
- holdout leakage;
- sealed test без отдельного разрешения;
- смешивания sealed test с другими splits;
- просроченной или не совпадающей authorization;
- изменённого prediction/corpus hash;
- превышения `max_tasks`;
- прямой команды evaluate до любой записи в SQLite;
- прямого solver-вызова в training mode без authorization;
- повреждённого wheel-cache.

Runner self-test намеренно создаёт pass, fail, timeout и mutation и проверяет их
классификацию.

## 14. Критерии приёмки изменения

Изменение принимается, когда:

1. manifest полон;
2. coverage contract выполнен без waivers;
3. fast suite зелёная;
4. при зависимости от DDS зелёная DDS suite;
5. independent cross-check не обнаружил расхождений;
6. cold bootstrap и warm bootstrap доказаны;
7. repository source-integrity чист;
8. machine-readable artifacts сохранены;
9. массовых DDS-результатов не появилось;
10. readiness/holdout/skill-claim находятся в ожидаемом состоянии;
11. изменение прошло pull request на актуальной базе `main`;
12. после merge проверки повторены на merge commit.

## 15. Канонические команды

Проверка реестра:

```bash
python test_runner.py --manifest test_matrix.json --check-only
```

Fast suite:

```bash
python test_runner.py \
  --manifest test_matrix.json \
  --suite fast \
  --report /tmp/dds-test-report-fast.json
```

DDS suite после pinned bootstrap:

```bash
source .venv/bin/activate
python test_runner.py \
  --manifest test_matrix.json \
  --suite dds \
  --report /tmp/dds-test-report-dds.json
```

Один тест:

```bash
python test_runner.py --manifest test_matrix.json --test run-stage-entry
```

## 16. Граница доказательств

Зелёные тесты доказывают проверенные программные и методические контракты. Они не
доказывают автоматически приобретённый бриджевый навык.

Для `confirmed/stable` нужны независимые transfer, контрпримеры, regression
streak и реальные турнирные данные. Тестовый алгоритм не позволяет подменить эти
доказательства техническим успехом.
