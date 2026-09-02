# DDS Stage 2C.4 — Blind Regression Retest и методический аудит

Дата: 2026-08-20.

## Что выполнено

Stage 2C.4 фактически запущен и завершён. Workflow run: `32380533183`, artifact: `9410795030` (`dds-stage2c4-blind-regression-32380533183`).

Метод: 5-fold family-disjoint cross-fit. Семейства не пересекались между обучением и тестом. Новые DDS-вызовы не выполнялись: использованы уже зафиксированные candidate scores / DD-regret из Stage 2C.2. Validation и sealed не открывались.

## Базовый candidate

### Защита

- optimal-rate: 64.5% → 68.7%;
- mean regret: 0.664 → 0.553;
- regret >=2: 149 → 109;
- critical (1–2 optimal cards): 3.08% → 30.0%, mean regret 2.115 → 1.415;
- constrained (3–4): 26.06% → 43.03%, mean regret 1.461 → 1.024.

### Разыгрывающий

- optimal-rate: 76.8% → 76.2%;
- mean regret: 0.400 → 0.390;
- regret >=2: 83 → 78;
- critical: 71.08% → 77.11%;
- constrained: 61.50% → 65.49%.

Кандидат улучшает трудные позиции, но местами ухудшает flexible/trivial. Поэтому полный автоматический переход на него не разрешён.

## Regression subset

На 587 прежних continuation-ошибках:

- mean regret: 1.813 → 0.995;
- regret >=2: 232 → 120;
- 298 позиций улучшены;
- 31 ухудшены;
- 269 из 587 стали равнооптимальными (45.83%).

Это показывает перенос между независимыми семействами, а не простое воспроизведение исходных DDS-ответов.

## Selective gate

Для защиты и розыгрыша построен консервативный family-disjoint gate: candidate применяется только в feature-бакетах, где на других folds ожидаемая выгода > 0.08 взятки; иначе остаётся старый ответ.

Итог hybrid-политики:

- защита: optimal 67.8%, mean regret 0.566, regret >=2 = 115 (старое: 64.5%, 0.664, 149);
- разыгрывающий: optimal 77.8%, mean regret 0.383, regret >=2 = 83 (старое: 76.8%, 0.400, 83).

Методический gate: **PASS**.

## Ограничения

- action-flip counterexamples не получили отдельного нового card-choice retest в этом workflow; это остаётся отдельной исследовательской задачей и не даёт права объявлять transfer по counterexamples доказанным;
- semantic bridge mechanism labels не утверждались;
- automatic skill/model promotion запрещён;
- validation/sealed остаются закрыты.

## Следующий gate

`owner_decision_on_validation`

Технически Stage 2C.4 завершён. Следующее действие, которое нельзя выполнять автономно по принятому правилу управления процессом: решение владельца об открытии validation.
