# Аудит алгоритма анализа видео 3.1-test-r3

Дата: 2026-08-29

Статус: `CONTRACT_PASS / FIELD_NOT_RUN / SHADOW_ONLY`

## Что проверялось

- определение и профиль `bridge_lesson_3_1_test`;
- readiness и учёт executed/deferred стадий;
- producer/verifier speaker evidence;
- изоляция тестового профиля от стабильного `bridge_lesson`;
- evidence-export;
- карточные shadow-контракты, повороты, речь, board metadata и `39 → 13`;
- запреты production, Canon promotion и следующего видео.

## Найденные ошибки r2

### P1 — ложный speaker PASS при низком покрытии

Для положительного результата требовались два кластера, но не требовалась
минимальная доля размеченного транскрипта. Два размеченных сегмента из длинного
урока могли дать `PASS`, хотя большая часть речи оставалась без говорящего.

### P1 — завышенный статус содержания

При полностью отложенных `bridge_context`, `bridge_positions` и
`educational_candidates` readiness называл результат `PARTIAL`. По стандарту
3.1 FREE технический архив без предметного и педагогического слоя должен быть
`ARCHIVE_ONLY`.

### P2 — риск изменения стабильного receipt

Первая реализация coverage-полей добавляла их также в стабильный receipt.
Регрессионный тест это обнаружил. Изменение отменено для стабильного профиля:
новая схема и coverage существуют только в `bridge_lesson_3_1_test`.

## Исправления и улучшение

1. Ревизия повышена до `3.1-test-r3`.
2. Для тестового профиля введён speaker report
   `universal-video-speaker-structure-v2`.
3. Положительный speaker-result требует одновременно
   `label_coverage >= 0.80` и `speech_duration_coverage >= 0.80`.
4. При недостаточном покрытии все speaker-аннотации удаляются и возвращается
   `INCONCLUSIVE / INSUFFICIENT_LABEL_COVERAGE`.
5. Verifier независимо пересчитывает coverage и проверяет hash-bound порог.
6. Evidence-export показывает coverage только для тестовой схемы v2.
7. Стабильный профиль сохраняет прежнюю схему v1 и прежнюю форму receipt.
8. `content_result` при полностью deferred domain/pedagogy изменён на
   `ARCHIVE_ONLY`.

## Проверки

- 165 актуальных Universal Video / Bridge Video / Bridge Vision tests: PASS;
- целевые readiness/speaker/conformance/evidence-export tests: PASS;
- Python compileall: PASS;
- JSON example: PASS;
- архивная целостность: проверяется при сборке итогового пакета.

Adversarial-проверки включают:

- два кластера при покрытии 20% — fail closed;
- 80% коротких сегментов при низком покрытии длительности — fail closed;
- подмену минимального coverage-порога — fail closed;
- реальные имена — fail closed;
- cluster collapse — fail closed;
- изменение readiness — fail closed;
- сохранение стабильного v1 receipt без новых полей — PASS.

## Что не доказано

- полевой результат на «Диане 13» с новой схемой;
- точность speaker boundaries по ручной разметке;
- карточный pixel-backend и утверждённый interface-profile;
- распознавание карт на независимом holdout;
- полный бриджевый и педагогический master-analysis нового runner.

Поэтому это contract PASS, а не production или field PASS.

## Операционная граница

Стабильный 3.1 FREE, Канон, production и runtime не изменялись. Видео, Oracle,
Drive, deploy и GitHub не запускались. «Диана 14» не запускалась.
