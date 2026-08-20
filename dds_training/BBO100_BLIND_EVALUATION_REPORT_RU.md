# BBO-100 HOLDOUT-20 — слепая оценка карточных решений

**Статус:** BLIND_EVALUATION_COMPLETE

20 решений GPT-5.6 Sol были зафиксированы отдельным коммитом до открытия oracle/DDS. Сыгранная карта BBO/GIB используется только как описательное сравнение и не считается истиной школы или эталоном торговли.

## Общий результат

| Метрика | Результат |
|---|---:|
| Задач | 20 |
| Точное совпадение с сыгранной картой | 15 / 20 (75,0%) |
| Нулевой DD-regret | 17 / 20 (85,0%) |
| Средний DD-regret | 0,200 взятки |
| Медианный DD-regret | 0 |
| Максимальный DD-regret | 2 взятки |
| Лучше / равно / хуже сыгранной карты по DDS | 1 / 18 / 1 |

## По типам решений

| Тип | Задач | Нулевой regret | Средний regret | Точное совпадение с BBO | Лучше / равно / хуже BBO |
|---|---:|---:|---:|---:|---:|
| declarer_continuation | 7 | 7/7 | 0,000 | 7/7 | 0 / 7 / 0 |
| defense_continuation | 6 | 6/6 | 0,000 | 6/6 | 0 / 6 / 0 |
| opening_lead | 7 | 4/7 | 0,571 | 2/7 | 1 / 5 / 1 |

Главный технический вывод этого небольшого transfer-набора: все 13 проверенных решений после первого хода — 7 решений разыгрывающего и 6 решений защиты — получили нулевой DD-regret. Все три ошибки DDS пришлись на первый ход защиты. Это диагностический сигнал для следующего независимого теста, но не основание автоматически менять методику или объявлять навык устойчивым.

## Решения по сдачам

| Задача | Тип | Прогноз | BBO | Regret прогноза | Regret BBO | Равнооптимальные DDS-карты |
|---|---|---:|---:|---:|---:|---|
| BBO100-H01-opening_lead | opening_lead | DA | DK | 0 | 0 | C2, C6, C8, C9, DA, DK, DQ, S6, ST |
| BBO100-H02-declarer_continuation | declarer_continuation | H4 | H4 | 0 | 0 | H4, H9, HA, HQ, HT |
| BBO100-H03-defense_continuation | defense_continuation | H2 | H2 | 0 | 0 | H2, H3, H8, HT |
| BBO100-H04-opening_lead | opening_lead | HT | HT | 0 | 0 | C2, CT, D4, D5, DQ, H3, H4, H5, H6, H8, H9, HT, S3 |
| BBO100-H05-declarer_continuation | declarer_continuation | D2 | D2 | 0 | 0 | D2, D4, DJ |
| BBO100-H06-defense_continuation | defense_continuation | H2 | H2 | 0 | 0 | H2, H3, H7 |
| BBO100-H07-opening_lead | opening_lead | CA | D6 | 1 | 0 | D2, D6, H2, S3, S6, S7, SQ, ST |
| BBO100-H08-declarer_continuation | declarer_continuation | H4 | H4 | 0 | 0 | H4, H7, HJ |
| BBO100-H09-defense_continuation | defense_continuation | CQ | CQ | 0 | 0 | C6, C8, CQ |
| BBO100-H10-opening_lead | opening_lead | DT | DT | 2 | 2 | H9 |
| BBO100-H11-declarer_continuation | declarer_continuation | D5 | D5 | 0 | 0 | D5, D8 |
| BBO100-H12-defense_continuation | defense_continuation | S5 | S5 | 0 | 0 | S5, S8, SJ, SQ |
| BBO100-H13-opening_lead | opening_lead | CA | D2 | 1 | 1 | H3, H6, H8 |
| BBO100-H14-declarer_continuation | declarer_continuation | H3 | H3 | 0 | 0 | H3, HK |
| BBO100-H15-defense_continuation | defense_continuation | S3 | S3 | 0 | 0 | S3, S4, S6, S7, SJ, SQ |
| BBO100-H16-opening_lead | opening_lead | DA | SQ | 0 | 1 | DA |
| BBO100-H17-declarer_continuation | declarer_continuation | H2 | H2 | 0 | 0 | H2, HA, HK |
| BBO100-H18-defense_continuation | defense_continuation | DK | DK | 0 | 0 | D2, D4, DJ, DK |
| BBO100-H19-opening_lead | opening_lead | C5 | ST | 0 | 0 | C2, C5, C8, CQ, CT, D2, D4, D6, D8, HA, S4, S9, ST |
| BBO100-H20-declarer_continuation | declarer_continuation | C6 | C6 | 0 | 0 | C6, D4, DK, S3, S4, S5, S7, S9, SJ, ST |

## Evidence Gate и границы вывода

- До коммита с 20 прогнозами DDS не вызывался, скрытые руки и сыгранные карты для выбранных решений не просматривались.
- После фиксации прогнозов использован локальный DDS3 v3.0.0 с переиспользованием SolverContext.
- Равнооптимальные карты сохранены: другая карта с тем же DDS-результатом не считается ошибкой.
- DDS является double-dummy oracle. Нулевой DD-regret сам по себе не доказывает, что выбор был лучшим single-dummy решением при неполной информации.
- Результат N=20 — независимое диагностическое transfer evidence, а не достаточная выборка для калибровки уверенности или утверждения устойчивого навыка.
- BBO/GIB bidding не становится системой школы.
- Запись в канон, методику, программу и Student Profile остаётся DENY.

## Provenance

- prediction gate commit: `5a6c0f015effc7a9a916591f52da100110fa7beb`
- blind prediction commit: `2426531ab1cc02bf94ee6a4178a56a6874abb1aa`
- frozen task packet SHA-256: `4ee99c708bb997f28101c03367ef74eaf8c7242a413d1869685723d7b6b46c0f`
- frozen archive SHA-256: `18bcbc66e4aa79bb44907dd598046a5d755c3791e70e2497e82e3fae89d000dc`
- DDS source pin: `37c8a79f4c67c55d1a309ccb66dd00cb58af464a`
- evaluation run: `32392200131`
- full machine evidence on private Drive: `1Kllgriz9SFUyJX3ew1TmEfN59QkXRL_J`
- generated report on private Drive: `1sAOO-1gaFaNgsbgfNIrvHigOChkrabco`
