# DDS3 / DDS Learning: сверка и минимальная приёмка, 2026-10-19

Исходный main: `45a129f13f54923bbdd418e721731326675279d6` (новее исходной
сверки `36edbdf609`). Scope: код и опубликованные документы, без чтения
построчных holdout-ответов, запуска DDS, реального корпуса или изменения служб.
Governance: ASSURED для корректности алгоритма; production promotion и
приобретение навыка в эту приёмку не входят.

## Версии и граница смысла

- [DDS3 Analysis Algorithm v2.0](../bridge_school_api/dds3/ALGORITHM_V2.md)
  описывает математический full-deal/position контур; solver — DDS3 v3.0.0.
- [DDS Learning v2.3](ALGORITHM_DDS_LEARNING_V2_3_RU.md) — принятая базовая
  версия, закреплённая в `config.py` и README. Её
  [security addendum](ALGORITHM_DDS_LEARNING_V2_3_SECURITY_ADDENDUM_RU.md)
  сохраняет отдельные одноразовые допуски для вычислительных scopes.
- [v2.5 Stage2B](ALGORITHM_DDS_LEARNING_V2_5_STAGE2B_RU.md) — candidate/testing,
  использующий подготовку и общие функции v2.4; predictor v0.3 — отдельная ось
  версий, не solver и не принятый алгоритм школы.
- Double-dummy regret означает потерю при знании всех рук. Он не доказывает
  ошибку человека при неполной информации. `human_view.py` создаёт отдельный
  single-dummy вид: своя рука, открытый стол, публичная торговля и сыгранные
  карты. Факт DDS сам по себе не становится правилом торговли или школьным каноном.

## Точный gap-list

| Область | Принятая v2.3 | Кандидат v2.5 / оставшийся пробел |
|---|---|---|
| Статус | Базовая версия алгоритма | Candidate/testing; запись данных или merge кода не означает принятия |
| Legal line и DD trajectory | `playline.py`, `dds_play.py`, prefix solve и равнооптимальные ходы | Используются подготовленные линии; подготовка curriculum не доказывает качество card-level решений |
| Family isolation | `root_deal_id`, folds, shards; TRAIN отдельно от holdout | `load_train_facts` фильтрует TRAIN, `prepare_oof_candidate` проверяет пересечение семейств; wrapper `recalibrate_oof_rows` сам ставит `out_of_fold=True` и отдельно требует будущего ужесточения входного контракта |
| Raw confidence | OOF calibration/abstention | Support-aware prior 0.18 для неподдержанного contract fallback; это вход к калибратору, не доказанная вероятность |
| Метка успеха первого хода | Нельзя приписывать чужому решению DDS-результат | **Подтверждённый дефект:** v2.4/v2.5 калибратор и diagnostics использовали source `dd_regret` после замены prediction на candidate; исправляется этим изменением |
| Model selection | Раздельный paired comparison по типу задачи | Исторические baseline-v0.1 и adaptive-v0.2 разделены; четыре NT/suit группы, practical threshold и bootstrap; automatic promotion=false |
| Calibration diagnostics | Нужна проверка вероятности и поддержки | Brier/ECE и accept-rate вычисляются на строках, использованных для fit калибратора; OOF базового predictor не делает эти diagnostics независимой проверкой самого калибратора |
| Continuations | Обе стороны должны присутствовать | Историческая подготовка: 650 line sources, ровно 1000 declarer + 1000 defense; это подготовка задач, не подтверждение навыка |
| Counterexamples | Слепой контрпример должен быть пройден | Исторически 441 **непроверенный кандидат**; число не означает 441 пройденный контрпример |
| Multi-contract | Blind prediction до DDS, family-safe provenance | Blueprint 500 × 20 = 10000 задач; reinforcement, не independent transfer; исторически predictions_locked=false |
| Расследования и память | Immutable facts, отдельно версии правил и навыков | Исторически 8140 structural, 0 card-level; очередь 25 групп с восстановленной мастью; structural resolution не равно стабильному навыку |
| Stable skill | Transfer, real-world, подтверждённое правило, counterexamples, regression streak текущей версии | Stage2B evidence явно stable_skill_claim=false; исправление кода этого не меняет |
| DDS3 вне Stage2B | Математический контракт и реальный solver | В v2.0 отдельно отмечены незавершённые raw-image extraction, >=50 real-screenshot corpus и общий tournament replay E2E; этот patch их не закрывает |

Числа Stage2B выше взяты из неизменяемой опубликованной сводки
[STAGE2B_V25_PREPARATION_EVIDENCE.json](STAGE2B_V25_PREPARATION_EVIDENCE.json),
датированной 2026-08-18, run `32121009819`. Это историческая сверка, не новый
вычислительный результат и не подтверждение текущей доступности архивов.

Есть более поздняя линия Stage2C. В
[Stage2C.6 validation report](STAGE2C6_VALIDATION_REPORT_RU.md) (2026-08-22,
run `32554317456`) записан PASS узкой suit-defense policy: overall defense
mean regret 0.474 → 0.463, optimal 66.9% → 67.4%, 2+ errors 89 → 83.
Declarer и NT-defense остаются baseline. Это не принятие Stage2B v2.5:
следующий gate — `owner_decision_on_new_sealed`, automatic promotion запрещён.
Открытый [PR #945](https://github.com/olegmed1-art/bridge-video-free/pull/945)
фиксирует отдельный BLOCKED-пакет из-за отсутствующих нормализованных family-ID
manifests для доказательства нового неиспользованного sealed scope. Статус
`closed` из отчёта Stage2B нельзя переносить на всю более позднюю историю.

## Минимальное исправление и критерии приёмки

Пример дефекта: source выбирает SA (score 5, dd_regret 0), candidate — S2
(score 3). До исправления кандидат мог получить calibrated_probability=1,
accept=true и 100% reported success при фактической потере 2 взяток.
Обратный случай ошибочно снижал confidence хорошего кандидата.

Исправление: общий `opening_lead_loss` вычисляет потерю **выбранной карты** по
сохранённой таблице `scores`. `row_success`, `row_loss`, обе версии comparison
и calibration diagnostics используют одну семантику. Отсутствующая карта,
пустые/невалидные scores, NaN/Infinity и значения вне 0..13 блокируют расчёт;
source `dd_regret` не служит запасным ответом. Равнооптимальные карты остаются
успехом. Continuation-контракты сохраняются отдельно.

Дополнительная находка review PR #2186 подтверждена независимо: усечённые
`scores={SA:3}` при `best_defense_tricks=5` давали loss=0. Теперь присутствующее
поле `best_defense_tricks` обязано быть конечным числом в 0..13 и совпадать
с максимумом таблицы. Противоречие в любую сторону и невалидное поле блокируют
все потребители; отсутствие поля остаётся совместимым. Это инвариант записи
`dds_engine.evaluate_opening_lead`, а не новая бриджевая трактовка.

Новые calibrator/diagnostics содержат
`opening_lead_outcome_policy=predicted-card-dds-score-v1`. Исторические DDS-факты
и опубликованный JSON не переписываются. Старые Stage2B confidence, Brier/ECE,
acceptance counts и accepted-success-rate нельзя использовать для приёмки
кандидата до нового разрешённого пересчёта. При полных корректных scores paired
loss comparisons уже были привязаны к карте; их старые значения этим дефектом
сами по себе не опровергнуты. Переносить вывод на отдельный Stage2C pipeline без
проверки его зависимостей также нельзя.

Ограниченная приёмка:

1. Regression падает на исходном main для candidate better/worse, затем проходит.
2. Проверены обе стороны ошибки, равнооптимальные карты, нормализация регистра,
   отсутствие/невалидность scores, неизменность входных фактов.
3. Независимый конечный oracle перебирает 384 случая (64 таблицы из трёх карт
   со scores 0..3, три выбора и две старые метки). Он ищет минимальную компенсацию,
   доминирующую все альтернативы, вместо использования формулы production helper.
   Это I2-проверка ограниченного score-контракта, не доказательство DDS solver.
4. Проверяются существующие Stage2B core/preparation self-tests, human-view,
   crossfit и test manifest. Независимый reviewer проверяет окончательный diff.
5. Принятие software patch требует успешного доступного CI; принятие v2.5,
   promotion и stable skill остаются отдельными открытыми решениями.

Локальная проверка (Python 3.12.14, Windows): 5/7 invocations завершились PASS
(v2.4 core, v2.5 core, human-view, crossfit при двух hash seeds). Оба preparation
self-test выполнили все функциональные assertions и вывели `ok: true` на 200
синтетических строках; их процесс завершился с ошибкой очистки временной SQLite
базы (`WinError 32`, открытые дескрипторы). Это не полный PASS этих тестов;
существующий Linux/Python 3.14 CI должен подтвердить полный маршрут.

Первый независимый review проверил 8232 случая полных согласованных таблиц;
его одобрение отозвано после подтверждения P2 с противоречивым максимумом.
Новая регрессия падает на head `b66311a` и проходит после исправления. Приёмка
требует повторного независимого review и CI окончательного head; прежний
[45/45 PASS](https://github.com/olegmed1-art/bridge-video-free/actions/runs/39296952974)
относится только к `b66311a` и сам по себе не закрывает новое замечание.

Повторная сверка main `1742ba4ed046e6d8c661341916b982465187087a` показала
только независимое изменение Observer; DDS, AGENTS и governance не изменились.

## Следующие действия и границы

Открытие PR выявило опасный автоматический trigger подготовки 30k; выполненный
объём, отмена и пауза workflow описаны в
[журнале локализации инцидента](DDS_EXECUTION_CONTAINMENT_20261019.md).
Эта подготовка не является свидетельством приёмки. До явного admission по
scope/бюджету вычислительный workflow оставляется на паузе; safety gates и
обычные программные тесты сохраняются.

После приёмки patch: отдельно ужесточить admission OOF-строк, затем подготовить
план пересчёта calibration на уже зафиксированных TRAIN-фактах с проверкой
хешей, бюджета, независимой calibration evaluation и разрешения на объём.
Не повторять 42000-row подготовку автоматически. Затем потребуются реальные
card-level/transfer/counterexample/rule evidence и отдельное решение о версии.

Любое DDS TRAIN/validation/sealed выполнение требует свежего допуска своего
scope и бюджета. Ни данный аудит, ни тесты его не выдают. IBM/Diana20, службы,
ключи, права и инфраструктура вне scope. Rollback patch — revert кода;
возврат ошибочной калибровки не делает её допустимой для promotion, поэтому
предпочтительно сохранить кандидат в testing до исправления вперёд.
