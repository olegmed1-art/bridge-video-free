# DDS Stage 2C.5 — независимый TRAIN shadow gate

Дата: 21 августа 2026.

## Итог

Новый TRAIN-only цикл после отклонения предыдущего candidate на sealed дошёл до следующей независимой границы и **прошёл TRAIN methodology gate**.

Новый policy был сформирован только из evidence, существовавшего до validation/sealed: для защиты используется learned card-loss candidate, для разыгрывающего сохраняется прежний baseline без переключений. Validation и sealed не использовались для обучения, выбора признаков, настройки threshold или gate.

## Независимость выборки

Candidate обучался на 2 000 TRAIN continuation-фактах из 553 семейств. Для shadow-проверки детерминированно выбраны 650 ранее не использовавшихся TRAIN contract-семейств. После построения continuation curriculum проверено 2 000 позиций — 1 000 declarer и 1 000 defense — из 646 семейств.

Пересечение candidate-fit и shadow семейств равно 0. Пересечение TRAIN/validation/sealed семейств в исходном корпусе также равно 0. Предсказания были зафиксированы до первого DDS-вызова, SHA-256 locked predictions: `a7afde6d2409e302464ad93ea4acd4b7ed96f8621015ec81cc62312fb73c1583`.

## Результаты

### Защита

- old: optimal 66,7%, mean regret 0,486, regret >=2 — 99;
- learned/policy: optimal 68,2%, mean regret 0,429, regret >=2 — 80.

Все три заранее объявленных условия для защиты выполнены: mean regret снизился, ошибок ценой 2+ взятки стало меньше, optimal-rate не ухудшился.

### Разыгрывающий

Learned candidate отдельно оказался слабее baseline (75,1% optimal и mean regret 0,354 против 78,5% и 0,298), поэтому новая policy его не применяет. Policy на всех 1 000 declarer-позициях идентична old: optimal 78,5%, mean regret 0,298, regret >=2 — 57, переключений 0.

Это соответствует заранее выбранной fail-closed стратегии: улучшать защиту, не рискуя качеством разыгрыша.

## Gate

`train_gate_pass = true`.

`methodology_gate_pass = true`.

Следующая граница: `owner_decision_on_validation`.

Автоматическое продвижение модели запрещено. Historical database не изменялась. Validation и sealed не открывались и не использовались.

## Execution evidence

Workflow run: `32507310240`, job: `96850261927`, artifact: `9455757407` (`dds-stage2c5-train-shadow-32507310240`), digest `sha256:f8c3d6d22fd047b317b56e5f885f86974cc431f38a48b6cd15487921eb426218`.

GitHub workflow получил итоговый статус failure только на финальном generic repository-cleanliness guard после завершения вычислений. До этого успешно завершились: восстановление verified TRAIN artifacts, восстановление exact main TRAIN corpus, lock predictions, DDS evaluation всех 2 000 позиций, проверка methodology gate, packaging и upload artifact. `git diff --exit-code` не показал изменения tracked sources; следом guard обнаружил runtime untracked-файл. Это не влияет на полученный TRAIN evidence и не даёт права автоматически открывать validation.
