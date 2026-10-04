# Independent review of the isolated tournament teacher

Reviewed on 2026-10-04 by a separate agent, without modifying the consumer,
rules, existing tests, repository activation, or external services.

## Semantic assessment

No executable semantic defect was found against the supplied 14 teacher
decision records. The review covered exact versus minimum suit lengths,
conjunctions and alternatives, numeric boundaries, forcing metadata, preserved
companion rules, and missing or unsupported context. All 77 tests in
`test_full_decisions.py` passed during review.

The two new rules retain DRAFT status. The partial TDEC-20261002-011 remains
linked to its clarifying TDEC-20261003-001; it is not treated as a separate
sufficient selection instruction. The unresolved continuation questions stay
outside the executable scope. Unlisted NT shapes, including 4333 and 4432,
are neither silently accepted nor declared universally forbidden.

Synthetic numeric premises are not valuations computed from the cards.
Assessing confirmed conditions does not establish a unique recommended call.
Only the explicit 15-17, 5M332 opening/rebid instruction permits a recommendation.

## Findings and fixes verified

- The earlier unjustified 40-point upper bound was removed. A premise of 41
  is included in the independent recommendation probe.
- The CLI now catches excessive JSON nesting through `RecursionError`.
- The preserved 2H response and 3S rebid initially inherited source locations
  describing adjacent corrected rows. Their locations now identify the
  separate 2H and 3S rows explicitly.
- Those companions initially exposed HCP in `confirmed_conditions` while
  executing synthetic school-point comparisons. Literal source wording now
  stays in `source_row_conditions`; executable `confirmed_conditions` uses
  school points and explicitly records the unresolved counting method.
  No conversion or equivalence to HCP is asserted.

## Reproducible additional checks

Run from the repository root:

```sh
python -m experiments.tournament_teacher.independent_review
```

The standard-library-only oracle reads no network service and writes no files.
It checks all 560 suit distributions of thirteen cards, twelve explicit point
premises, and ten auction contexts: **67,200 decisions**. Its expected choices
are constructed independently from the teacher's explicit instruction, without
reading rule JSON or reusing consumer predicate helpers. The observed result
was **67,164 abstentions, 36 recommendations, and zero policy deviations**.
It also checks that activation, production-readiness, and fallback flags remain
false. Printed hashes identify the actual code and rules used by each run.

A second probe mutates each field of all 26 scenario requests with eleven
malformed values. **2,574 probes produced zero crashes**. This robustness probe
reuses request fixtures and is not claimed as an independent semantic oracle.

## Assurance limits

The semantic review is an I1 separate same-model pass. The finite independent
algorithm adds evidence specifically for recommendation-policy isolation; it
does not independently establish every assessment predicate or source truth.
Enumeration covers all suit distributions at the listed point premises and
auctions, not every possible request, point value, card-rank arrangement, or
auction. Live Sheet/PDF provenance was not independently re-read by this
reviewer; that reconciliation remains the implementing agent's evidence.

This is an offline test-only path. It does not establish production activation,
complete bidding strategy, teaching effectiveness, or readiness to deploy.
