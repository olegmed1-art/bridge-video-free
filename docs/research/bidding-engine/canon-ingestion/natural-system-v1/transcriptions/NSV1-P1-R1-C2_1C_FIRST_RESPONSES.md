# Verified transcription: NSV1-P1-R1-C2 - Открытие 1♣. Первые ответы

Source: `school-canon-bidding-natural-system-v1`  
Page: 1  
Grid cell: row 1, column 2  
Verification method: 300 DPI visual crop checked against the complete page  
Authority: source PDF, not the machine extraction

## Exact source text

> **Открытие 1♣. Первые ответы**
>
> 1♦ = Ф1, от 6 очков, 4♦+, если ровно 4♦, то нет 4♥/♠
>
> 1♥/♠ = Ф1, от 6 очков, 4♥/♠+, при 44 говорим 1♥, при 5/4 5ку
>
> 1NT = НФ, 6-10, нет 4♥/♠
>
> 2♣ = НФ 6-10, 5♣+, нет 4♥/♠
>
> 2♦/♥/♠ = ФГ, 13+, 5♦/♥/♠+,
>
> 2NT = ИНВ к 3NT, 11-12, равн, нет 4♥/♠
>
> 3♣ = ИНВ, 11-12, 5♣+
>
> 3♦/♥/♠ = блок, 4-7, 7♦/♥/♠+
>
> 3NT = играть, 13-15, равномер, нет 4♥/♠

## Transcription decisions

- The shared lines `1♥/♠`, `2♦/♥/♠` and `3♦/♥/♠` will later be decomposed into separate call candidates; the transcription itself preserves the printed grouping.
- `Ф1`, `НФ`, `ФГ`, `ИНВ`, `равн`, `равномер`, `при 44`, `при 5/4 5ку` and `блок` are preserved as source terminology.
- The trailing comma printed after the `2♦/♥/♠` line is retained in the quoted source text but does not authorize any missing continuation.
- `нет 4♥/♠` is treated as a source statement about both majors; it has not been broadened to other suits.

## Candidate decomposition plan

The block will produce separate candidates for:

`1♦`, `1♥`, `1♠`, `1NT`, `2♣`, `2♦`, `2♥`, `2♠`, `2NT`, `3♣`, `3♦`, `3♥`, `3♠`, `3NT`.

## Open semantic questions found during formalization

1. The exact machine definition of `очки`.
2. The exact forcing-state transitions for `Ф1`, `НФ`, `ФГ` and `ИНВ`.
3. Whether `4♦+`, `4♥/♠+`, `5♣+`, `5♦/♥/♠+` and `7♦/♥/♠+` use inclusive lower bounds. The plus sign strongly implies this, but the operator will be tested consistently across the whole PDF before activation.
4. The tie-break rule `при 44 говорим 1♥, при 5/4 5ку` must be formalized for every hearts/spades length combination without adding cases not stated by the source.
5. The exact shape domain for `равн` / `равномер`.
6. Whether `блок` adds a quality predicate beyond the explicit 4-7 strength and seven-card suit.
7. Possible overlap between the game-forcing two-level suit responses and the natural/invitational calls must be resolved by explicit strength and suit-length boundaries, not by row order.

Until these operators and conflict tests are complete, the candidates remain non-active.
