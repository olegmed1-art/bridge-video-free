# Independent I2 — authored spot-establishment exercises

**PASS for the four frozen local candidate exercises and their offline prompt projection.** No blocker found in the reviewed candidate. This is mathematical/legality/privacy assurance, not canon activation, publication approval, automatic learner assessment or evidence of learning effectiveness.

Reviewed candidate SHA256: `e3f0710d1bba8dc17b136395197cf2dc4b7a6c24d00be4c78c4d883e69897090`.

| Exercise | Guaranteed target-card tricks, exact fixed deal | Maximum NS tricks in remaining position |
|---|---:|---:|
| spot-length-c-entry-v1 | 1 | 5 |
| spot-length-c-no-entry-v1 | 0 | 4 |
| spot-length-d-entry-v1 | 1 | 5 |
| spot-length-d-no-entry-v1 | 0 | 4 |

## Independent method

`independent_i2.py` imports only Python standard-library modules and reads the authored JSON. It does not import or invoke the author's generator, verifier, snapshot world generator or any bridge engine. Four 52-bit hand masks represent ownership. A memoized recursion enumerates every legal card at every turn; NS maximize, EW minimize. A target trick counts only when a target card is the winning card, not merely played or discarded on a partnership trick. NT winner, following suit, seat order and next leader are explicit rules.

Two objectives are solved separately for each complete-information five-card position: target-card tricks and all NS tricks. This gives 8 exhaustive core solves, 46,668 visited states and 64,224 explored edges. Two additional exact residual solves after prematurely cashing the heart entry both give target value 0, adding 2,440 states. Explanatory optimal lines are stored, but conclusions come from the complete recursion, not from one selected line.

Seven primitive law checks pass, including an off-suit ace losing in NT, mandatory following suit, legal discards and winner-to-leader rotation. Sixteen deliberately corrupted prefix variants are rejected: wrong opening leader, wrong declared winner, a duplicated/not-held card and a revoke for each exercise.

## Deal, history and arithmetic

All four full deals contain exactly the standard 52 distinct cards, thirteen per seat. The combined PBN seat order is N/E/S/W. Each eight-trick prefix has 32 legal plays with correct ownership, following suit, leader and declared winner. West opens against South declarer. The prefix leaves exactly the declared twenty-card position, five cards per hand, with South to lead trick nine. The earlier South-opening draft is outside this PASS; only the pinned corrected candidate is reviewed.

All four authored good/counterexample continuations and both premature-entry counterlines replay legally to completion and have the stated target/partnership counts. The four ready winners are target-suit A/K plus the heart ace and the side-suit ace. The spade three is a loser in these fixed positions.

For each target suit, the full deal has 7 NS cards and 6 defender cards, split 3–3. Four target-suit cards have been played; the remaining position has 5 NS cards and 4 defender cards, split 2–2. The public list of played target cards matches the prefix. These are different time slices, not competing counts.

The excluded old `A863 + QJ52` example has 8 own cards and 5 missing. Splits 3–2 and 4–1 are valid examples; 3–3 and 4–2 are arithmetically impossible for five missing cards. The candidate marks the old answer key `use_as_gold=false`. No old simulated-tester result was used as evidence of student ability.

## Guarantee and information boundary

The prompt explicitly supplies the defenders' remaining target-suit split 2–2 and the played target cards. It does not ask the learner to invent that split.

For each positive position, an information-independent plan was checked against all legal defender plays across all 6 assignments of the four target honors and all 20 assignments of the six off-suit control cards: **120 layouts and 5,760 complete defense lines per target suit**, 240 layouts and 11,520 lines overall. Every line yields one target trick and five NS tricks.

The plan is A then K of the target suit, following low from North; heart two to North's ace; North's remaining target seven while South discards the spade three; then North's side-suit two to South's ace. Its bridge justification also does not depend on locating the unseen off-suit cards: the supplied 2–2 lengths exhaust both defenders' target cards, the two aces control the transfers, and there are no trumps. The enumeration uses the authored off-suit card set; it is not an enumeration of every unseen card set in the whole deck.

For each negative prompt, the private full deal is a consistent counterexample with exact target maximin 0. This is sufficient to refute a guarantee based only on the public information. It does **not** assert that every possible hidden layout prevents a spot trick, nor that every consistent layout has total maximum 4. The value 4 in the table is exact for the authored complete-information position. No perfect-information strategy is silently presented as an information-limited universal strategy.

The premature-entry explanation is supported beyond a single bad line: after the specified early heart trick, exhaustive residual play cannot guarantee a target trick on that fixed deal.

## Privacy and authority

`student-review-prompts.json` is exactly a prompt-field allowlist with neutral IDs `spot-review-001` through `004`. It includes only the intended South/North hands and stated public information. It contains no expected solution, rubric, full hidden deal, setup evidence, private position, provenance, arithmetic answer block or target-card answer key. Author IDs containing entry/no-entry are not copied. Projection is marked `OFFLINE_REVIEW_ONLY`.

Authority remains `CANDIDATE_RESEARCH`; canon/curriculum activation, profile writes, publication and automatic scoring permissions remain false. Pedagogical validity and an active rule binding have not been independently established here. The two C/D pairs are suit-isomorphic teaching contrasts, not four independent empirical samples.

## Reproduce and evidence identity

```powershell
& 'C:\Users\Oleg\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' 'C:\Users\Oleg\Documents\Codex\2026-10-12\task\evidence\spot-card-exercises\independent_i2.py'
```

- Solver SHA256: `b9a9cf1315a5539f0ac978637a5bd86a46b88b2a0fe7ddf2928ba0690ada6c59`.
- Projection SHA256: `95700a25a353eeef63dbab0fae6305c0418e052843684cbbd7d36279218bb0d9`.
- Detailed counts, exact values, optimal lines and residual proofs: `independent_i2_results.json`.

The solver pins the candidate hash and refuses changed input pending re-review. All review writes stay in `independent_i2*` files in this local exercise folder. No remote mutation, IBM/V2, database, profile, adapter or site operation occurred. I2 is supported by the independently implemented exact solver and adversarial checks, not merely a second prose reading or a claim about unobservable model provenance.
