# Полная система управления Школой спортивного бриджа — v1.0 RC1

Статус: **PROPOSED / NOT YET CANONICAL**  
Версия: **1.0-rc1**  
Владелец governance: **Директор Школы спортивного бриджа**  
Делегированный исполнительный управляющий: **AI Management System / ChatGPT-assisted tooling**  
Предлагаемый преемник: `docs/governance/SCHOOL_GOVERNANCE_OPERATING_MODEL.md` после отдельного утверждения  
Дата пересмотра после пилота: определяется при утверждении

## 1. Назначение

Эта модель определяет, как Школа спортивного бриджа:

- выбирает стратегические направления;
- управляет портфелем программ, проектов, исследований и постоянных сервисов;
- распределяет полномочия между директором и AI-управляющим;
- проводит исследования и независимую проверку;
- управляет каноном школы и мировым знанием;
- измеряет технический, бриджевый, педагогический, финансовый и операционный результат;
- сохраняет состояние и evidence вне отдельных чатов;
- изменяет саму систему управления.

Модель не является штатным расписанием. Coordinator, Curator, Observatory, Red Team и другие роли — логические функции. Они могут исполняться отдельными людьми, агентами, моделями, инструментами, проверочными проходами или автоматическими задачами.

## 2. Иерархия управления и документов

При конфликте применяются в следующем порядке:

1. применимые правила платформы, безопасности, закона и владельца аккаунта;
2. последнее явное решение директора для того же scope;
3. утверждённый School Governance Charter / настоящая модель после активации;
4. специализированные политики канона, портфеля, сервисов, данных, исследований и технического управления;
5. `AGENTS.md`;
6. project-state, ADR, планы, аудиты и исторические материалы.

Исторический документ не становится текущей политикой только потому, что он подробный.

## 3. Основные принципы

1. **Директор отвечает за направление и смысл; AI отвечает за реализацию.**
2. **Автономия пропорциональна проверяемости.** Чем больше автономия, тем выше обязанность сверять реальное состояние, хранить evidence и обеспечивать rollback.
3. **Школа управляет ценностью, а не количеством задач.** Проект оправдан только если он создаёт способность, устраняет риск или даёт измеримую пользу.
4. **Проекты и постоянные сервисы управляются раздельно.** Проект заканчивается; сервис продолжает работать.
5. **School Canon и World / External Knowledge не смешиваются.** Внешнее знание может создавать кандидата, но не становится каноном молча.
6. **Технический PASS не равен бриджевой правильности, каноничности или педагогической пользе.**
7. **Отсутствие evidence не превращается в PASS.** Допустимы `INCONCLUSIVE` и `STOPPED`.
8. **Actual state имеет приоритет над памятью чата и устаревшей документацией.**
9. **Контроль пропорционален риску.** Мелкие обратимые задачи не должны проходить полный исследовательский ритуал.
10. **Критические решения должны быть восстанавливаемы.** Решение, источники, версии, тесты и состояние хранятся вне чата.
11. **Новые существенные расходы и внешние обязательства требуют решения директора.**
12. **Governance сама подлежит versioning, Red Team и пересмотру.**

## 4. Полная организационная схема

```text
ДИРЕКТОР ШКОЛЫ
стратегия • смысл • существенный канон • расходы • внешние обязательства
                         │
                         ▼
AI MANAGEMENT SYSTEM
портфель • архитектура • исследования • педагогические системы
технологии • данные • операции • надёжность • FinOps
                         │
          ┌──────────────┴──────────────┐
          ▼                             ▼
ПОРТФЕЛЬ ЦЕННОСТИ               ПОРТФЕЛЬ СЕРВИСОВ
программы / проекты             постоянные возможности школы
исследования / эксперименты     обучение / канон / видео / аналитика / IT
          │                             │
          └──────────────┬──────────────┘
                         ▼
            ТРИ ЛИНИИ УПРАВЛЕНИЯ РАБОТОЙ

1. DELIVERY           2. RISK & QUALITY       3. ASSURANCE
Coordinator           Curator                 Red Team
Implementer           Observatory             independent model/tool
Research Lab          Data Steward            formal checker
Specialists           Security / FinOps       external expert if needed
Learning Quality
                         │
                         ▼
                    EVIDENCE GATE
                         │
                         ▼
                AI TECHNICAL DECISION
                         │
        директору только при director-level boundary
```

## 5. Полномочия директора и AI-управляющего

### 5.1. Директор школы

Директор является:

- владельцем школы;
- владельцем стратегии;
- главным бриджевым экспертом;
- accountable owner канона;
- владельцем существенных финансовых и внешних обязательств.

Директор решает:

- стратегическое направление школы;
- существенные изменения смысла торговой системы и методики;
- материально новые расходы, подписки и платные функции;
- юридические, billing, ownership и account-level действия;
- публикации и обязательства перед внешними лицами, если нет standing authorization;
- необратимые решения с существенным риском потери данных или собственности;
- материальную нерешённую бриджевую неоднозначность.

### 5.2. AI Management System

AI-управляющий автономно отвечает за:

- перевод целей директора в программы, проекты и сервисы;
- портфель, приоритеты и capacity;
- архитектуру, код, базы, интеграции и инфраструктуру;
- исследования и Research Lab;
- педагогические системы и аналитику;
- надёжность, безопасность, recovery и FinOps;
- evidence, Red Team, Observatory и project-state;
- исправления, тесты, рефакторинг и эксплуатацию;
- подготовку директорских decision packs.

Рутинная техническая и исследовательская работа не возвращается директору.

## 6. Управление каноном: точная граница полномочий

Директор остаётся accountable owner канона. AI действует как delegated canon steward.

### 6.1. Автономное обслуживание канона без изменения семантики

AI может автономно:

- исправлять опечатки и формат;
- восстанавливать и усиливать provenance;
- структурировать уже утверждённое правило;
- добавлять тесты и machine-readable representation;
- устранять технические дубликаты;
- переносить знание между схемами без изменения смысла;
- помечать устаревшее правило `SUPERSEDED` или `RETIRED`, если преемник уже явно утверждён;
- исправлять очевидную техническую ошибку, не меняющую бриджевое соглашение.

### 6.2. Семантическое изменение канона

Требует director-level решения, если включает:

- новое соглашение или конвенцию;
- изменение значения заявки;
- изменение диапазона силы, длины, forcing/alert semantics;
- изменение приоритета между допустимыми заявками;
- отказ от ранее действующего правила;
- выбор между несовместимыми источниками;
- изменение уровня курса или педагогической философии;
- интерпретацию, которая не следует однозначно из утверждённого источника.

AI подготавливает evidence pack, альтернативы, последствия и рекомендацию. Внешняя практика не активируется как School Canon автоматически.

## 7. Два портфеля школы

### 7.1. Портфель ценности

Содержит:

- стратегические программы;
- проекты;
- исследования;
- эксперименты;
- значимые улучшения;
- обязательные изменения.

Каждый объект должен иметь:

```text
work_id
title
work_class
strategic_rank
urgency
problem_statement
benefit_hypothesis
target_users
expected_capability
success_metrics
baseline
target
confidence
owner
current_state
next_decision
next_action
blockers
dependencies
capacity_needs
cost_boundary
risk_level
reversibility
source_of_truth
evidence_location
benefit_review_date
kill_criteria
completion_criteria
```

### 7.2. Портфель сервисов

Содержит постоянные возможности школы, например:

- Student Learning Service;
- School Canon Knowledge Service;
- Bidding Engine Service;
- Research Lab Service;
- Tournament Analysis Service;
- Video Processing Service;
- Data and Identity Service;
- Reliability and Recovery Service.

Каждый сервис должен иметь:

```text
service_id
purpose
users
service_owner
service_level
health_metrics
cost
critical_dependencies
data_classes
runbook
backup_and_recovery
last_restore_test
known_risks
improvement_backlog
```

Проект, создающий новую способность, не считается полностью завершённым, пока не определён режим эксплуатации или явное закрытие без сервиса.

## 8. Класс работы, срочность и стратегический ранг

Они не смешиваются в одно поле P0.

### 8.1. Класс работы

```text
INCIDENT
MANDATORY
STRATEGIC
OPERATIONAL
IMPROVEMENT
RESEARCH
```

### 8.2. Срочность

```text
EXPEDITE
HIGH
NORMAL
LOW
```

### 8.3. Стратегический ранг

```text
S1 — ключевая способность школы
S2 — важное развитие
S3 — полезная возможность
```

Пример:

```text
School Canonical Bidding Engine:
class = STRATEGIC
rank = S1
urgency = NORMAL

Production data loss:
class = INCIDENT
urgency = EXPEDITE
```

## 9. Intake и решения портфеля

Новая идея проходит:

```text
цель / проблема
→ проверка дублей и зависимостей
→ классификация
→ benefit / risk / cost / capacity analysis
→ ACCEPT / PARK / MERGE / REPLACE / REJECT
```

Новая формулировка из чата не создаёт новый проект автоматически. Она может изменить существующий scope или заменить прежнюю постановку.

## 10. Capacity и WIP

Школа управляет не только количеством активных задач, но и дефицитными ресурсами:

```text
director_attention
AI_engineering_capacity
research_compute
data_curator_capacity
production_change_capacity
human_bridge_review_capacity
budget_capacity
```

WIP-лимиты устанавливаются по реальному capacity и пересматриваются по фактам. Стратегические проекты могут идти параллельно, если не конкурируют за один и тот же дефицитный ресурс.

## 11. Режимы управления работой

### LIGHTWEIGHT

Для низкого риска, полной обратимости, отсутствия канона, персональных данных, production и новых расходов.

```text
выполнить → проверить → сохранить результат
```

### STANDARD

Для обычного проекта или значимого изменения.

```text
scope → plan → execute → test → checkpoint → review
```

Curator и Red Team могут быть короткими отдельными проходами.

### ASSURED

Обязателен для:

- новых core algorithms;
- canon-affecting research;
- production migrations;
- recovery proof;
- benchmark claims для выбора модели;
- существенного privacy/provenance/hidden-information риска;
- дорогих или труднообратимых изменений.

```text
Coordinator
→ Curator evidence contract
→ Research / Implementation
→ Observatory
→ Red Team
→ Evidence Gate
→ Decision
```

### INCIDENT

Ускоренная отдельная процедура:

```text
detect → contain → recover → verify → postmortem → permanent protection
```

Containment и recovery не ждут полного исследовательского цикла, если ущерб продолжается.

## 12. Три линии управления внутри крупной работы

### 12.1. Delivery

**Coordinator**:

- scope, WIP, dependencies, stop/go;
- разрешённый следующий шаг;
- terminal classification;
- защита от silent scope expansion.

**Implementer / Technical Owner**:

- проектирование и реализация;
- тесты, rollback, deployment и integration.

**Research Lab**:

- воспроизводимые исследования;
- BEN/BBA/Pons/DDS и другие инструменты;
- массовые расчёты;
- evidence и confidence.

**Specialists**:

- database, security, FinOps, bridge engines и другие методы.

### 12.2. Risk & Quality

**Research Curator**:

- фиксирует вопрос, критерии, версии и ограничения;
- защищает от hindsight bias;
- проверяет provenance и соответствие вывода исходному вопросу.

**School Observatory**:

- read-only factual state;
- time, cost, errors, versions, latency, coverage, quality;
- baseline vs attributable change;
- longitudinal history.

**Learning Observatory**:

- learning gain;
- retention;
- error patterns;
- confusion;
- teacher corrections;
- time to mastery;
- transfer to real play.

**Data Steward / Security / FinOps**:

- классификация данных;
- доступ и retention;
- privacy и external-tool boundary;
- стоимость, forecast и budget limits.

### 12.3. Independent Assurance

**Red Team**:

- пытается опровергнуть вывод;
- ищет false PASS, leakage, edge cases, regressions и альтернативные объяснения;
- не оптимизируется на согласие с implementer.

Дополнительные independent checks:

- другая модель;
- solver;
- formal checker;
- внешний робот;
- независимый источник;
- человек-эксперт.

## 13. Уровни независимости проверки

```text
I0 — self-check тем же проходом
I1 — отдельный проход без доступа к первоначальному заключению
I2 — другая модель, solver, алгоритм или formal checker
I3 — внешний независимый робот, источник или технический контур
I4 — человек-эксперт
```

Минимум:

- LIGHTWEIGHT: I0;
- STANDARD: I1 по существенным рискам;
- ASSURED: минимум I2;
- материальная нерешённая бриджевая неоднозначность: I4.

Red Team получает immutable evidence pack, формирует собственный вывод до чтения заключения implementer, а его отчёт не редактируется implementer.

## 14. Уровни зрелости evidence

```text
E0 — предположение / неподтверждённое сообщение
E1 — документированный источник
E2 — воспроизводимый тест или анализ
E3 — независимая проверка / повтор
E4 — проверенный shadow или pilot
E5 — наблюдаемый production или педагогический результат
```

Рекомендуемые пороги:

- архитектурная гипотеза: E2–E3;
- production promotion: E3–E4;
- утверждение педагогической пользы: E5;
- каноническое правило: достаточный источник + provenance + tests + approval/activation.

Evidence Gate проверяет не только наличие, но и уровень evidence, свежесть, релевантность и независимость.

## 15. Разные жизненные циклы

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

### Каноническое знание

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

## 16. Evidence Gate и терминальные решения

Перед значимым решением проверяется:

- актуальны ли первичные источники;
- зафиксированы ли версии и входы;
- достаточно ли evidence level;
- есть ли требуемая independence level;
- воспроизводим ли результат;
- выполнен ли Red Team scope;
- подтверждает ли Observatory фактический результат;
- определены ли rollback и recovery;
- не смешаны ли технический, предметный, педагогический и канонический статусы;
- соблюдены ли cost/privacy boundaries.

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

Coordinator не имеет права превращать отсутствующее evidence в PASS.

## 17. Педагогические gates

Для педагогической системы или изменения методики отдельно оцениваются:

```text
BRIDGE_CORRECTNESS
TEACHABILITY
LEARNING_EFFECT
```

- `BRIDGE_CORRECTNESS`: содержание корректно в заданной системе/версии.
- `TEACHABILITY`: материал пригоден для объяснения целевой группе.
- `LEARNING_EFFECT`: есть наблюдаемое улучшение обучения.

Техническая работоспособность платформы не доказывает ни один из этих трёх результатов.

## 18. Управление данными, приватностью и внешними инструментами

Минимальные классы данных:

```text
PUBLIC
INTERNAL
CONFIDENTIAL
STUDENT_SENSITIVE
CREDENTIAL_SECRET
```

Для каждого класса задаются:

- допустимое хранилище;
- роли чтения/изменения;
- срок хранения;
- backup/recovery;
- допустимость исследований;
- допустимость передачи внешнему сервису;
- правила удаления;
- audit trail.

Секреты, credential data и private student data не размещаются в публичном GitHub или неразрешённых внешних сервисах. Использование нового внешнего платного инструмента требует director approval; бесплатный инструмент также должен пройти privacy/security classification, если получает непубличные данные.

## 19. Финансовое управление

Для проекта и сервиса хранятся:

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

При запросе нового расхода директор получает:

- стоимость;
- ожидаемую пользу;
- бесплатную альтернативу;
- vendor-lock-in риск;
- срок и условия остановки;
- последствия отказа.

Always-on baseline не маскируется как incremental experiment cost и наоборот.

## 20. Память, continuity и source of truth

Чат — интерфейс, а не операционная база истины.

Для каждого крупного проекта:

- `PROJECT_STATE` — короткий индекс текущего состояния;
- tracker issue — программа и история;
- Git — код, policies, ADR и versioned knowledge;
- Neon — structured runtime/research/canon state;
- Drive — исходные материалы и крупные артефакты;
- Observatory — фактические метрики и health;
- evidence store — immutable результаты и receipts.

Перед material action выполняется reconciliation с первичными источниками. После material decision сохраняются checkpoint и change record.

## 21. Управленческий ритм

### Continuous / event-driven

- критический мониторинг;
- incident detection;
- активные compute jobs;
- backup freshness;
- project-state update после material change.

### Еженедельный внутренний AI-review

- WIP и capacity;
- blockers;
- cost drift;
- stale projects;
- service health;
- required director decisions.

### Ежемесячный директорский обзор

Кратко:

1. состояние школы;
2. главные программы;
3. созданная польза;
4. существенные риски;
5. расходы;
6. требуемые решения директора.

### Ежеквартальный стратегический обзор

- цели;
- портфель;
- сервисы;
- канон;
- архитектура;
- learning outcomes;
- финансовая устойчивость;
- проекты, которые нужно остановить.

## 22. Матрица ключевых решений

| Решение | Директор | AI Management | Curator | Observatory | Red Team |
|---|---|---|---|---|---|
| Стратегия школы | Решает | Готовит и исполняет | Проверяет основания | Даёт факты | Проверяет риски |
| Приоритет портфеля | Решает при существенном выборе | Ведёт автономно | Проверяет benefit/evidence | Даёт capacity/cost | Проверяет opportunity cost |
| Техническая архитектура | Информируется | Решает и исполняет | Проверяет provenance | Измеряет | Проверяет крупные изменения |
| Несемантическое обслуживание канона | Информируется | Решает и исполняет | Проверяет источник | — | Выборочно |
| Существенное изменение канона | Решает | Готовит предложение | Проверяет источники | Даёт evidence | Ищет противоречия |
| Research conclusion | Информируется | Принимает technical decision | Проверяет вопрос/provenance | Измеряет | Пытается опровергнуть |
| Production change в утверждённых границах | Информируется | Решает и исполняет | По необходимости | Наблюдает | По риску |
| Новый существенный расход | Решает | Готовит decision pack | — | Даёт baseline | Проверяет альтернативы |
| Внешняя публикация/обязательство | Решает или standing authorization | Готовит/исполняет | Проверяет источники | — | Проверяет риск |

## 23. Governance of governance

Фундаментальное изменение этой модели проходит:

```text
PROPOSAL
→ IMPACT ANALYSIS
→ RED TEAM
→ DIRECTOR APPROVAL
→ VERSIONED ACTIVATION
```

Документ должен иметь:

```text
version
status
effective_date
owner
review_date
supersedes
change_log
approved_exceptions
```

Мелкие технические уточнения, не меняющие распределение полномочий, могут выполняться автономно с change record.

### Emergency deviation

При активном инциденте допускается временное отступление только для containment/recovery, если:

- действие минимально необходимое;
- риск продолжения выше риска отклонения;
- фиксируется reason и scope;
- после стабилизации выполняются review, rollback/normalization и postmortem.

Emergency deviation не используется для обхода расходных, канонических или account-owner решений.

## 24. Пилотирование и активация

Эта версия является candidate model, а не активным каноном управления.

Перед активацией v1.0 рекомендуется:

1. применить модель к School Canonical Bidding Engine;
2. применить service-management часть к одному постоянному техническому сервису;
3. провести независимый Red Team модели;
4. проверить реальную overhead/capacity стоимость;
5. уточнить определения `material semantic change`, `substantial spend` и минимальные evidence/independence levels;
6. получить director approval;
7. активировать versioned governance и пометить прежнюю модель `SUPERSEDED`.

## 25. Критерии успеха модели

Модель считается работающей, если:

- директор получает только вопросы своего уровня;
- техническая и исследовательская работа идёт автономно;
- активный портфель не расползается;
- проекты создают измеримые способности или закрываются;
- постоянные сервисы имеют owner, health, cost и recovery;
- School Canon не загрязняется мировым знанием;
- крупные выводы проходят независимую проверку;
- missing evidence не превращается в PASS;
- состояние восстанавливается без памяти конкретного чата;
- управление не создаёт больше overhead, чем предотвращаемый риск.
