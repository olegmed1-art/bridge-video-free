# Bidding Knowledge v0 — implementation evidence

Date: 2026-08-26  
Tracker: GitHub issue #609  
Governance: ASSURED / minimum I2  
Migration: `db/migrations/0105_bidding_knowledge_v0.sql`

## Outcome

A first executable bidding-knowledge infrastructure layer has been implemented without adding or changing any bridge-system meaning.

The implementation reuses the existing generic identity, provenance, version and approval objects in `public` and adds a bounded `bidding` schema for executable projections, activation gates, runtime retrieval and append-only decision traces.

No course note, external robot result, inferred convention, range or bid has been inserted as SCHOOL CANON by this change.

## Authority boundary

`public.knowledge_version.authority_class` remains the authority source.

- `school_canon` can enter the canonical runtime view only through a matching active `public.canon_activation`.
- `external` can enter only the explicit `world_external` lane.
- `research_candidate` and `school_practice` are not runtime-eligible in v0.
- WORLD objects are excluded from the default runtime catalog; they are returned only when the caller explicitly sets `p_include_world=true`.

A canonical↔world comparison view is built from the existing `public.knowledge_relation` graph. It does not promote either side.

## Executable objects

The migration creates:

- `bidding.rule` — executable projection of one `knowledge_version`;
- `bidding.rule_relation` — `depends_on`, `overrides`, `excludes`, `continues_to`, `implies`;
- `bidding.rule_test` — positive, negative, boundary, interference, hidden-information, conflict and regression tests;
- `bidding.rule_conflict` — explicit unresolved/resolved conflict registry;
- `bidding.runtime_activation` — separately gated canonical and world activations;
- `bidding.decision_trace` — append-only resolver trace;
- active canonical and world views;
- `bidding.get_runtime_rule_catalog(...)` — retrieval only, not applicability evaluation or bid selection;
- `bidding.canon_world_link_v` — structural comparison links.

## Activation gate

An active rule must satisfy all of the following:

1. the executable projection is `validated`;
2. an active source link exists;
3. positive, negative, boundary and hidden-information tests have passing evidence;
4. no test currently fails;
5. no open conflict involves the rule;
6. authority lane matches the knowledge authority class;
7. SCHOOL CANON has a matching active canonical approval for the same scope and validity interval;
8. WORLD knowledge has no canonical approval reference.

The active views repeat the gate dynamically. Therefore, a later failed test, inactive source, retired rule, expired activation or newly opened conflict removes the rule from runtime retrieval without waiting for another promotion action.

## Information-set boundary

The runtime schema contains the acting hand and public state only.

A recursive JSON guard rejects hidden/full-deal keys in executable rules and public runtime fields, including partner/opponent hands and compass-seat hands. Test fixtures may contain oracle-only hidden information because hidden-information invariance tests need it, but those fixtures are not runtime inputs.

`bidding.decision_trace` is append-only and rejects updates or deletes even by a privileged writer. A gap trace must reference `public.knowledge_gap`.

## Access boundary

- readers receive schema usage and read access;
- workers may maintain candidate rules, relations, tests and conflicts;
- workers may append decision traces;
- workers cannot insert/update/delete runtime activations;
- workers cannot update/delete decision traces;
- activation remains a controlled owner/governance operation.

## Neon integration verification

Temporary branch: `tmp-bidding-knowledge-v0-20260826`  
Branch ID: `br-nameless-haze-b11fy1xb`  
Parent: `br-wispy-lab-b1rq54of`  
Database: `neondb`  
PostgreSQL: 18.6

Synthetic verification results:

- 3 executable fixture rules created;
- 8 passing gate tests created;
- 1 canonical and 1 external activation admitted;
- canonical-only catalog returned 1 rule;
- research catalog returned 2 rules;
- external rule count in the canonical view remained 0;
- canonical↔world relation was exposed once;
- activation before required test coverage failed closed;
- authority-lane mismatch failed closed in both directions;
- a failed test removed the WORLD rule from the active view;
- an open conflict removed the canonical rule from the active view;
- an inactive source removed the canonical rule from the active view;
- restoring each gate restored the expected view;
- hidden-key writes to rule/runtime trace were rejected;
- decision-trace mutation was rejected;
- worker privilege checks confirmed no activation write and no trace update.

Final fixture counts:

| Measure | Result |
|---|---:|
| executable rules | 3 |
| rule tests | 8 |
| active runtime activations | 2 |
| active SCHOOL CANON rules | 1 |
| active WORLD rules | 1 |
| canonical-only catalog | 1 |
| opt-in research catalog | 2 |
| append-only traces | 1 |
| canonical↔world links | 1 |

The migration registry checksum is `5b2a4f37bb98d81ce39ba22202985581dceaa9227077dd7c537c18c7e1bff14c`.

The Neon whole-branch schema-diff endpoint returned HTTP 413 because the parent schema is too large; verification therefore used direct object introspection, trigger/privilege inspection, executable fixtures and fail-closed assertions on the temporary branch.

## Independent assurance

`tests/test_bidding_knowledge_v0_contract.py` is a separate static contract checker. It verifies the migration checksum, parser-compatible function bodies, required objects, no hidden-hand columns, activation gates, authority-lane separation, WORLD opt-in behavior, append-only traces, activation privileges and absence of embedded bridge meanings.

Result: 10/10 checks passed locally.

This provides an I2-style independent algorithmic/formal pass in addition to the live PostgreSQL integration test. It does not replace future bridge-domain I4 review of any actual canonical rule meaning.

## Rollback

Before production use, rollback is straightforward because no existing table is altered:

1. stop callers from using the `bidding` schema;
2. `DROP SCHEMA bidding CASCADE`;
3. remove `0105_bidding_knowledge_v0` from `public.schema_migration` only as part of the controlled rollback record.

The migration does not seed canonical or external knowledge, so rollback does not require translating or deleting bridge meanings.

## Remaining boundary

This change creates durable storage, gating and retrieval. It intentionally does not yet define the constraint-evaluation DSL, applicability algorithm, candidate ranking semantics or the first real school bidding branch. Those require evidence and, for actual SCHOOL CANON meaning, explicit canonical provenance/approval.
