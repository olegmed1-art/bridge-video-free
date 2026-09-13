# DDS Stage 2C.4 — Validation

Дата: 2026-08-20.

## Решение владельца и граница этапа

Validation была открыта по явному решению владельца после успешного Stage 2C.4 blind regression и selective methodology gate. Sealed-test на этом этапе не открывался.

До первого DDS-вызова был детерминированно сформирован validation-набор из 2 000 continuation-позиций: 1 000 решений разыгрывающего и 1 000 решений защиты. Исходные validation-семейства выбирались по SHA-256-порядку без использования DDS-результата; пересечение с TRAIN-семействами равно 0. Старый, candidate и hybrid выбор карты были зафиксированы до DDS; SHA-256 locked predictions: `2be61c3b70fb43aeb91f41240ff31fb252cb20170e2b2e3580305e0de7368a0d`.

Candidate строился только по ранее зафиксированным TRAIN-фактам Stage 2C.2. Selective gate использовал только прежнее family-disjoint OOF TRAIN evidence Stage 2C.4 и заранее установленный порог ожидаемого выигрыша > 0.08 взятки. Validation не использовалась для обучения или изменения gate.

## Результат

### Защита

- old: optimal 61.9%, mean regret 0.541, regret >=2 — 99;
- candidate: optimal 65.6%, mean regret 0.480, regret >=2 — 91;
- hybrid: optimal 64.7%, mean regret 0.492, regret >=2 — 96;
- hybrid переключился на candidate в 800 из 1 000 решений.

Защита прошла обе заранее объявленные проверки: hybrid снизил средний regret и число ошибок 2+ взятки относительно old.

### Разыгрывающий

- old: optimal 74.5%, mean regret 0.349, regret >=2 — 72;
- candidate: optimal 74.6%, mean regret 0.344, regret >=2 — 73;
- hybrid: optimal 74.7%, mean regret 0.344, regret >=2 — 68;
- hybrid переключился на candidate в 140 из 1 000 решений.

Заранее объявленная граница для разыгрывающего — hybrid mean regret не хуже old более чем на 0.01 — выполнена; фактически regret немного улучшился.

## Validation gate

**PASS.** Все три заранее объявленных условия выполнены:

- defense hybrid mean regret < old;
- defense hybrid regret >=2 < old;
- declarer hybrid mean regret <= old + 0.01.

При этом автоматическое продвижение модели не разрешено. Validation не добавлялась в обучающую память, historical database не изменялась, sealed-test не оценивался.

## Execution evidence

Validation run: `32416711449`, evidence artifact: `9424202210` (`dds-stage2c4-validation-32416711449`), artifact digest `sha256:52c6d91eb9336a69cd1475ef72d82fd40d0a6c3a1ee028a8e7c63937bca106e1`; внутренний архив validation evidence имеет SHA-256 `d122c58ed3aec773d6ef2812cb2acb7c36e8a129da2cc6f2db1d3c2aef2482fb`.

Сам workflow отмечен GitHub как failure из-за финального общего repository-cleanliness guard после завершения DDS-оценки. До этого успешно завершились: lock/pre-open checks, DDS evaluation всех 2 000 позиций, проверка validation evidence, проверка sealed boundary, packaging и upload artifact. `git diff --exit-code` не показал изменения tracked sources; падение произошло на последующей проверке untracked runtime-файлов. Поэтому этот технический hygiene-failure не меняет результат validation.

## Следующий gate

`owner_decision_on_sealed`

Sealed остаётся закрытым до отдельного явного решения владельца.
