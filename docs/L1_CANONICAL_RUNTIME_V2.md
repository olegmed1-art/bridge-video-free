# SCHOOL_L1_DB_V1 canonical runtime v2

Snapshot date: 2026-08-22.

Source of truth: the school's Google Drive catalog, sheets `Навыки`, `AI — Атомы знаний`, `AI — Правила системы`, `AI — L1 Regression`, and `AI — L1 Integrity`.

## Registry closure

Runtime v2 pins the complete current `SCHOOL_L1_DB_V1` active-rule registry:

- 111 ACTIVE domain rules;
- 2 ACTIVE governance rules;
- 121 Skills total;
- 103 APPROVED Skills;
- 17 PARTIAL Skills;
- 1 BLOCKED Skill (`SKILL-0081`).

The sorted domain-rule ID fingerprint is:

`143d7387734a721ebedd6cab78af21278f7b250d963ee2c82562e50dc3bbfd33`

Tournament-system rules are deliberately excluded from the L1 registry.

## Execution boundary

`l1_canonical_runtime.py` remains the bounded semantic evaluator proved by the current regression contract.

`l1_canonical_runtime_v2.py` adds the complete registry and distinguishes three cases:

1. a registered rule with an implemented bounded evaluator — evaluate deterministically;
2. a registered rule without a bounded evaluator — `BLOCK / KNOWN_RULE_NOT_EXECUTABLE`;
3. an unknown, tournament, typo, or synthetic rule — `BLOCK / UNKNOWN_RULE_ID`.

No natural-language rule text is interpreted at runtime. Missing conditions, external conventions, Lesson 15 signaling, and PARTIAL/BLOCKED canon are never guessed.

## Safety boundary

This change performs no:

- Neon migration or production database write;
- Drive mutation from runtime code;
- DDS/network call;
- person-specific Student Profile write;
- automatic import of tournament-system rules into L1;
- automatic promotion of PARTIAL/BLOCKED Skills.

Production DB synchronization remains a separate gated operation. In particular, the repository runtime must not be treated as authorization to apply pending Neon migrations or catalog-to-DB writes.
