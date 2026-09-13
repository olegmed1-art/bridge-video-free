# WORLD Knowledge v0 — safe draft design

Status: **DRAFT / NOT APPLIED TO PRODUCTION**  
Program: issue #609  
Work packages: `WORLD-SCHEMA-001`, `WORLD-META-001`, `WORLD-ROBOT-001`, `WORLD-RESOLVER-001`

## Storage decision

Use the existing `bidding` schema and the existing versioned knowledge graph.  A
second physical database would duplicate migration, recovery and ACL boundaries
without adding authority safety.  Separation is enforced by `authority_class`,
runtime lane, read API and append-only trace:

- `school_canon` is queried first through the school-only catalog.
- `external` can be queried only after a durable `CANON_GAP`.
- external rules cannot carry a `canon_activation`; the active school catalog
  excludes them.
- a world decision is evidence/advice, never a write to a school knowledge item.

`0201_world_knowledge_v0.sql` extends (but does not rewrite) the unpromoted
`0200_bidding_knowledge_v0` candidate.  It therefore remains blocked behind
the current #798 migration/I2 gate.

## Resolver contract

`resolve_two_lane` accepts only an acting hand and public auction/context from
the caller.  It produces exactly one of:

1. `CANON_MATCH`;
2. `CANON_CONFLICT` (and stops);
3. `WORLD_FALLBACK` after `CANON_GAP` and a reliable world answer;
4. `WORLD_CONFLICT` after `CANON_GAP`, preserving alternatives;
5. `UNRESOLVED_GAP`.

No confidence averaging is permitted.  The database trace rejects a selected
world rule, a world candidate list or a world outcome unless `CANON_GAP` and a
same-school `knowledge_gap` exist.  Hidden partner/opponent cards are rejected
recursively in robot inputs, raw responses and traces.

## Robots

The migration registers pinned robot engine/version/model hash, configuration
hash and convention card separately.  Each decision has one of two modes:

- `ROBOT_RECONSTRUCTED_SURFACE` — a tested reconstruction;
- `ROBOT_LIVE_DECISION` — a bounded request to that pinned configuration.

Both retain acting hand, public context, raw response, interpretation,
confidence, license/API boundary (on the robot record) and a full decision
trace.  Registration is intentionally not granted to the ordinary worker;
adding an engine/version is a reviewed research operation.

## WORLD-META-001 loading boundary

The committed intake snapshot verifies the expected Drive dimensions but is not
the 402 source records themselves.  `scripts/stage_world_metadata.py` accepts
only a row-level export with exactly 245 sources, 42 authors, 95 audit records
and 20 queue records, writes a SHA-256-bound manifest, and refuses any count
mismatch.  It cannot insert a bidding rule or activate any knowledge.

Before a real staging run: export the four named Drive tabs, retain their
headers and stable IDs, run the script, and attach the resulting manifest to an
append-only `world_intake_batch`.  Actual rows should then be loaded as
external source/evidence metadata only; no semantic rule extraction occurs in
this package.

## Remaining gates

- `0200` is still absent from production and #798's independent I2 review is
  still a prerequisite.
- `WORLD-BRANCH-001` needs a selected, explicitly bounded first canonical
  vertical; it cannot be chosen from unactivated PDF candidates.
- `WORLD-I2-001` must independently exercise authority isolation,
  hidden-information firewall, incompatible-system separation and absence of
  WORLD-to-canon promotion on a disposable branch.
