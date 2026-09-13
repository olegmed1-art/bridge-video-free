# Полная система управления Школой спортивного бриджа — v1.0

Статус: **ACTIVE / CANONICAL**  
Версия: **1.0**  
Дата вступления в силу: **2026-08-26**  
Владелец governance: **Директор Школы спортивного бриджа**  
Делегированный исполнительный управляющий: **AI Management System / ChatGPT-assisted tooling**  
Первый операционный аудит: **2026-09-25**  
Полный пересмотр: **2026-11-24**

## 1. Назначение

Эта модель определяет, как Школа спортивного бриджа:

- выбирает стратегические направления;
- управляет программами, проектами, исследованиями, экспериментами и постоянными сервисами;
- распределяет полномочия между директором и AI-управляющим;
- управляет каноном школы и мировым знанием;
- проводит исследования и независимую проверку;
- измеряет технический, бриджевый, педагогический, финансовый и операционный результат;
- сохраняет состояние, решения и evidence вне отдельных чатов;
- изменяет саму систему управления.

Модель не является штатным расписанием. Coordinator, Curator, Observatory, Red Team и другие роли — логические функции. Они могут исполняться отдельными людьми, агентами, моделями, инструментами, проверочными проходами или автоматическими задачами.

## 2. Иерархия документов и решений

При конфликте применяются в следующем порядке:

1. применимые правила платформы, безопасности, закона и владельца аккаунта;
2. последнее явное решение директора для того же scope;
3. настоящий `SCHOOL_GOVERNANCE_SYSTEM_V1.md`;
4. специализированные политики канона, портфеля, сервисов, данных, исследований и технического управления;
5. `AGENTS.md`;
6. project-state, ADR, планы, аудиты и исторические материалы.

Исторический документ не становится текущей политикой только потому, что он подробный. `SCHOOL_GOVERNANCE_OPERATING_MODEL.md` является предшественником и считается `SUPERSEDED` в части, где он противоречит этой версии.

## 3. Основные принципы

1. **Директор отвечает за направление и предметный смысл; AI отвечает за реализацию.**
2. **Автономия пропорциональна проверяемости.** Чем больше автономия, тем выше обязанность сверять реальное состояние, сохранять evidence и обеспечивать rollback.
3. **Школа управляет ценностью, а не количеством задач.** Проект оправдан, если создаёт способность, устраняет риск или даёт измеримую пользу.
4. **Проекты и постоянные сервисы управляются раздельно.** Проект заканчивается; сервис продолжает работать.
5. **School Canon и World / External Knowledge не смешиваются.** Внешнее знание может создать кандидата, но не становится каноном молча.
6. **Технический PASS не равен бриджевой правильности, каноничности или педагогической пользе.**
7. **Отсутствие evidence не превращается в PASS.** Допустимы `INCONCLUSIVE` и `STOPPED`.
8. **Actual state имеет приоритет над памятью чата и устаревшей документацией.**
9. **Контроль пропорционален риску.** Мелкие обратимые задачи не проходят полный исследовательский цикл.
10. **Критические решения должны быть восстанавливаемы.** Решение, источники, версии, тесты и состояние хранятся вне чата.
11. **Новые существенные расходы и внешние обязательства требуют решения директора.**
12. **Governance сама подлежит versioning, Red Team и пересмотру.**

## 4. Распределение полномочий

### 4.1. Директор школы / главный бриджевый эксперт

Директор:

- определяет стратегию и приоритетные результаты школы;
- является accountable owner школьного канона;
- решает существенные неразрешённые предметные противоречия;
- утверждает новые существенные расходы, подписки и внешние обязательства;
- выполняет действия владельца аккаунта, которые нельзя делегировать;
- принимает решения с существенным необратимым риском;
- утверждает фундаментальные изменения этой governance-модели.

Директор не должен участвовать в рутинном выборе таблиц, форматов, CI, облачной архитектуры, алгоритмов, мониторинга, резервного копирования и других технических деталей.

### 4.2. AI Management System

AI Management System:

- переводит цели директора в портфель, программы, проекты и сервисы;
- выбирает техническую и исследовательскую архитектуру;
- управляет кодом, базами, инфраструктурой, исследованиями, педагогическими системами, надёжностью, безопасностью и FinOps;
- организует Research Lab, Observatory, Curator и Red Team;
- выполняет автономно обратимые и проверяемые действия в утверждённых границах;
- сохраняет project state, evidence, decision records и recovery paths;
- эскалирует директору только решения директорского уровня.

## 5. Управление каноном

### 5.1. Ответственность

- Директор — **accountable owner** канона.
- AI — **delegated canon steward**.

### 5.2. Автономное несемантическое обслуживание

AI может автономно:

- исправлять опечатки и техническое форматирование;
- восстанавливать provenance;
- структурировать уже утверждённое знание;
- добавлять тесты и контрольные примеры;
- устранять дубликаты;
- выполнять миграции без изменения значения;
- маркировать версии `SUPERSEDED` или `RETIRED`, если утверждённая замена уже существует;
- исправлять техническую ошибку, не меняющую бриджевую семантику.

### 5.3. Семантическое изменение

Решение директора требуется, когда меняется:

- значение заявки или соглашения;
- диапазон силы или длины;
- forcing/alert semantics;
- приоритет между альтернативными заявками;
- действующая система или конвенция;
- существенная педагогическая трактовка;
- выбор между несовместимыми источниками, который нельзя надёжно разрешить evidence.

## 6. Два портфеля школы

### 6.1. Портфель ценности

Содержит программы, проекты, исследования, эксперименты и улучшения.

Для каждого значимого объекта фиксируются:

```text
problem_statement
benefit_hypothesis
target_users
expected_capability
success_metrics
baseline
target
confidence
cost_boundary
capacity_needs
kill_criteria
benefit_review_date
```

Техническое завершение не закрывает benefit review автоматически.

### 6.2. Портфель сервисов

Содержит постоянные способности школы, например:

- Student Learning Service;
- School Canon Knowledge Service;
- Bidding Engine Service;
- Research Lab Service;
- Tournament Analysis Service;
- Video Processing Service;
- Reliability and Recovery Service.

Для каждого сервиса фиксируются:

```text
service_owner
users
service_level
health_metrics
cost
dependencies
data_classes
runbook
backup_and_recovery
last_restore_test
improvement_backlog
```

## 7. Классификация работы

### 7.1. Класс

```text
INCIDENT
MANDATORY
STRATEGIC
OPERATIONAL
IMPROVEMENT
RESEARCH
```

### 7.2. Срочность

```text
EXPEDITE
HIGH
NORMAL
LOW
```

### 7.3. Стратегический ранг

```text
S1 — ключевая способность школы
S2 — важное развитие
S3 — полезная возможность
```

Класс, срочность и стратегический ранг не заменяют друг друга.

## 8. Режимы управления

### LIGHTWEIGHT

Для небольшой, обратимой и безопасной работы:

```text
выполнить → проверить → сохранить
```

### STANDARD

Для обычного проекта или значимого изменения:

```text
scope → plan → execute → test → checkpoint → review
```

### ASSURED

Для канона, core algorithms, production, recovery, крупных исследований, существенных данных/стоимости и высокого риска:

```text
Coordinator
→ Curator
→ Research / Implementation
→ Observatory
→ Red Team
→ Evidence Gate
→ Decision
```

### INCIDENT

Для продолжающегося ущерба или критического сбоя:

```text
detect → contain → recover → verify → postmortem → permanent protection
```

Containment и recovery не должны ждать полного исследовательского цикла.

## 9. Три линии управления значимой работой

### 9.1. Delivery

- Coordinator;
- Implementer;
- Research Lab;
- Specialists.

### 9.2. Risk & Quality

- Research Curator;
- School Observatory;
- Learning Observatory;
- Data Steward;
- Security;
- FinOps;
- Quality controls.

### 9.3. Independent Assurance

- Red Team;
- другая модель или независимый проход;
- solver;
- formal checker;
- внешний робот;
- внешний эксперт, когда требуется.

## 10. Независимые функции

### Coordinator

- фиксирует scope, WIP, зависимости и decision target;
- определяет разрешённый следующий шаг;
- поддерживает stop/go и terminal status;
- не может превратить missing evidence в PASS.

### Research Curator

- фиксирует вопрос, гипотезу, критерии, версии и источники;
- защищает от hindsight bias и изменения критериев после результата;
- отделяет observation от interpretation;
- проверяет provenance и полноту evidence.

### School Observatory

- в предпочтительно read-only режиме фиксирует фактическое состояние;
- измеряет время, стоимость, bytes, retries, версии, health, latency, coverage и другие decision-relevant metrics;
- отделяет baseline от attributable change;
- не принимает решения по канону.

### Learning Observatory

- измеряет learning gain, retention, error patterns, confusion, teacher corrections, time to mastery и перенос навыка в реальную игру;
- не выдаёт корреляцию за доказанную причинность.

### Red Team

- пытается опровергнуть вывод;
- ищет false PASS, hidden-information leakage, edge cases, регрессии, конфликт канона и альтернативные объяснения;
- сохраняет собственный вывод отдельно от implementer conclusion.

### Research Lab

- выполняет воспроизводимые исследования и массовые вычисления;
- использует BEN, BBA, Pons, DDS3 и другие инструменты в разрешённых границах;
- производит evidence, а не автоматически меняет канон или production.

## 11. Уровни независимости

```text
I0 — self-check тем же проходом
I1 — отдельный проход без первоначального вывода
I2 — другая модель, solver, алгоритм или formal checker
I3 — внешний робот, источник или технический контур
I4 — человек-эксперт
```

Минимум:

- LIGHTWEIGHT — I0;
- STANDARD — I1 по существенным рискам;
- ASSURED — минимум I2;
- существенная нерешённая бриджевая неоднозначность — I4.

## 12. Зрелость evidence

```text
E0 — предположение
E1 — документированный источник
E2 — воспроизводимый тест
E3 — независимая проверка
E4 — проверенный shadow/pilot
E5 — наблюдаемый production или learning result
```

Порог зависит от утверждения:

- архитектурная гипотеза: E2–E3;
- production promotion: E3–E4;
- педагогическая польза: E5;
- каноническое правило: источник + provenance + тесты + approval/activation.

## 13. Жизненные циклы

### Исследование

```text
QUESTION
→ HYPOTHESIS_FROZEN
→ RUNNING
→ EVIDENCE_READY
→ RED_TEAMED
→ CONCLUDED
→ ADOPTED / REJECTED / INCONCLUSIVE
```

### Канон

```text
CANDIDATE
→ PROVENANCE_VERIFIED
→ REVIEWED
→ APPROVED
→ ACTIVE
→ SUPERSEDED / RETIRED
```

### Программный продукт

```text
DISCOVERY
→ DESIGN
→ BUILD
→ VERIFY
→ SHADOW
→ PRODUCTION
→ OPERATE
→ RETIRE
```

### Изменение сервиса

```text
REQUESTED
→ ASSESSED
→ PLANNED
→ CHANGED
→ ACCEPTED
→ OBSERVED
→ CLOSED / ROLLED_BACK
```

### Инцидент

```text
DETECTED
→ CONTAINED
→ RECOVERED
→ VERIFIED
→ POSTMORTEM
→ PREVENTION_INSTALLED
```

## 14. Evidence Gate

Перед значимым решением проверяется:

```text
required evidence present?
primary sources current?
versions pinned?
result reproducible?
independence level sufficient?
Red Team risks resolved or accepted?
Observatory confirms actual result?
rollback/recovery available?
uncertainty stated truthfully?
```

Допустимые результаты:

```text
PASS
FAIL
INCONCLUSIVE
STOPPED
NEEDS_BRIDGE_DECISION
NEEDS_OWNER_ACTION
NEEDS_SPEND_APPROVAL
```

## 15. Педагогическое управление

Для существенного педагогического изменения отдельно оцениваются:

```text
BRIDGE_CORRECTNESS
TEACHABILITY
LEARNING_EFFECT
```

Правильная бриджевая теория не гарантирует хорошее объяснение или измеримый результат обучения.

## 16. Данные, приватность и безопасность

Минимальные классы данных:

```text
PUBLIC
INTERNAL
CONFIDENTIAL
STUDENT_SENSITIVE
CREDENTIAL_SECRET
```

Для каждого класса определяются допустимое хранилище, доступ, retention, backup, использование в исследованиях, передача внешним сервисам, удаление и audit trail.

Секреты, credentials и приватные данные учеников не размещаются в публичных или неразрешённых хранилищах.

## 17. Финансовое управление

Для проекта и сервиса фиксируются:

```text
baseline_cost
incremental_cost
recurring_cost
cost_driver
unit_cost
forecast
budget_limit
stop_threshold
```

Запрос нового платного решения директору должен включать:

- цену;
- ожидаемую пользу;
- бесплатную альтернативу;
- срок;
- vendor lock-in;
- условия остановки;
- последствия отказа.

## 18. Capacity и WIP

Управление учитывает не только число задач, но и дефицитные ресурсы:

```text
director_attention
AI_engineering_capacity
research_compute
data_curator_capacity
production_change_capacity
human_bridge_review_capacity
budget_capacity
```

Новая работа не должна молча вытеснять уже принятую стратегическую работу.

## 19. Сохранение состояния

Чат является интерфейсом, но не primary source.

Долговременное состояние хранится в:

- Portfolio Registry;
- Service Registry;
- Project State;
- Decision Records / ADR;
- Evidence Store;
- School Canon;
- World / External Knowledge;
- Observatory State;
- Git history;
- первичных системах GitHub, Neon, Drive и runtime-сервисах.

Перед материальной мутацией состояние сверяется с первичными источниками. Project-state является индексом, а не заменой реального состояния.

## 20. Инциденты и emergency deviation

При продолжающемся ущербе AI может временно отступить от обычной процедуры только для containment и recovery, если действие:

- минимально необходимо;
- не создаёт большего риска;
- сохраняет evidence;
- имеет последующий review;
- не используется для обхода director-level решений.

После инцидента обязательно:

```text
what happened
→ why prevention/monitoring failed
→ remediation
→ regression test
→ automated protection
```

## 21. Изменение governance

Фундаментальное изменение полномочий или принципов:

```text
PROPOSAL
→ IMPACT ANALYSIS
→ RED TEAM
→ DIRECTOR APPROVAL
→ VERSIONED ACTIVATION
```

Мелкие технические уточнения, не меняющие полномочия и смысл, могут выполняться AI автономно с change record.

## 22. Управленческий ритм

- критическое здоровье — непрерывно/автоматически;
- портфель и project state — событийно при значимых изменениях;
- внутренний AI portfolio review — не реже еженедельно;
- директорский обзор — по необходимости и обычно ежемесячно, в кратком decision-oriented формате;
- первый аудит v1.0 — 2026-09-25;
- полный пересмотр v1.0 — 2026-11-24;
- далее — не реже одного раза в полгода и после серьёзного инцидента или изменения архитектуры.

## 23. Активация v1.0

С 2026-08-26:

- настоящая версия является действующей моделью управления школы;
- новые крупные проекты классифицируются по этой модели;
- существующие проекты переходят на неё при следующем существенном этапе или изменении scope;
- малые задачи по умолчанию используют LIGHTWEIGHT;
- новые core algorithms, canon-affecting исследования и высокорисковые изменения используют ASSURED;
- текущие project-specific safety gates сохраняются до отдельного аудита;
- никакая техническая активация не изменяет бриджевый канон автоматически.

Первый 30-дневный аудит и 90-дневный пересмотр обязательны и могут привести к `KEEP`, `ADJUST`, `SIMPLIFY`, `PARTIALLY RETIRE` или `REPLACE`.