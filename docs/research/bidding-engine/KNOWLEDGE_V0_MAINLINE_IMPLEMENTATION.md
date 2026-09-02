# Bidding knowledge v0 - mainline implementation

Status: **DRAFT / TEMPORARY-BRANCH VERIFIED / NOT IN PRODUCTION**  
Program: issue #609

## Purpose

Provide the durable executable storage and audit boundary required before real SCHOOL CANON bidding rules can be imported.

This implementation contains no bridge-system meaning, bid range, convention or seeded rule.

## Active migration path

The migration is located under the repository's actual database migration system:

- wrapper: `database/migrations/0200_bidding_knowledge_v0.sql`;
- immutable components: `database/migrations/0200_bidding_knowledge_v0/*.sql`;
- regression: `database/tests/100_bidding_knowledge_v0.sql`.

The migration wrapper and every component share one composite SHA-256 calculated by `database/scripts/migrate.sh`. Editing, adding or removing any SQL component after application changes the durable migration checksum and is rejected. CI includes a component-tamper regression.

## Objects

The migration creates the `bidding` schema with:

- `rule` - one executable projection of one versioned knowledge object;
- `rule_relation` - `depends_on`, `overrides`, `excludes`, `continues_to`, `implies`;
- `rule_test` - immutable test definitions after activation;
- `rule_test_run` - append-only test results;
- `rule_conflict` - explicit overlaps and contradictions;
- `runtime_activation` - authority-separated, time-scoped activation;
- `decision_trace` - append-only acting-hand/public-state trace;
- `ingestion_run` - durable import identity and terminal status;
- `ingestion_event` - append-only sequential action log.

Read models and functions separate:

- active SCHOOL CANON runtime catalog;
- active WORLD / EXTERNAL research catalog;
- canonical-to-world structural links.

## Fail-closed activation

A rule cannot enter an active runtime lane unless:

1. its executable projection is `validated`;
2. its knowledge version is reviewed/approved and runtime-eligible;
3. an active source is linked;
4. positive, negative, boundary and hidden-information tests each have a latest PASS;
5. every other enabled test has a latest PASS;
6. no open conflict involves the rule;
7. SCHOOL CANON has a matching active `canon_activation`;
8. the requested runtime interval does not overlap another active interval for the same rule/lane/scope.

WORLD rules never enter the SCHOOL runtime catalog.

## Information boundary

Executable rules, traces and audit payloads recursively reject keys representing:

- partner or opponent hands/cards;
- compass-seat hands;
- full deals;
- hidden or actual unknown cards.

A decision trace may contain only the acting hand and public state.

## Immutability and audit

- active rule content cannot be updated or deleted;
- active rule test definitions and relations cannot be changed;
- test-result rows are append-only;
- decision traces are append-only;
- ingestion events are append-only and sequential;
- terminal ingestion runs cannot be reopened or have their identity rewritten;
- audit timestamps are server-controlled by column-level grants.

## Runtime access

- reader/app/worker can query the canonical runtime function;
- only worker can query the research catalog containing WORLD rules;
- app and worker can append bounded decision traces;
- worker can create candidates, tests, test results, conflicts and ingestion events;
- worker cannot activate rules or pre-resolve a conflict;
- internal validation functions are not callable runtime APIs.

## Verification evidence

Temporary Neon branch:

- project: `misty-poetry-18012774`;
- branch name: `bidding-knowledge-v0-mainline-validation-20260827`;
- branch id: `br-muddy-resonance-b1pf1tze`;
- parent: production branch `br-wispy-lab-b1rq54of`.

Observed after migration:

- 9 base tables;
- 3 views;
- 20 schema functions;
- 16 non-internal triggers;
- migration registry entry present.

Transactional smoke verified activation gates, authority separation, hidden-information rejection, school scoping, active-rule immutability, trace/event append-only behavior, ingestion lifecycle, runtime overlap rejection and effective principal ACLs.

The first smoke attempt rolled back on a test-only misuse of `PUBLIC` as a named role. The corrected smoke passed. Both events are retained in the canonical ingestion work log.

## Current boundary

- Production Neon effect: **NONE**.
- Real bidding rules created: **0**.
- SCHOOL CANON rules activated: **0**.
- The rejected course notes are not used.
- The approved two-page canonical PDF is being processed independently in draft PR #662.
