# DDS Stage 2C.6 — свежая validation

Дата: 2026-08-22.

## Что исправлено

После провалов предыдущих финальных/validation-проверок learned-policy был сужен без использования результатов validation или sealed для обучения. Зафиксированный Stage 2C.6 candidate использует learned-выбор карты только для защиты в мастевых контрактах. Защита в БК и весь розыгрыш остаются на прежнем baseline.

До этой validation кандидат прошёл отдельный свежий TRAIN-shadow: defense policy mean regret 0.520 → 0.496, optimal 63.4% → 64.4%, regret >=2 — 97 → 83. Validation и sealed при формировании Stage 2C.6 не использовались.

## Независимость новой validation

Новая validation-в wave сформирована до DDS детерминированно. Из 3 000 validation-семейств были отдельно реконструированы и исключены 650 семейств Stage 2C.4 и ещё 650 непересекающихся семейств Stage 2C.5. Новый набор использует 650 других source-семейств; пересечение с предыдущими 1 300 равно нулю. TRAIN / validation / sealed по-прежнему семейно непересекаются.

Из нового набора сформированы 2 000 continuation-позиций: 1 000 решений разыгрывающего и 1 000 решений защиты. Все policy-предсказания были зафиксированы до первого DDS-вызова; SHA-256 locked predictions: `6b2b4abbc13e6d70930441f9e7162f526f2caaf2f4d2e290499d4fb679ffe7d5`.

Предыдущие validation-результаты не использовались ни для fit, ни для tuning. Sealed не открывался и не читался.

## Результат

### Защита — общий итог

- baseline: optimal 66.9%, mean regret 0.474, regret >=2 — 89;
- фиксированная Stage 2C.6 policy: optimal 67.4%, mean regret 0.463, regret >=2 — 83.

Все три общих условия улучшились одновременно.

### Защита — мастевые контракты

На 804 позициях, где learned-policy действительно включался:

- baseline: optimal 67.66%, mean regret 0.4067, regret >=2 — 54;
- learned/policy: optimal 68.28%, mean regret 0.3930, regret >=2 — 48.

Таким образом, именно разрешённый learned-сегмент подтвердил улучшение и среднего качества, и optimal rate, и хвостового риска 2+ взятки.

### Защита — БК

На 196 позициях policy не переключалась и была полностью равна baseline:

- policy/baseline: optimal 63.78%, mean regret 0.750, regret >=2 — 35.

Диагностический learned-вариант в БК был хуже по всем ключевым показателям (optimal 61.22%, mean regret 0.806, regret >=2 — 44), что подтверждает правильность запрета learned-policy для NT defense.

### Разыгрывающий

Policy не переключалась ни в одной из 1 000 позиций и полностью совпала с baseline:

- optimal 75.4%, mean regret 0.360, regret >=2 — 71.

## Validation gate

**PASS.** Выполнены все восемь заранее объявленных условий:

- overall defense mean regret улучшился;
- overall defense regret >=2 уменьшился;
- overall defense optimal rate не снизился;
- suit-defense mean regret улучшился;
- suit-defense regret >=2 уменьшился;
- suit-defense optimal rate не снизился;
- declarer policy осталась точно baseline;
- NT-defense policy осталась точно baseline.

Validation не добавлялась в обучение, gate по ней не перенастраивался, historical database не изменялась, automatic promotion запрещён.

## Execution evidence

Workflow run: `32554317456`; job: `96986004905`; artifact: `9470992353` (`dds-stage2c6-validation-32554317456`), artifact digest `sha256:fa53eb64d11539fde49948e3fbe2852fd244866845fb224562edcad650170a6c`; внутренний архив evidence SHA-256 `3aade4653a6357ed228fa8960b6760993c6fffd47c5ec7a4cd687df626b40b1f`.

Все substantive validation steps завершились success, включая lock до DDS, восстановление immutable TRAIN evidence, 2 000/2 000 DDS-оценок, проверку protected boundary и отсутствие tracked source mutation.

## Следующий gate

`owner_decision_on_new_sealed`

Старый sealed-набор повторно использовать нельзя. PASS этой validation не является разрешением на promotion и не открывает sealed автоматически. Для следующей финальной проверки нужен новый независимый sealed-контроль и отдельное решение владельца.
