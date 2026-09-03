# Video-to-Canon AI promotion decision — 2026-09-03

Status: `DIRECTOR_APPROVED / POLICY ACTIVE / PRODUCTION NOT DEPLOYED`
Governance mode: `ASSURED`
Policy version: `school-video-auto-canon-v1`
Tracker: issue #609; implementation: draft PR #1086

## Decision

Per-rule human confirmation is removed for knowledge learned from authorized
teacher video. After all required AI checks pass, a sealed candidate may be
automatically versioned and activated in SCHOOL CANON.

This decision does not authorize automatic promotion from WORLD / EXTERNAL,
course notes rejected as canonical sources, legacy L1 quarantine, inferred
repairs of unreadable material, ambiguous teacher speech or conflicting rules.

## Impact analysis

Benefit: five years of lessons can be converted into teachable, executable
knowledge without creating an impossible manual review queue. The primary new
risk is confident semantic error: ASR, speaker attribution, context recovery or
rule normalization can agree while still being wrong.

The control moves from per-item human approval to machine-verifiable evidence:
immutable source binding, exact transcript digest, trusted teacher identity,
explicit why/purpose, four test classes, independent semantic and bridge
verification, hidden-information analysis, regression, integrity, conflict scan
and tested rollback.

## Red Team failure classes

- forged or replayed source authorization;
- statement substituted behind a valid transcript locator;
- two nominal verifiers backed by the same model family;
- high-confidence extraction from incomplete auction context;
- WORLD knowledge used to repair teacher speech;
- hidden hands leaking into a rule or test;
- activation despite an open Canon conflict;
- verification bundle replayed for modified content;
- rollback named but not restoration-tested.

Every class must fail closed. The promotion object is content-addressed by both
candidate and verification-bundle SHA-256. The bundle is stored as canonical
JSON and rehashed by the database; it seals the candidate, validity interval,
scope, checks and exact prior activation IDs. Activation also recomputes a hash
over the locked executable rule rather than trusting a compiler-supplied marker.
Semantic, bridge, hidden-information and control attestations use separate
database capability roles whose authenticated principals are mapped to disjoint
check sets; free-form family labels cannot establish independence.

## Activation and rollback

The policy is active in governance. Production deployment is a separate
database/runtime change and remains blocked until migration regression,
integrity, rollback and independent I2 review pass. No current Canon row is
changed by this decision record or its draft implementation.

Promotion atomically supersedes an overlapping prior version for the same
knowledge item and scope, preserving both prior activation identities in the
receipt. Rollback of a promoted rule revokes its activation and restores that
preceding version for the same system profile, learner level and scope. History,
provenance, verifier receipts and the rejected/revoked version remain stored.
