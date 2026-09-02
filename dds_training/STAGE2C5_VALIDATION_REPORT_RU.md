# DDS Stage 2C.5 — validation

Дата: 21 августа 2026.

## Итог

Stage 2C.5 validation завершена на 2 000 заранее зафиксированных позициях из 644 семейств. Validation gate: **FAIL**. Следующий разрешённый статус — `return_to_train_new_candidate`.

Предсказания были зафиксированы до первого DDS-вызова (`locked_prediction_sha256 = 718b2428bf7e01984852d7b3a31542484c3e11e42ca761e827b0dc3b7038b5a5`). Validation не использовалась для обучения или настройки gate, historical database не изменялась, sealed не открывался, automatic promotion запрещён.

## Результаты

### Защита

- old: optimal 64,6%, mean regret 0,471, regret >=2 — 71;
- candidate/policy: optimal 65,3%, mean regret 0,464, regret >=2 — 79.

Mean regret улучшился и optimal-rate вырос, но число тяжёлых ошибок regret >=2 ухудшилось с 71 до 79. Поэтому заранее объявленное условие `defense_regret_2plus_condition` не выполнено.

### Разыгрывающий

Policy намеренно оставалась идентичной baseline: optimal 75,1%, mean regret 0,349, regret >=2 — 69, переключений 0. Условие сохранения declarer выполнено.

## Методологическая граница

Validation-результат является только фактом отклонения текущего candidate. Численные outcomes validation запрещено использовать как TRAIN-признаки, target-метки, regression cases или для подбора следующего threshold/policy. Новый candidate должен быть сформирован только из TRAIN-owned evidence и проверен на новой family-disjoint TRAIN-shadow волне.

Новый sealed **не открывается**, поскольку validation не прошла.

## Execution evidence

Workflow run: `32510692975`; job: `96860827320`; conclusion: `success`.

Artifact: `9456931482` (`dds-stage2c5-validation-32510692975`), 681285 bytes, digest `sha256:6bc1be2c1199994a8141ef3b7c8b0454260ac6c83ec8eb6fdbe0054032d2211c`.
