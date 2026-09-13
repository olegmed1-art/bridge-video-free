# Модуль Bridge School DDS3

Канонический double-dummy вычислительный модуль проекта «Школа спортивного бриджа».

Актуализировано по финальному аудиту issue #236 2026-08-22.

## Каноническое ядро

- Основной вычислитель школы: **DDS3**.
- Зафиксированная версия ядра: `v3.0.0+cdd13cf5b700788ac8c1391501b42445b3129b45`.
- Полная сдача рассчитывается существующим C++ entry point `dds/dds_pbn_cli.cpp` через `Dockerfile.dds3`.
- Произвольные позиции рассчитываются persistent worker `dds/dds_position_ctx_cli.cpp`, который держит один `SolverContext` и переиспользует его между связанными запросами.
- Никакой heuristic/model/web-solver/alternate-DD fallback не разрешён.
- При невозможности выполнить DDS3 операция должна завершаться fail-closed ошибкой DDS; подстановка ответа другого решателя запрещена.
- Успешный результат DDS3 содержит provenance с `engine: DDS3` и `fallback_used: false`.

## Поддерживаемые контракты

### Полная сдача

Схема: `bridge_dds3/1.0`.

Поддерживаются PBN и структурированная раздача N/E/S/W. Перед вычислением полная сдача валидируется как 52 уникальные стандартные карты, по 13 карт у каждой руки.

Канонический результат:

- порядок рук N/E/S/W;
- порядок мастей S/H/D/C/NT;
- таблица DD 5x4;
- Par score и Par contracts;
- provenance DDS3.

### Произвольная позиция

Схема: `bridge_dds3_position/v1`.

Операции runtime: `position` и `position_all_moves`.

Вход включает остатки рук, козырь/NT, сторону хода и при необходимости незавершённую текущую взятку. Позиция проверяется на допустимые карты, дубликаты, согласованность количества остаточных карт и отсутствие уже сыгранной карты в остатках рук.

DDS3 возвращает оценки всех legal moves. Нормализующий слой сохраняет:

- максимальное число достижимых взяток;
- все равнооптимальные карты;
- значение каждой альтернативы;
- regret каждой альтернативы;
- классы regret `0`, `1`, `2+`.

Persistent worker сообщает telemetry `worker_generation`, `worker_request_index`, `context_reused`, `nodes`, `previous_nodes`, `nodes_delta`. Повторные и ветвящиеся позиции могут использовать один `SolverContext`/TT.

Важно: валидация позиции подтверждает внутреннюю корректность текущего состояния, но сама по себе не доказывает историческую последовательность всех ранее сыгранных карт. Для точной атрибуции ошибки по ходу нужен реальный card-by-card replay.

### Raw screenshots / images

Структурированный контракт наблюдения: `bridge_dds3_screenshot_observation/v1`.

Пакетный контракт: `bridge_dds3_screenshot_batch/v1`.

Production raw-image operation принимает фактические JPEG/PNG/WebP bytes без ручного структурирования сделки. `solve_raw_image()` автоматически пробует только явно поддерживаемые local/free layout extractors и останавливается fail-closed после того, как распознанный layout дал неоднозначность/ошибку; другой extractor не используется как скрытый repair.

На 2026-08-22 положительно field-proven пять layout families:

- Israel Bridge Federation yellow panel;
- publication cross;
- publication grid;
- named quadrant;
- EBU appeals-form cross.

Raw-image gate требует **до DDS3**: явные Board/Dealer/Vulnerability, все четыре руки, confidence для каждого metadata/hand-suit field, identity extractor, SHA-256 конкретных входных bytes и затем ровно 52 уникальные стандартные карты / 13 на руку. Недостающая/неоднозначная карта, конфликтный или неподдерживаемый layout не ремонтируются дополнением колоды или бриджевой логикой.

Канонический regression foundation содержит 60 реальных federation board images из трёх независимых source PDFs с отдельной vector-text truth. Для первого layout measured result: 42/60 exact deal + exact metadata, wrong accepts 0, accepted precision 42/42=100%, real-pixel negative cases 5/5 rejected. Четыре дополнительные семьи имеют отдельные реальные field gates и per-family exact/rejection metrics. Полный audit: [`docs/dds3-issue236-final-evidence-20260822.md`](../../docs/dds3-issue236-final-evidence-20260822.md).

Это не обещание распознавать любой ранее неизвестный графический дизайн. Неизвестный layout должен быть добавлен и доказан отдельно; пока этого нет, корректный результат — vision rejection. Такое ограничение является fail-closed safety boundary, а не разрешением угадывать данные.

Core DDS3 сам не выполняет OCR и не угадывает карты. Vision-layer строит `ScreenshotDealObservation`; только после evidence/52-card validation математический DDS3 получает структурированную сдачу.

## Runtime API

Standalone runtime `dds3_runtime/app.py` предоставляет:

- `/readyz` — отдельная readiness-проверка настоящего DDS3;
- `/v1/compute` — аутентифицированный вычислительный endpoint;
- `operation=dd_table` — полная таблица;
- `operation=position` / `position_all_moves` — произвольная позиция и все legal moves;
- raw-image operation — image bytes -> local/free vision -> strict validation -> DDS3.

Неисправность DDS3 не должна ломать общий health/API школы: DDS-зависимые операции изолированы.

## Контрольная проверка классическим DDS

Классический **DDS 2.9.0** разрешён только как post-hoc regression/reference expert. Он не является fallback и не имеет права влиять на первичный DDS3-ответ.

Правильный протокол сравнения:

1. зафиксировать входной PBN/corpus и его hash до расчётов;
2. запустить DDS3 первым;
3. заморозить DDS3 outputs и их SHA-256;
4. только после этого запустить DDS 2.9.0;
5. сравнить результаты после обоих вычислений.

DDS3 и DDS 2.9.0 имеют общую кодовую родословную, поэтому совпадение является сильным regression evidence, но не полностью независимым математическим доказательством.

## CI / evidence gate

`.github/workflows/dds3-module.yml` проверяет реальный pinned DDS3, deterministic golden, Board 16, настоящий position worker, повторное использование context/TT, negative position case, production runtime readiness, HTTP `dd_table` и HTTP `position_all_moves`.

Vision workflows отдельно проверяют 60-image real corpus contract и пять field-proven layout families, включая severe-crop / ambiguity rejection. Canonical image truth строится независимо от DDS3; ошибка extraction не маскируется как ошибка solver.

Численный DD-результат считается доказанным только если он фактически получен DDS3. Unit/mock tests могут проверять контракт и нормализацию, но не заменяют реальный DDS3 integration gate.

## Архитектурное правило

DDS3 отвечает за вычислительные факты. Интерпретация торговли, методика преподавания и учебные выводы находятся выше этого слоя и должны опираться на материалы школы. DDS-результат сам по себе не создаёт нового правила торговли или преподавания.

Канонический алгоритм анализа и улучшения: [`ALGORITHM_V2.md`](./ALGORITHM_V2.md).
