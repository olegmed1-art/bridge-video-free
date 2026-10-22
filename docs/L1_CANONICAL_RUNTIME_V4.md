# SCHOOL_L1_DB_V1 — canonical runtime v4

Runtime v4 is the final autonomous source-explicit compilation wave over the current 111 ACTIVE L1 domain rules.

## Coverage

- v2 bounded rules: 40
- v3 additional source-explicit rules: 40
- v4 additional procedural/structural/guidance rules: 30
- total bounded ACTIVE domain rules: 110 / 111

The sole known ACTIVE domain rule left deliberately unbounded is:

`RULE-L1-COMPETITIVE-STRENGTH-PRINCIPLE`

Its canonical meaning says that increased competitive aggression needs sufficient strength/values and must account for penalty risk, but the source does not define a deterministic sufficiency threshold or action. Encoding one would invent methodology/system content, so runtime continues to return `KNOWN_RULE_NOT_EXECUTABLE` for that rule.

## Boundaries preserved

- no PARTIAL/BLOCKED Skill promotion;
- no Lesson 15 signaling code inference;
- no Lesson 16 invention;
- no tournament-system inheritance into L1;
- no natural-language execution at runtime;
- no Neon migration/sync, production deployment, Drive mutation, DDS/network call, or person-specific Student Profile write.

This is therefore complete to the current source boundary: further semantic execution requires a new approved source or teacher decision for the remaining qualitative rule.

## Observation and resolver correction, 2026-10-22

`l1-canonical-runtime-v4-observation-safe-v1` applies the compared local context
correction to main `a993b69d931f88f84fac0c2dd3d02592f3d2e5ee`, with a typed
semantic comparison of tied actions. This is an ASSURED technical correction;
canonical rule IDs, ranges, meanings and ranking weights are unchanged.

- Public evaluators v1 through v4 read explicit observations. Missing values
  are unknown, while measured integer zero and boolean false remain valid.
  Integers reject booleans, strings and floats. HCP is evaluated only by rules
  that need it. Profile isolation precedes input parsing.
- The existing competitive NT stopper constraint filters explicit NT proposals
  before specificity/scope/priority ranking. Unknown constraint context withholds
  NT proposals too. Explicitly inapplicable constraints and reference-only or
  unimplemented BLOCK results do not veto unrelated suit/procedural actions.
  The resolver uses a finite set of existing NT action tokens, not substrings.
- Trick evaluation accepts T and 10 as the same physical rank, including suit
  glyphs, rejects duplicate physical cards and malformed/incomplete tricks, and
  retains the winning card's supplied spelling. It does not prove full-deal legality.
- Tied action dictionaries compare without insertion-order sensitivity. Scalar
  types, list versus tuple, sequence order and nested value types remain distinct.
  Unsupported objects and non-finite floats cannot establish agreement.

`tests/test_l1_observation_contract.py` exercises the public seams. Three existing
positive fixtures now supply previously omitted observations (zero shortness
counters and exclusion of the earlier own-suit branch); their expected actions
are unchanged. The incomplete versions are negative regressions.

The L1 v4 CI runs this regression chain when any underlying L1 runtime changes.
This correction does not install a Teacher UI, expand FULL_CALL, register Canon,
or activate a deployment. The production smoke scope gate remains unchanged;
its historical exact-delta exemption does not authorize this new delta. Release
remains HOLD pending integration reconciliation and a separately authorized release.

Rollback before release is to close the unmerged PR. After an authorized merge,
revert this correction as one unit; do not mix the strict wrappers with an older
base evaluator. No database or user-data rollback is required by this code delta.
