# SCHOOL_L1_DB_V1 — canonical runtime v3

Runtime v3 expands the bounded executable subset of the current L1 Drive canon without changing the canon itself.

## What is added

- 40 additional ACTIVE domain rules whose trigger/condition/action structure is explicit in the canonical `AI — Правила системы` table.
- Numeric bands, classifications and branch gates are encoded literally from that table.
- Existing v1/v2 behavior remains the fallback for previously bounded rules, registry isolation, governance gates and unknown-rule blocking.

## What is deliberately not added

- qualitative/heuristic rules that require bridge judgment not already represented by an explicit input flag;
- any PARTIAL or BLOCKED Skill promotion;
- Lesson 14 provisional cue-bid content beyond already approved explicit rules;
- Lesson 15 attitude/count/suit-preference signaling;
- Lesson 16 content;
- tournament-system inheritance into L1;
- natural-language interpretation of Drive rows at runtime.

Known canonical rules that are still not bounded remain `KNOWN_RULE_NOT_EXECUTABLE` rather than being guessed.

## Safety boundary

This module is pure and side-effect free. It performs no Neon/production write, migration, deployment, Drive mutation, DDS call, network call or person-specific Student Profile write.

Production DB synchronization remains separately gated and is not implied by this runtime expansion.
