# Compilation partition 01

Status: ACTIVE WORKING GATE

## Lane A — safe to compile structurally now

A source line may enter Lane A only if all of its executable predicates use:

- exact auction path from the PDF;
- mechanical call/suit normalization;
- explicit numeric range with resolved `+` lower-bound notation;
- explicit suit length using `+` notation (not bare 6/7/8);
- explicit printed shape family rather than generic `balanced`;
- source-scoped semantic tags whose branch is fully printed;
- no unresolved priority against another applicable call.

Lane A permits creation of candidate rule objects and abstract tests. Card-level strength tests remain blocked until OP-007 point-count method is fixed.

## Lane B — source-faithful candidate only

Use when the source line is clear as text but one semantic operator remains undefined, e.g. generic balanced, fit, splinter, stopper, good suit, playing tricks, bare preempt length. Preserve the term and provenance; `activation_eligible=false`.

## Lane C — explicit conflict/gap

Use when two source-derived actions overlap or opening/response priority is missing. Required runtime behavior after infrastructure activation: `conflict` or `gap`, never arbitrary rule order.

Current Lane C fixtures include:

- 1NT versus five-card-major opening;
- incomplete equal-major/minor opening priority;
- 1♣ response overlaps 1NT/2♣, 2NT/3♣, 3NT/2♦;
- multiple qualifying 5+ game-forcing suit responses.

## Test policy

Every eventual active rule requires at least:

- provenance/source-location assertion;
- positive applicability test;
- negative applicability test;
- lower/upper boundary test where numeric;
- overlap/conflict test against neighboring rules;
- hidden-information test for any inference used after the call.

No test may mark a card-level point boundary PASS while OP-007 remains unresolved.

## Activation policy

Lane A is **not** equivalent to active canon. It means 'safe to compile further'. Activation remains blocked until infrastructure is deployed, required tests pass, conflicts are closed, and every operator used by that specific rule is resolved.