# DDS Stage 2C.3 — Card-Level Investigation & Difficulty Model

Дата: 2026-08-19.

## Итог

Этап 2C.3 выполнен офлайн на неизменяемом артефакте Stage 2C.2. Новые DDS-вызовы не выполнялись; использованы уже полученные candidate scores, optimal sets и DD-regret.

Главные результаты:
- 2 000 continuation-позиций получили difficulty-классификацию;
- 1 201 card-level ошибка оформлена как отдельное механическое расследование;
- из них 587 continuation-ошибок и 614 ошибок первого хода из counterexample корпуса;
- 1 201 regression-кандидат создан, но ещё не проходил blind retest;
- 441 counterexample pair переклассифицирована по изменению optimal set;
- validation и sealed остаются закрыты.

## Difficulty model

Классы:
- critical: 1–2 равнооптимальные карты;
- constrained: 3–4;
- flexible: 5–8;
- trivial: 9+.

### Разыгрывающий
- critical: 249 позиций, 71.08% optimal, mean regret 0.442;
- constrained: 226, 61.50%, mean regret 0.726;
- flexible: 247, 70.45%, mean regret 0.510;
- trivial: 278, 100%, regret 0.

### Защита
- critical: 130 позиций, только 3.08% optimal, mean regret 2.115;
- constrained: 165, 26.06%, mean regret 1.461;
- flexible: 320, 67.81%, mean regret 0.450;
- trivial: 385, 98.96%, mean regret 0.010.

Это подтверждает, что общий optimal-rate защиты сильно маскируется лёгкими позициями. Приоритет обучения — critical/constrained defense.

## Card-level investigations

Всего: 1 201.
- continuation: 587;
- opening-lead counterexamples: 614;
- severe regret >=2: 514;
- critical/constrained defense continuation errors: 248.

Каждое расследование содержит выбранную карту, все DDS candidate scores, optimal set, regret и difficulty. Семантическая бриджевая причина намеренно не назначается автоматически: без проверенной continuation line это было бы недоказанным выводом.

## Counterexamples

После сравнения optimal sets:
- action flip (optimal sets не пересекаются): 20 пар;
- partial flip: 421;
- negative controls: 0.

Для 20 настоящих action-flip пар модель изменила решение только в 2 случаях и не прошла ни одну пару полностью. Это сильный сигнал слабого переноса механизма, но выборка action-flip пока мала.

## Regression corpus

Создано 1 201 candidate regression cases. Статус: `generated_unretested`.

Они не считаются доказанным обучением до нового blind retest. Следующий gate:
`blind_regression_retest_before_validation`.

## Ограничения

- Новые DDS-вычисления в 2C.3 не проводились.
- Semantic skill labels не утверждаются.
- Validation/sealed не открывались.
- Навыки не переводились в stable/confirmed.
