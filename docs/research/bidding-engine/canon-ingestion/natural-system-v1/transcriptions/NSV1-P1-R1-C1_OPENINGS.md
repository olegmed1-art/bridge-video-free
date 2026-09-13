# Verified transcription: NSV1-P1-R1-C1 - Открытия

Source: `school-canon-bidding-natural-system-v1`  
Page: 1  
Grid cell: row 1, column 1  
Verification method: 300 DPI visual crop checked against embedded PDF text  
Authority: source PDF, not the machine extraction

## Exact source text

> **Открытия**
>
> 1♣ = 12-22 от 3♣ при 3♣3♦, при 4♣4♦ лучшая по качеству, нет 5 карт в ♥ и ♠
>
> 1♦ = 12-22, 3♦+, 3♦ только в раскладе 4432, нет 5 карт в ♥ и ♠
>
> 1♥/♠ = 12-22, от 5♥/♠, 1♥ отрицает 5♠
>
> 1NT = 15-17 любые 5332, 5m422, 6m322
>
> 2♣ = Форсинг гейм, 23+ равномера или сильная масть, 8+ взяток с руки
>
> 2♦/♥/♠ = 7-11, 6♦/♥/♠, полублоки
>
> 2NT = 20-22 любые 5332, 5m422, 6m322
>
> 3♣/♦/♥/♠ = сила до открытия, блоки, 7 карт
>
> 3NT = AKQxxxx в миноре, без задержки сбоку
>
> 4♣/♦/♥/♠ = сила до открытия, блоки, 8 карт

## Transcription decisions

- Suit symbols are preserved as printed.
- `NT` is preserved in the source transcription; runtime normalization to `1NT`, `2NT`, `3NT` is mechanical only.
- Source shorthand `m` in `5m422` and `6m322` is preserved and has not yet been expanded in the authoritative transcription.
- `AKQxxxx` is preserved exactly as printed.
- Phrases such as `лучшая по качеству`, `сильная масть`, `полублоки`, `сила до открытия` and `без задержки сбоку` remain source terminology. They are not silently replaced with outside definitions.

## Candidate decomposition plan

The shared lines will be decomposed into separate action candidates for:

`1♣`, `1♦`, `1♥`, `1♠`, `1NT`, `2♣`, `2♦`, `2♥`, `2♠`, `2NT`, `3♣`, `3♦`, `3♥`, `3♠`, `3NT`, `4♣`, `4♦`, `4♥`, `4♠`.

Candidate decomposition does not itself activate canon.

## Open semantic questions found during formalization

These are not transcription defects. They are places where a runtime operator must be defined without changing the source meaning:

1. How `лучшая по качеству` is calculated when clubs and diamonds are 4-4.
2. Whether a printed suit length `6`, `7` or `8 карт` means exactly that length or at least that length in every applicable opening.
3. The formal boundary of `сильная масть` in the 2♣ opening.
4. The formal hand-evaluation method for `8+ взяток с руки`.
5. The exact numeric/structural predicate represented by `сила до открытия`.
6. The operational definition of `полублоки`.
7. The stopper test represented by `без задержки сбоку`.
8. Whether `любые 5332` includes every five-card suit exactly as written; no restriction is added at this stage.

Until resolved, affected candidates remain non-executable or use an explicit unresolved predicate. They must not be activated by guessing.
