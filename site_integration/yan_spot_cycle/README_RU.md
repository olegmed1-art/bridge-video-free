# Локальный урок и подключение к сайту Яна

Два независимых назначения: `spot.a7c2` и `spot.f3b8`. Локальный режим
`LOCAL_SINGLE_USER` предназначен для одного оператора компьютера. Это не
авторизация посетителей сайта и не аккаунт ученика школы.

## Постоянный локальный запуск

Нужны Python с `requirements-api.txt` и PHP CLI с HTTP streams. Создание
в **новой** папке через штатный `TeacherSessionStore.create`:

```
python -m site_integration.yan_spot_cycle.create_local --output /path/to/new-local-lesson
python -m site_integration.yan_spot_cycle.run_local --root /path/to/new-local-lesson
```

Открыть `http://127.0.0.1:8090/ru/practice/spot-cycle/`. На странице есть ссылки
на оба уже созданных назначения. Сервер слушает только буквальный loopback,
проверяет Host/Origin, CSRF, владельца и replay журнала. Пути журналов берутся
только из `private/bindings.json`, не из запроса. Назначения и runtime лежат
вне web root `site/`. `private/runtime.json` содержит локальный CSRF nonce:
его не публикуют и не включают в пакеты/CI artifact.

Повторный `run_local` использует прежние файлы. Он не создаёт и не сбрасывает
журналы. Входной `entry.e1` остаётся первым шагом штатного API; заранее
записанных ответов в новых назначениях нет. После ответа на целевое упражнение
и terminal ACK показываются сохранённые три поля и ожидание проверки:
`AWAIT_TEACHER_REVIEW`, `next_assignment=null`. Оценка и mastery не выводятся
из технического прохождения. Переход по второй ссылке открывает отдельное
назначение, не выдуманный следующий шаг первого журнала.

`preview.py` и `tests/backend_fixture.py` — прежние синтетические fixtures;
они не являются постоянным режимом или доказательством готовности сайта.

## Воспроизводимая проверка на разрешённом Linux CI

```
python -m site_integration.yan_spot_cycle.verify_persistent --output deliverables/new-verification
```

Проверка запускает настоящий PHP HTTP server и отдельный uvicorn backend,
проходит страницу и transport для обоих журналов, явно вносит два новых
проверочных входных ответа и два новых целевых ответа. Это автоматические
тестовые записи, не исторические ответы человека. Затем останавливает только
свой backend, запускает его заново с теми же файлами, повторно читает ответы
и проверяет их равенство и неизменность SHA-256 журналов. Копии обоих журналов
снова проходят штатный replay. Artifact содержит только эти SQLite файлы,
привязки, безопасную очередь проверки и отчёт; runtime nonce исключён.
Хранение artifact в существующем CI — один день. Журналы не коммитятся.

На Windows ранее заблокировано локальное socket-соединение. Повторная
диагностика sockets, изменение защиты и повышение прав не нужны для этой
проверки. Локальные назначения можно создать без запуска HTTP.

## Производственный шов

Маршрут `/index.php?route=practice/spot-cycle`; исходный `.htaccess` также
направляет `/ru/practice/spot-cycle/` (en/he) в этот front controller.
Сборка сверяет только два разрешённых публичных исходника архива по SHA-256.
В `index.php` добавляется девять строк до CMS bootstrap. Остальное содержимое
index.php и `.htaccess` сохраняется. SQL, конфиги, .bak, административные
реквизиты и CMS из архива не запускаются и не публикуются.

```
python -m site_integration.yan_spot_cycle.build_candidate --output /path/to/new-site-candidate
```

В PHP хост устанавливает `$GLOBALS['bridgeSpotBackendDispatch']` с контрактом
`{method,path,headers,body}` → `{status,headers,body}`. Допустимы только
фиксированные пути урока и allowlist assets. Передаются Cookie, Origin и
X-CSRF-Token. Python хост устанавливает **один** callable
`app.state.school_spot_binding_resolver(request)`, возвращающий
`(VerifiedSchoolSession, LearnerLessonBinding)`. Он должен проверить реальную
сессию и выбрать принадлежащий ей существующий журнал. Файловый
`FileTrustedJournalSource` выполняет только lookup; не аутентифицирует.
При наличии школьной SQL-модели можно использовать `SchoolSpotReadRepository`
для проверки активного владельца и участия. CMS admin не является учеником.

Обычный `app.py` локальный resolver не подключает. Без доверенного resolver
остаётся HTTP 503. Локальный launcher не входит в web overlay и не является
production fallback. Общий API token не заменяет идентичность ученика.

Для публичного запуска нужны сведения от действующего хостинга:

1. Версии PHP/webserver, доступные extensions, расположение front controller.
2. Способ запуска Python backend и его закрытый адрес за PHP/proxy.
3. Постоянная приватная папка журналов, владелец процесса, резервное копирование.
4. Канонический HTTPS origin и доверенная граница reverse proxy.
5. Действующий проверяющий session/cookie код: subject, срок, отзыв сессии,
   источник CSRF и доверенная таблица принадлежности назначений ученику.

После получения этих данных подключается реальный resolver и dispatcher;
развёртывание требует отдельного разрешённого этапа. Live сайт не менялся.

## Содержание и откат

Публичный `content/lesson.ui.json` сохранён с SHA-256
`b79ae7ba7b5ce2ad3ad1cfb3377f4b8cb025001961d56f2d108d5534caef320c`.
Сборщик проецирует только тексты и варианты в принятый UI contract. Оба prompt
и backend hash остаются прежними. Закрытый teacher файл SHA-256
`cdb8ee97d4aeddee058535984eda9059806e5e332ed1907f9dd749ecb2525e84`
не копируется в web root или пакет. Полные LESSON/reviewer-only/архив исключены.

Откат overlay: восстановить исходный index.php, удалить только добавленные
файлы overlay. Приватные журналы сохраняются отдельно; откат кода не означает
их сброс. Педагогическое ядро и его source pins не изменены.
