# 3.1 FREE internal revisions

## 3.1-free-master-analysis-r5 — stable
- Добавлен отдельный семантический QC бриджевой терминологии после акустического QC.
- Сырой ASR сохраняется отдельно от текста для анализа.
- Добавлен журнал автоматических исправлений и неразрешённых кандидатов.
- Критические исправления, способные изменить бриджевый смысл, не повышаются автоматически до FACT.
- Словарь расширен на основании ошибок в «Диана 8» и «Бридж по воскресеньям. Занятие 4. 23.04.23».
- Production validation: run #8 — success.
- ASR QC: 25/25, 0 failed, anchors 3/3.
- Semantic QC: PASS; применено 10 записанных коррекций, raw ASR сохранён.
- PDF QC: PASS; встроенный master_analysis.json подтверждён.
- Архивная ветка: `archive/3.1-free-r5-stable-2026-08-12`.

## 3.1-free-master-analysis-r4 — stable rollback
- Первый подтверждённый production мастер-анализ.
- Архивная ветка: `archive/3.1-free-r4-stable-2026-08-11`.
- Production validation: run #7 — success.
