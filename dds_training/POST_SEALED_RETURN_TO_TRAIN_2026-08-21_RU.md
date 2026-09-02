# DDS — решение после sealed, 21.08.2026

## Статус

Stage 2C.4 sealed завершён на 2 000 ранее не открывавшихся continuation-позициях (1 000 declarer + 1 000 defense). Sealed gate: **FAIL**. Текущий candidate/hybrid **не продвигается**.

## Что запрещено

- не обучаться на sealed-картах, DDS-ответах, regret или outcome;
- не менять gate/threshold по результатам sealed;
- не переносить sealed в TRAIN, regression corpus или Student Profile;
- не объявлять модель/навык подтверждёнными;
- не менять авторскую методику или канон школы;
- не повторно открывать тот же sealed для подбора нового candidate.

## Что разрешено

Следующий цикл строится только из TRAIN-owned evidence, существующих TRAIN card-level investigations и независимых новых TRAIN-семейств.

Приоритеты, определённые ДО sealed и подтверждённые Stage 2C.3/2C.4 TRAIN evidence:

1. critical/constrained defense continuation;
2. уменьшение regret >= 2 на защите;
3. сохранение качества declarer при selective switching;
4. отдельное исследование action-flip opening-lead counterexamples без семантических утверждений до доказательства.

Sealed используется только как финальный факт `candidate rejected`; численные sealed outcomes не являются признаками, target-метками или данными настройки следующего candidate.

## Новый TRAIN gate

Новый candidate допускается к следующей независимой проверке только если:

1. training/evaluation families разделены family-disjoint;
2. predictions зафиксированы до DDS;
3. candidate и selective gate построены только на TRAIN evidence;
4. на TRAIN OOF/regression одновременно не ухудшается declarer и улучшается difficult defense;
5. отсутствует автоматический promotion;
6. validation/sealed остаются недоступными новому циклу до отдельного gate.

## Масштаб

Автоматический 30k/40k прогон не назначается. Размер нового TRAIN-корпуса определяется количеством новых независимых семейств и power/coverage нужных difficulty-бакетов, а не круглым числом.

## Authority

Статус: `RETURN_TO_TRAIN_NEW_CANDIDATE`.
Authority: `EVIDENCE_ONLY`.
Следующий разрешённый этап: подготовка нового TRAIN-only candidate и blind family-disjoint regression. Любое открытие validation или нового sealed требует отдельного owner decision.
