# DDS TRAIN: план OracleLight → IBM

Дата: 2026-10-19. Исходники сверены с `f733c943f822cb91e135e6761b6e95c806231f45`.
Статус: **план, запуск заблокирован**. Это не квитанция допуска и не исполняемая
команда. `dispatch_allowed=false`; `coordinator=oracle_light`;
`compute_plane=ibm`; `github_role=light_ci_and_coordination`.

Владелец разрешил существующий IBM и его расходы. Новые ресурсы, ключи и права
не нужны и не запрашиваются. DDS не должен занимать окно текущей Diana20.
GitHub workflow `dds-main-30k-prepare.yml` остаётся выключенным и имеет
безусловный запрет выполнения job; IBM-допуск не снимает этот запрет.

## Что существует сейчас

Проверка выполнена по исходникам, без обращения к IBM и без чтения корпуса:

| Участок | Первичный источник | Ограничение |
| --- | --- | --- |
| OracleLight | `ops/ibm_oracle_watchdog_inventory.py` | Инвентаризация `autopilot-lite-vnic`, не DDS-диспетчер |
| OracleLight → IBM SSH | `ops/ibm_ssh_probe_safe.py` | Существующий закреплённый маршрут и ключ, фиксированная диагностика |
| IBM controller | `ops/ibm_trial_executor.py` | Фиксированные режимы; DDS-режим отсутствует |
| IBM school payload | `docs/ibm_school_compute_ondemand.md`, `ops/ibm_school_worlds_probe.py` | WORLD_GENERATE на 256 раздач; не общая очередь |
| Lease qualification | `ops/ibm_oracle_lease_qualification.py` | Не предоставляет lease, service или Stop authority; watchdog не принят |
| Прежний DDS route | `assistant_lab/DDS3_DIRECT_PATH.md`, `assistant_lab/worker.py` | Neon → Oracle → localhost DDS; не IBM |

Готового универсального DDS-исполнителя OracleLight → IBM пока нет. Параметры
Oracle `CPUQuota=600%`, 3 часа и Oracle-specific 6 OCPU / 12 GB нельзя переносить
на IBM как проверенную ёмкость. Лимиты IBM WORLD_GENERATE (60 секунд на госте,
600 секунд цикла) также не являются принятым DDS envelope.

## Точный исторический TRAIN scope

Источник — `run_main_train_authorized.sh` и сохранённый prediction lock:

| Поле | Зафиксированное значение |
| --- | --- |
| Corpus | 30 000 раздач, seed `20260815` |
| Fresh main | Раздачи 10 001–30 000; 40 000 задач |
| TRAIN | 28 000 задач / 14 000 семейств, 5 folds |
| Shards | 20 всего; TRAIN — индексы 4–17, 14 × 2 000 задач |
| Prediction SHA-256 | `ae946bec73f12bead7ebd6e54947bb6f2d2268798cf08b7b468495f943befe2b` |
| Историческая проверка числа DDS results | `22497 → 50497` |
| Follow-up generation | Выключена |
| Validation / sealed | Закрыты; не входят в scope |

Эти числа идентифицируют прежний объём, **не требуют повторного расчёта** и не
доказывают текущее наличие полного корпуса или результатов. У отменённого
GitHub run сохранились компактная квитанция и lock; упаковка полного состояния
была пропущена. Нельзя восстановить отсутствующие OOF-маркеры догадкой.

## Минимальная следующая работа

1. В разрешённом TRAIN-хранилище сверить manifest и неизменные DDS-факты:
   task/family IDs, пять folds, split, provenance solver, полные оценки ходов,
   отдельное исключение семейств, ранее использованных в sealed. Не читать
   validation/sealed payload. Зафиксировать digest выбранных файлов, source SHA,
   task/family manifest и факт исключения обучавшихся семейств из heldout fold.
2. Если этих фактов достаточно, выполнить **только TRAIN recalibration** с
   исправленной оценкой выбранного хода и строгим OOF input policy. DDS solver
   повторно не запускать. Старые файлы сохраняются; новые отчёты имеют новые
   имена, исходные digest и версии outcome/input policy.
3. Если фактов не хватает, сначала выписать точные отсутствующие TRAIN task IDs.
   Отдельный solver scope должен включать только этот список и неизменный
   prediction manifest. Сам historical scope 28 000 не является таким допуском.
4. Сверить candidate calibration diagnostics, раздельные contract/opening-lead
   и source-baseline группы; затем независимое ревью. Это не принятие навыка
   и не разрешение validation/sealed.

Первый предлагаемый ресурсный предел **для recalibration без DDS**: один процесс,
1 vCPU, `MemoryMax=2 GiB`, 15 минут, до 512 MiB новых выходных файлов, heartbeat
каждые 30 секунд. Это предложение для проверки владельцем IBM, а не сведения о
свободной машине. Превышение любого лимита завершает работу с квитанцией; оно
не расширяет scope и не запускает solver. Исходное хранилище — только чтение.
Для отсутствующих DDS-фактов ресурсный предел пока не определён: его можно
предложить после квалификации адаптера и малого отдельно допущенного canary.

## Координация и техническая приёмка до исполнения

Родитель `01a0fe62-21ed-76ed-9615-939d2ab4e02a` согласует окно с IBM owner
`01a12a8a` и Diana20 owner `01a14e28`. Нужна проверенная квитанция, содержащая:

- существующий instance и принятый DDS/recalibration adapter revision;
- завершение или явную передачу окна Diana20, время начала/истечения lease;
- подтверждённые CPU, MemoryMax, диск, wall-clock deadline, heartbeat и владельца
  остановки только нашей задачи; запрет остановки/изменения Diana20;
- точный source SHA и dataset/task/family/prediction digests, `scope=main_train`,
  допустимое число задач, solver provenance или явное `solver_run=false`;
- одноразовое потребление квитанции, nonce, expiry и атомарную защиту от повтора,
  по существующим `launch_authorization.py` / `authorized_run_stage.py`;
- независимую проверку, что входы не содержат validation/sealed и что метки OOF
  подтверждены producer evidence, а не только самозаявлением в JSON.

До появления этой квитанции и принятого адаптера dispatch остаётся запрещённым,
даже если все поля черновика заполнены. Не создавать сервисы, общую очередь,
новые ключи или права для обхода отсутствующего допуска. Старые GitHub/Oracle
launchers не являются разрешённой реализацией нового маршрута.

## Что уже можно принять отдельно

Малые синтетические тесты проверяют строгие OOF split/marker/identity/family/fold,
сохранение метаданных при экспорте, запрет GitHub preparation и явную BLOCKED
квитанцию наблюдателя. Они не читают реальный корпус и не вызывают DDS.
Принятая Learning v2.3 остаётся базой, v2.5 Stage2B — candidate/testing.
Double-dummy оценивает фиксированную полную раздачу; эти изменения не доказывают
качество человеческого решения при неполной информации.

Откат software patch не разрешает тяжёлый GitHub compute: административная
блокировка и решение о размещении сохраняются. Неизменные DDS-факты и ранее
созданные квитанции не удаляются и не переписываются.
