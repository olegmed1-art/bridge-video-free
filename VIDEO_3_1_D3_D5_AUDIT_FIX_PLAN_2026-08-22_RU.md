# Video 3.1 — remediation по аудиту D3–D5

Дата: 2026-08-22

Этот change set не ослабляет evidence gates и не выполняет тяжёлую повторную обработку пользовательских видео.

## Findings

1. **Board reconstruction coverage.** На D3/D4/D5 report-visual v4.2 сформировал 3/5/4 partial-board clusters и 0 VERIFIED_FULL_BOARD. Это честный fail-closed результат. Нельзя повышать partial board до full через complement hidden hands, время, тему или номер Board.
2. **Metric provenance.** Агрегированный `complete_learning_interactions` может включать complete interactions разных поколений quality logic, тогда как `transcript_decision_window_complete_interactions_v4_1` отражает только evidence-linked v4.1 windows. Summary обязан показывать оба счётчика с происхождением, а не создавать видимость противоречия.
3. **Readiness semantics.** `METHODOLOGY_READY` означает достаточность доказанных interaction для разрешённого методического анализа, а не полную реконструкцию всего урока. Summary должен показывать coverage ratio и partial count.
4. **Deprecated knowledge wording.** `promotable_knowledge_candidates` остаётся compatibility alias и не означает authority на promotion. Пользовательский summary не должен показывать alias как самостоятельный promotable показатель.

## Remediation

- добавить в v4.2 summary отдельный `Evidence coverage / provenance` блок;
- показывать evidence-linked complete count, total complete count, partial count и coverage ratio;
- при расхождении total/evidence-linked выводить явную provenance note вместо молчаливого неоднозначного числа;
- скрыть `promotable_knowledge_candidates` и `promotable_knowledge_candidates_deprecated_alias` из human-facing summary, сохранив их в JSON для backward compatibility;
- показывать board reconstruction status как PARTIAL/VERIFIED с явным 52-card gate;
- не менять алгоритм реконструкции раздач до отдельного доказательного улучшения parser/vision path и field gate.

## Safety boundary

Этот PR не должен:

- реконструировать East/West по дополнению колоды;
- объединять board states только по времени/теме/номеру;
- повышать METHODOLOGY_READY до утверждения о полном покрытии урока;
- активировать canon/curriculum/profile writes;
- перезапускать heavy video/ASR для D3–D5.
