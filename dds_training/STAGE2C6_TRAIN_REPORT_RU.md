# DDS Stage 2C.6 — свежий TRAIN shadow gate

Дата: 21 августа 2026.

## Итог

Stage 2C.6 завершил независимую TRAIN-only проверку изменённого coarse policy и **прошёл TRAIN gate и methodology gate**.

Candidate был определён только по Stage 2C.5 TRAIN-shadow evidence: learned defense включён только в мастевых контрактах; в NT-защите и для всех позиций разыгрывающего сохранён baseline. Результаты Stage 2C.5 validation являются только rejection evidence и не использовались для fitting, feature selection, threshold/gate tuning, regression generation или policy selection.

## Изоляция и lock

Для свежего shadow были выбраны 650 ранее не использовавшихся TRAIN-семейств. Исключены 553 Stage 2B/2C.2 fit-семейства и 646 Stage 2C.5 TRAIN-shadow семейств; суммарно исключено 1 199 семейств. После построения curriculum оценено 2 000 позиций — 1 000 declarer и 1 000 defense — из 644 семейств.

Пересечение fresh shadow с fit/prior-shadow families равно 0. Предсказания были зафиксированы до первого DDS-вызова, SHA-256 locked predictions: `84c3c6e78fd0a4d1a4699c34fc6cb92c5dd97b99496edffc91bc9bf4cc28a92e`.

Validation и sealed не открывались; learning и historical database не изменялись; automatic promotion запрещён.

## Результаты policy

### Защита — общий итог

- old: optimal 63,4%, mean regret 0,520, regret >=2 — 97;
- policy: optimal 64,4%, mean regret 0,496, regret >=2 — 83.

Все три заранее объявленных условия TRAIN gate выполнены: mean regret снизился, ошибок ценой 2+ взятки стало меньше, optimal-rate не ухудшился.

### Защита — мастевые контракты

На 782 позициях learned policy применялся во всех случаях:

- old: optimal 64,45%, mean regret 0,4425, regret >=2 — 57;
- learned/policy: optimal 65,73%, mean regret 0,4118, regret >=2 — 43.

Это подтверждает TRAIN-only основание для selective deployment в мастевых контрактах.

### Защита — NT

На 218 NT-позициях policy оставался идентичен baseline, переключений 0:

- policy/old: optimal 59,63%, mean regret 0,7982, regret >=2 — 40.

Learned NT candidate улучшил optimal-rate и mean regret, но ухудшил regret >=2 до 46, поэтому coarse policy корректно сохранил baseline в NT.

### Разыгрывающий

Policy на всех 1 000 declarer-позициях идентична old, переключений 0:

- old/policy: optimal 75,6%, mean regret 0,336, regret >=2 — 69.

Learned declarer candidate отдельно был хуже по optimal-rate и mean regret, поэтому он не применялся.

## Gate и authority boundary

`train_gate_pass = true`.

`methodology_gate_pass = true`.

Следующая защищённая граница: `owner_decision_on_validation`.

Старая Stage 2C.5 validation authority не переносится автоматически на изменённый Stage 2C.6 candidate. Validation остаётся закрытой до нового явного решения владельца. Sealed для этого candidate не открывается; ранее открытый Stage 2C.4 sealed set не переиспользуется.

Никакого автоматического продвижения model, skills, canon, curriculum, teaching methodology или Student Profile не выполнялось.

## Execution evidence

Execution PR: `#262`, branch `dds-stage2c6-train-shadow-20260821`, head `4519ccd763150dc7fd2a30597c7dc56834e16b92`.

Workflow run: `32516106887`, job: `96877998609`, artifact: `9458864514` (`dds-stage2c6-train-shadow-32516106887`), digest `sha256:8c0edef0580f644580301919baddf3716e916c867610682ac7493c858450edd4`.

Workflow conclusion: success. Все substantive TRAIN steps, methodology verification и artifact upload завершились успешно.
