# Приёмка качества миров и BEN: план без запуска

Статус: PLAN / NOT EXECUTED. Этот документ не разрешает producer, обучение,
canary, открытие validation/sealed, расходы или release. Принятая Learning v2.3
не меняется; v2.5 остаётся кандидатом. Capacity benchmark из одного повторяемого
запроса и интеграционный canary на четырёх мирах не являются quality evidence.

## Фиксируемый входной контракт

До любого oracle/evaluation фиксируются source commit, runtime, BEN model/network
hashes, bidding system/profile/version, scoring mode, dealer, vulnerability,
доступная игроку рука, публичная торговля и допустимый публичный play prefix.
Отсутствующее значение не заменяется нулём, PASS или NONE. BEN получает только
информацию своего игрока; скрытые карты и DDS answer не входят в его запрос.

Для каждого физического расклада сохраняются canonical 52-card identity,
root-family ancestry, split/fold, источник и преобразования. Одинаковые карты
под разными IDs выявляются как duplicate collision; родство не выдумывается
по одному сходству PBN. Нужны 13 уникальных карт на руку, одна полная колода,
сохранение известной руки и проверка всех объявленных hard constraints.

Генератор сейчас поддерживает только известную руку и explicit hard constraints.
Это не posterior по торговле. До такой заявки нужны версия правила/модели,
связывающей публичную торговлю с ограничениями/весами, и отдельная проверка
распределений HCP/shape/voids/ключевых карт на заранее заданных сценариях.
Неполная генерация и невозможные ограничения дают отдельный unsupported/error,
без усреднения оставшихся миров как полного набора. Uniform и weighted sampling
различаются в отчёте; веса, coverage и effective sample size публикуются явно.

## Парное сравнение качества

1. До оценивания зафиксировать cohort семей, задачи, candidate actions, seed,
   полный общий список world IDs/weights и неизменяемые baseline predictions.
   Оба варианта оцениваются на одном наборе. Missing/illegal/unknown/duplicates
   учитываются явно; нельзя выбирать победителя по удобному пересечению.
2. Восстановить каждую завершённую торговлю из dealer и calls, проверить contract,
   declarer, doubles/redoubles и passed-out. Связать DDS request/result hashes
   с этим миром; чужая таблица, неполная матрица или неверный seat order — отказ.
3. Разделить DDS proxy и качество решения при неполной информации. Per-world
   double-dummy допускает знание скрытых рук при продолжении игры; это не
   доказательство реализуемой single-dummy стратегии. Нулевой DD regret и совпадение
   с BBO/BEN не превращаются в канон или человеческий навык.
4. Для IMP сравнивать разность очков двух вариантов в каждом мире, затем применять
   объявленную IMP conversion и агрегировать. Для MP нужен зафиксированный field
   context. Средние raw points не выдаются за IMP/MP. Отчёт сохраняет NS/сторону
   игрока, success/defeat probabilities, illegal/missing, mean loss и tail 2+;
   suit/NT, lead/declarer/defense и vulnerability показываются отдельно.
5. Confidence оценивается на внешних family holdouts всех upstream fits и выбора
   параметров. Brier/ECE и coverage-risk относятся только к этому cohort. Обычный
   OOF base predictor плюс исключение evaluation families из fit калибратора
   доказывает лишь прямое исключение этих targets из calibrator fit; другие base
   models могли учиться на них. Такой crossfit — conditional diagnostic, не
   независимая оценка всего pipeline.

До запуска заранее фиксируются sample-size/power basis, practically relevant
improvement и условия non-regression по сегментам. Для заявленного превосходства
нужны парные family-cluster intervals и достижение обоих порогов: статистического
и практического; одно значение среднего или latency PASS недостаточно. Неизвестные
порог/размер/бюджет оставляют приёмку NOT READY, а не автоматически выбираются
по уже увиденным результатам.

## Gate и независимая проверка

Software acceptance: negative cases на скрытую информацию, противоречивую ancestry,
переименованные дубликаты, неполное покрытие, unknown vulnerability, ошибочную
торговлю и перепутанную DDS-таблицу должны отклоняться. Это проверяется малыми
синтетическими тестами и не является corpus qualification.

Quality acceptance: новый reproducible baseline receipt связывает model SHA,
фактически потреблённые TRAIN task/root-family IDs, hashes fit-time inputs и
source/runtime. Исторический receipt не синтезируется и заново не разыскивается.
Отсутствие такого receipt — UNKNOWN training lineage, а не доказанный leakage.

I2 проверяет расчёт метрик, family exclusion и provenance независимо от scorer.
Человеческое single-dummy решение и применимость к системе школы требуют отдельной
слепой transfer-проверки и I4 при существенном споре. Capacity, исполнитель,
лимиты и денежный бюджет — отдельный admission gate. Release HOLD сохраняется
независимо от успешных unit tests или CI.
