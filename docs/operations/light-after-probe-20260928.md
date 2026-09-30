# Oracle Light: read-only диагностика AFTER, 28.09.2026

Статус: отдельная диагностическая ветка; первый live read-only запуск 36401535332
завершён PASS 28.09.2026 09:08 UTC. Restore не выполнен.
Олег явно разрешил дальнейшую работу по усмотрению 28.09.2026 13:05 Asia/Yerevan;
это разрешение использовано для ограниченной live диагностики.
Разрешение Олега 28.09.2026 11:18 Asia/Yerevan: подготовить диагностическую правку,
совместимую с незавершённым восстановлением, без изменения main и рабочей среды.

## Что подготовлено

`ops/incident/light_after_probe_20260928.py` — отдельный диагностический процесс.
Он не добавлен в maintenance bundle и не заменяет исходные модули. Перед импортом
проверяется весь исторический bundle SHA256
`4ff5fabec410197437c130d3b56980451c32f90a5fffb9e468c211661f7337da` из commit
`8bbc1d61010ef70c3fca02b5151ac86fce02a144`. Тест реально собирает этот пакет из Git,
сравнивает digest и загружает его в отдельном изолированном Python.

Проверка привязана к request `01112f45…`, scope `c0795e9e…`, failed run
36388443245/1/job108818853655 и полному AFTER digest `51e06db1…`.
Полные значения зафиксированы в коде. Права, журнал и request не переписываются.
Ранее потреблённый request используется исключительно как неизменяемое
доказательство; claim(), Agreement, runtime.stage() и восстановление не вызываются.
Истёкшее окно не обновляется. Диагностика не выдаёт права на последующий restore.

Последовательно наблюдаются:

1. Точное сохранённое содержимое request, scope, целевая БД и completed failed run.
2. Совпадение текущего main с историческим source и полный live HOLD.
3. Отсутствие выполняющихся registry workflow и прежних supervisor-процессов.
4. Оригинальный OwnedConnections.assert_drained() на Oracle через byte-verified
   установленный driver, с теми же owner-параметрами соединения.
5. Оригинальный полный engine.snapshot(), dormant и равенство принятому AFTER.
6. Повторная проверка request, HOLD, main, failed run и происхождения модулей.

В каждой owner-сессии первой SQL устанавливается session
`default_transaction_read_only=on`; проверяются default и фактический
`transaction_read_only`, а в snapshot-транзакции фактический режим проверяется
повторно. libpq options остаются пустыми: оригинальная проверка Neon запрещает
routing overrides. SET влияет только на собственную сессию.

Вывод — только фиксированные status/phase/code; без exception text, токенов,
DSN, приватного HOLD, строк журналов или содержимого snapshot. Любая ошибка
прекращает дальнейшие шаги. PASS явно содержит resume_authorized=false и
historical_cause_proven=false.

## Границы результата

Этот процесс может установить, какой read-only gate отказывает сейчас на Oracle.
Он не воспроизводит старое временное состояние, active run/job admission,
claim tail или совокупный mutating stage admission. Проверка supervisor
уже включена; дополнительный journal gate описан ниже. PASS не
объясняет исторический RPC_EOF и не разрешает новую попытку restore. Существующий
post-inspect уже подтвердил целостность журналов и prior units; они не чинятся.

Исторические launcher, bundle и runtime не изменены. Только в диагностической
ветке существующий зарегистрированный workflow native-maintenance-owner-host.yml
заменён отдельным manual branch-only read-only job. Его версия в main остаётся
прежней. Слияние ветки в main для запуска не требуется и не предлагается:
исторический source guard по-прежнему должен видеть main=8bbc1d6.

## Перед возможным отдельным запуском

Исполняющий канал подготовлен в отдельной ветке и проверен offline/CI;
его первый live запуск выполнен после последующего явного разрешения (см. статус выше). Нельзя запускать скрипт через
обход no-new-privileges RDC, менять его защиту или переносить секреты в аргументы,
файлы проекта, отчёт либо журналы. Канал использует прежний pinned SSH host key, secrets существующего environment
database-production, sudo только через штатный SSH и закрытый stdin. RDC и его
защита не меняются. Ни один секрет не входит в remote command или артефакт. Runner использует неизменённый original lifetime.managed_stage: PID1 ограничивает
жизнь transient service 140 сек, KillMode=control-group, Restart=no. Внутренний
SIGALRM — 120 сек, SSH timeout — 170 сек. Standalone CLI имеет SIGALRM 180 сек
и также требует реальную matching managed_stage unit identity.
Не считать SIGALRM заменой внешнего ограничения процесса.

Интерфейс для такого будущего канала: Python 3.12 ARM64 с флагами `-I -B -S`,
`--source-bundle` указывает на точный старый исходный пакет, `--driver-bundle` —
на пакет pinned wheels из оригинального driver. Установка или починка driver
не производится. Секретный stdin — JSON с ровно `credential` (owner DSN из
LIGHT_MAINTENANCE_DATABASE_URL) и `token` (ограниченный GitHub read token).
Helper должен быть отдельно проверен по SHA256 из принятого commit этой ветки.

Перед каждым запуском нужны свежие проверки main/HOLD/отсутствия другого окна.
Разрешение на ограниченный диагностический канал уже получено. Подготовка этой ветки не является разрешением
исполнить её на production. Новое исключительное окно и mutating restore сюда
не включены.

## Проверка подготовки

`python -m unittest -q tests.test_light_after_probe_20260928`

Offline tests: оригинальный bundle и digest, изолированные импорты, отказ при
подмене bundle/загруженном чужом ops, обязательный read-only до исходных запросов,
закрытие сессии при отказе, сокрытие секретных exception text, fail-closed порядок
workflow/prior-host/backend/snapshot, отсутствие live доступа без isolation.
Первоначальные 14 offline tests PASS, включая дополнительные проверки manual branch context,
live run/jobs/main guard, привязки/формата отчёта, bounded bootstrap и его реального
изолированного subprocess отказа без секретов. Внешние наблюдения в тесте порядка
заменены заглушками; это не live attestation.
Первый live запуск подтвердил ARM64 driver/Oracle исполнение; локальные тесты его не заменяют.


## Подготовленный канал: manual dispatch только из диагностической ветки

Файл workflow уже зарегистрирован в main, но при будущей явно разрешённой
workflow_dispatch выбирается ref `fix/light-after-readonly-probe-20260928`.
Pull request запускает только contract job без secrets; production probe job
жёстко требует workflow_dispatch, точную ветку, owner/triggering owner и принятую
версию кода. Автоматического production запуска после commit/PR нет.

Используется единственное существующее input-поле `expected_main_sha`.
**Только в этой ветке** его описание и смысл — полный независимо принятый SHA
диагностического commit (ACCEPTED_PROBE_SHA). Это сохраняет совместимость schema
зарегистрированного workflow. Он обязан совпасть с GITHUB_SHA/WORKFLOW_SHA и
GitHub API run head_sha. Для проверки настоящего main отдельно передаётся
константа EXPECTED_MAIN=8bbc1d61010ef70c3fca02b5151ac86fce02a144. Никакой общий
source guard не ослаблен. Не передавать SHA диагностической ветки как новый main.

Runner повторно проверяет authenticated API run/jobs/main до и после SSH.
Результат привязан к commit/run/attempt/helper digest. Две прежние concurrency
группы исключают параллельные maintenance workflows; на самом host оригинальный
StageSupervisor проверяет собственную PID1 unit и отсутствие другого native
supervisor до и после наблюдений. Identity здесь — фактические run_id/attempt
диагностики, не RunBinding и не разрешение maintenance stage.

Подготовка создаёт/меняет только файлы отдельной ветки. Будущий запуск создаст
и удалит собственную временную systemd unit; production worker не перезапускается,
не меняется, HOLD не снимается. Это ограниченное обратимое host-действие, которое
надо явно включить в разрешение на один diagnostic dispatch. Restore и новое
30-минутное окно в это разрешение не входят.

## Дополнение: точные run identities под существующими блокировками

Диагностика использует inspector-style чтение: существующий store lock,
VERSION, принятый manifest digest, точный набор двух prior units, затем
оригинальный Journal(create_lock=False) для operation/pause. Проверяется BOUND
и полное равенство каждого event.run одному из принятых prior units (включая
job_id/attempt). До и после остальных live проверок pair digest должен совпадать
с ранее наблюдавшимся 9d427c66…; все три блокировки удерживаются в этот период.
Отсутствующий lock вызывает отказ и не создаётся. Нет записи файлов/claim/SQL.

Runtime прямо запрещает вызывать locked_scope из диагностики: такой вызов и
копирование его codeobject отвергнуты. Новый helper не конструирует stage packet
и не вызывает runtime.locked_scope. Это проверка аналогичных условий наблюдением,
а не выполнение исходного stage. PASS по-прежнему не устанавливает историческую
причину отказа и не разрешает restore.

16 offline tests PASS: дополнительно проверены точные recorded run identities,
отказ при missing lock без создания файла, неизменность fixture и освобождение
первой блокировки при отказе второй. Fixture подменяет trust/mount и pair parsing;
результат не является проверкой production mount или полной live журнала.
