# DDS Stage 2C.4 — Sealed final blind gate

Дата: 2026-08-21.

## Граница эксперимента

Sealed-test открыт по отдельному явному решению владельца после PASS validation. Validation использовалась только как prerequisite: её результаты не добавлялись в обучение и не использовались для перенастройки candidate или selective gate.

До первого DDS-вызова детерминированно сформирован sealed-набор из 2 000 continuation-позиций: 1 000 решений разыгрывающего и 1 000 решений защиты. Исходные sealed-семейства выбирались по SHA-256-порядку без использования DDS-результата. Пересечение семейств TRAIN/validation/sealed равно 0. Старый, candidate и hybrid выбор карты были зафиксированы до DDS; SHA-256 locked predictions: `49afd9f7bc5d1905f13fef0f0ef61d99ff6a32ceac5c9646b202dc636872c484`.

Candidate строился только по ранее зафиксированным TRAIN-фактам Stage 2C.2. Selective gate использовал только прежнее family-disjoint OOF TRAIN evidence Stage 2C.4 и неизменный порог ожидаемого выигрыша > 0.08 взятки.

## Результат

### Защита

- old: optimal 65.5%, mean regret 0.472, regret >=2 — 79;
- candidate: optimal 65.6%, mean regret 0.460, regret >=2 — 80;
- hybrid: optimal 64.7%, mean regret 0.470, regret >=2 — 81;
- hybrid переключился на candidate в 767 из 1 000 решений.

Средний regret hybrid немного лучше old: 0.472 → 0.470. Но число ошибок стоимостью 2+ взятки выросло: 79 → 81. Поэтому защитная часть заранее объявленного gate не пройдена полностью.

### Разыгрывающий

- old: optimal 75.3%, mean regret 0.331, regret >=2 — 56;
- candidate: optimal 73.4%, mean regret 0.363, regret >=2 — 63;
- hybrid: optimal 74.1%, mean regret 0.352, regret >=2 — 60;
- hybrid переключился на candidate в 139 из 1 000 решений.

Заранее объявленная граница для разыгрывающего требовала hybrid mean regret не хуже old более чем на 0.01. Фактическое ухудшение составило 0.021: 0.331 → 0.352. Это условие не выполнено.

## Sealed gate

**FAIL.** Из трёх заранее объявленных условий выполнено только одно:

- defense hybrid mean regret < old — PASS;
- defense hybrid regret >=2 < old — FAIL;
- declarer hybrid mean regret <= old + 0.01 — FAIL.

Следовательно, успешный validation-результат не подтвердился на финальном независимом sealed-наборе. Это именно тот тип расхождения, для обнаружения которого sealed-test оставался закрытым до последнего шага.

Автоматическое продвижение модели запрещено. Sealed не добавлялся в обучающую память, historical database не изменялась. Следующий gate: `return_to_train_new_candidate`.

## Execution evidence

Sealed run: `32465500905`, execution PR `#222`, evidence artifact `9440546595` (`dds-stage2c4-sealed-32465500905`), artifact digest `sha256:45deb40694ceba2fb3eb12da798f89af2246127cec7082813f032aad3a0a5776`; внутренний архив evidence имеет SHA-256 `bb77d9a9580414a6b576327c255605fd5ebb515f505f91fcd1f1adfdf6b03357`.

Сам workflow отмечен GitHub как failure из-за финального общего repository-cleanliness guard. До него успешно завершились prerequisite checks, lock/pre-open checks, DDS evaluation всех 2 000 sealed-позиций, проверка sealed evidence, packaging и upload artifact. `git diff --exit-code` не зафиксировал изменения tracked sources; падение произошло в последующей hygiene-проверке untracked runtime-файлов. Этот технический post-evaluation failure не меняет sealed-результат, который уже был вычислен, проверен и сохранён.
