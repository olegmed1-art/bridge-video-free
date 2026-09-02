# DDS Learning v2.5 — Stage 2B refinement

Статус: **candidate / testing**. Версия создана после первого реального OOF-прогона
v2.4 на 42 000 TRAIN-задачах. Она сохраняет все исходные факты и исправляет
недостатки, выявленные измерениями, а не предположениями.

## 1. Доказательства, вызвавшие ревизию

Первый Stage 2B preparation run подтвердил:

- 42 000 семейно-изолированных OOF-прогнозов;
- нулевое пересечение семейств между обучением и held-out fold;
- общий выигрыш candidate v0.3 по числу взяток и первому ходу;
- отсутствие вызовов DDS, validation и sealed test;
- неизменность SQLite-базы с 50 497 DDS-фактами.

Одновременно были обнаружены четыре ограничения:

1. `baseline` fallback с нулевой поддержкой получал почти единичную raw confidence,
   поскольку имел нулевую дисперсию и нулевую поправку.
2. Общий отчёт смешивал `bridge-baseline-v0.1` и `bridge-adaptive-v0.2`, поэтому не
   являлся чистым сравнением v0.3 с текущей моделью.
3. 500 line-source позиций дали 1 062 продолжения разыгрывающего и только 938
   продолжений защиты вместо требуемого равного баланса.
4. Агрегированная очередь потеряла фактическую деноминацию и помечала её как
   `unknown`.

## 2. Support-aware raw confidence

Для contract predictor raw probability теперь обязательно зависит от:

- числа OOF-наблюдений;
- дисперсии остатка;
- величины поправки;
- уровня backoff.

Если `support_count=0` или используется `baseline`, raw probability равна
консервативному prior `0.18`, а не `1.0`.

Raw probability остаётся только входом к калибратору. Канонический вывод содержит:

```text
raw_probability
calibrated_probability
lower_confidence_bound
support_count
accept
requires_deeper_review
```

Решение `accept=true` возможно только при достаточной поддержке и нижней границе
выше порога.

## 3. Чистое семейное сравнение

OOF-результат теперь стратифицируется одновременно по:

```text
source_predictor_version
+ task_family
```

Семейства:

- `contract_suit`;
- `contract_nt`;
- `opening_lead_suit`;
- `opening_lead_nt`.

Основным сравнением является candidate v0.3 против `bridge-adaptive-v0.2`.
Исторический baseline-v0.1 показывается отдельно.

Для каждой группы сохраняются:

- парный средний выигрыш;
- bootstrap 95% interval;
- доля нулевого regret/точного числа взяток;
- доля ошибок 2+;
- число улучшений, ухудшений и совпадений.

## 4. Mixed family policy

Новая версия не требует использовать одну модель во всех семействах.

Кандидат разрешается к будущей validation только если:

- средний OOF-выигрыш не меньше практического порога;
- нижняя граница bootstrap-интервала положительна.

По фактическим данным первого прогона ожидаемая политика:

```text
contract_suit        → candidate v0.3
opening_lead_suit    → candidate v0.3
contract_nt          → fallback v0.2
opening_lead_nt      → fallback v0.2
```

Это не автоматическое продвижение: validation всё равно требует отдельной команды.

## 5. Точная симметрия continuation curriculum

Количество line-source позиций увеличено до 650.

Curriculum обязан содержать ровно:

```text
1 000 declarer_continuation
1 000 defense_continuation
```

Недостаток одной стороны теперь блокирует preparation. Он не компенсируется
позициями другой стороны.

## 6. Multi-contract blueprint

Для 500 свежих TRAIN-семейств создаётся blueprint:

```text
500 × 4 разыгрывающих × 5 деноминаций = 10 000 задач
```

Blueprint:

- сохраняет `root_deal_id`, fold и split;
- не вызывает DDS;
- требует locked prediction до будущего DDS-запуска;
- маркируется reinforcement, а не independent transfer;
- не имеет права запускать вычисление самостоятельно.

Он позволяет извлекать больше учебных меток из одной DD-таблицы без нарушения
blind-first порядка.

## 7. Деноминация в очереди повторений

Перед агрегацией очередь обогащается метаданными исходной задачи. Группировка:

```text
skill + error_code + strain + mechanism + due_window
```

`NT` и мастевые контракты больше не смешиваются в `unknown`.

Исторические ошибки и regression cases не удаляются.

## 8. Calibration diagnostics

Отдельный отчёт сохраняет по каждому family/backoff:

- raw Brier;
- calibrated Brier;
- raw ECE;
- calibrated ECE;
- среднюю raw probability;
- среднюю calibrated probability;
- среднюю нижнюю границу;
- число принятых ответов;
- фактическую точность принятых ответов.

Наличие калибратора без диагностики больше не считается достаточным.

## 9. Канонический preparation workflow

Workflow `.github/workflows/dds-stage2b-prepare.yml`:

- запускается только вручную владельцем из `main`;
- восстанавливает exact artifact завершённого 30k TRAIN;
- проверяет внешний ZIP SHA-256 и внутренний state SHA-256;
- требует 50 497 DDS-фактов и 42 000 TRAIN-фактов;
- записывает SHA-256 базы до preparation;
- запускает только `stage2b_prepare_v25.py`;
- повторно проверяет SHA-256 базы;
- требует точный баланс 1000/1000;
- требует blueprint из 10 000 задач;
- требует mixed family policy;
- подтверждает закрытые validation/sealed;
- сохраняет один компактный архив.

## 10. Следующий gate

После успешной подготовки v2.5 разрешается только следующий TRAIN-контур:

```text
locked predictions для 2 000 continuations
+ locked predictions для blind counterexamples
+ locked predictions для 10 000 multicontract blueprint задач
→ отдельная owner-authorized DDS оценка
→ card-level расследования
→ regression и методический аудит
→ решение об открытии validation
```

Validation и sealed test не открываются этим документом или preparation workflow.

## 11. Граница утверждений

v2.5 улучшает качество эксперимента. Она не изменяет базовые веса GPT, не
объявляет навык устойчивым, не создаёт новую систему торговли и не заменяет
методику преподавателя.
