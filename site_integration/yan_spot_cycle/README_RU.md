# Локальный кандидат маршрута сайта Яна

Маршрут: `/index.php?route=practice/spot-cycle`.
Имеющийся `.htaccess` также направляет `/ru/practice/spot-cycle/`
(и префиксы en/he) в этот front controller. Связь архива с live deployment
не устанавливалась. Новая ветка index.php выполняется до CMS bootstrap;
остальная часть исходного index.php и весь .htaccess сохраняются.

Из архива использованы только два проверенных публичных входных файла.
Сборщик сверяет их с исходными SHA-256 ZIP entries. SQL, конфигурации,
резервные копии, CMS bootstrap и административная авторизация не копируются.

Собрать в **новую** папку из корня репозитория:

```
python -m site_integration.yan_spot_cycle.build_candidate --output /tmp/yan-candidate
```

Для интерактивного локального просмотра в Linux с установленными PHP и
зависимостями учебного backend:

```
python -m site_integration.yan_spot_cycle.preview --output /tmp/yan-preview --spot spot.a7c2 --port 8090
```

Открыть `http://127.0.0.1:8090/ru/practice/spot-cycle/`.
Для второй независимой задачи использовать `--spot spot.f3b8` и новую папку.
Preview создаёт только синтетический частный журнал рядом с web root,
слушает 127.0.0.1 и использует отдельный CLI fixture. Этот fixture и его
допуск не входят в устанавливаемую часть сайта. CMS из архива не запускается.

Подключение настоящего backend: в `include/spot-cycle.backend.inc.php`
вернуть существующий серверный HTTP dispatcher через
`$GLOBALS['bridgeSpotBackendDispatch']`. Он получает `{method,path,headers,body}`
и возвращает `{status,headers,body}`. Пути ограничены существующим
`/school/lessons/spot-cycle/`, его assets, `transport` и `saved-answer`.
Cookie, Origin и X-CSRF-Token передаются прежним проверкам backend;
проверки владельца журнала остаются там же. Общий API token, CMS admin,
новый логин и публичный прокси к loopback preview не используются.
Без такого dispatcher или без действующего допуска backend возвращается 503.
Поэтому локальный preview не означает готовность production identity binding.

Публичный исходный `lesson.ui.json` сохранён без изменений:
`b79ae7ba7b5ce2ad3ad1cfb3377f4b8cb025001961d56f2d108d5534caef320c`.
Сборщик переносит только текст/варианты/поля ввода в существующий UI contract;
оба исходных prompt проверяются на полное совпадение с backend и не меняются.
Для нового текста рассчитываются только новые хеши его представления.
Клиент не получает LESSON_RU, reviewer-only, архив или эталоны.
Актуальный приватный teacher-файл имеет SHA-256
`cdb8ee97d4aeddee058535984eda9059806e5e332ed1907f9dd749ecb2525e84`;
он остаётся материалом преподавателя вне web root и данного пакета.
Backend сохраняет свободные легальные ходы без автоматической оценки:
♦A/♦K/♣A с числом 1 для первой задачи, ♣A/♥A с числом 0 для второй
не превращаются в ошибку только из-за отличия от учебной линии.
Закрытые варианты интерфейса сохраняются как в публичном авторском JSON.
После terminal ACK остаются сохранённый ответ и AWAIT_TEACHER_REVIEW,
`next_assignment=null`. Проверки этой цепочки находятся только в `tests/`
этого модуля; прежние 19 Python + 11 JavaScript тестов повторно не запускаются.

Diff сайта: добавление одной ветки в index.php, два новых PHP include,
один JS bootstrap и два сгенерированных публичных assets. Отдельный
`*.index.patch` создаётся рядом с кандидатом. Откат — исходный index.php
и удаление только этих добавленных файлов. Установка на live не выполнялась.
