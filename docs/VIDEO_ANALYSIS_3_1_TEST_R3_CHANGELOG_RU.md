# Изменения Video Analysis 3.1-test-r3

Дата: 2026-08-29

## По сравнению с r2

- исправлен ложный speaker PASS при малом числе размеченных сегментов;
- добавлен тестовый coverage gate `>= 0.80` одновременно по числу сегментов и
  длительности речи;
- добавлена отдельная speaker schema v2 только для тестового профиля;
- verifier пересчитывает coverage и проверяет закреплённый порог;
- evidence-export показывает coverage для v2 и не меняет стабильный v1;
- `content_result` исправлен с `PARTIAL` на `ARCHIVE_ONLY`, пока все
  предметные и педагогические стадии deferred;
- в machine-readable definition добавлена speaker evidence policy;
- добавлены adversarial regression tests.

## Неизменяемые границы

- `3.1 FREE` не изменён;
- `bridge_lesson_3_1_test` остаётся opt-in и `SHADOW_ONLY`;
- `canonical_promotion_allowed=false`;
- `production_activation_allowed=false`;
- карточные suggestion-данные не становятся фактами;
- «Диана 14» не запускается автоматически.
