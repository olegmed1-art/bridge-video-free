# Video evidence to School Canon — v2 contract

Status: `AI AUTO-PROMOTION IMPLEMENTED / PRODUCTION NOT ACTIVATED`
Governance mode: `ASSURED`
Tracker: #609; upstream video runtime: #881

## Boundary

Authorized teacher video is a canonical learning source after the AI gate.
Artifacts move through:

`RAW_VIDEO -> TRANSCRIPT -> OBSERVATION -> TEACHER_ASSERTION -> RULE_CANDIDATE -> AI_VERIFIED -> ACTIVE`

The evidence adapter stops at `public.analysis_candidate`-compatible staging.
The separate promotion gate seals an idempotent activation command only after
all required checks pass. Per-rule human approval is not required.

## AI verification eligibility

A video-derived rule candidate enters AI verification only when:

- the source is classified `SCHOOL_PRIMARY_EVIDENCE`;
- source policy explicitly binds the exact video SHA-256, Drive file identity,
  trusted teacher and semantic scope;
- the teacher identity is verified for every cited transcript segment;
- the assertion text SHA-256 exactly matches its single transcript span;
- the rule includes source-backed why/purpose and consequences;
- normalized rule and tests contain no hidden-hand fields;
- positive, negative, boundary and interference tests all exist;
- no ambiguity or contradiction remains.

`TEACHING_CONTEXT`, `WORLD_EXTERNAL`, unapproved sources, low-confidence or
ambiguous statements and conflicts remain `EVIDENCE_ONLY`. They may identify a
gap, but cannot enter automatic Canon promotion.

## Automatic activation gate

`AI_VERIFICATION_PENDING` becomes `AUTO_PROMOTION_READY` only after all 16
checks in `video-canon-ai-promotion-v1` pass. Semantic parsing and bridge-logic
verification must be I2/I3 and come from different verifier families. The
hidden-information firewall must also be I2/I3. These families are not
caller-supplied labels at the database boundary: separate NOLOGIN capability
roles are mapped through an immutable verifier registry to disjoint check sets.
Promotion re-resolves every PASS receipt against that registry and requires the
lane to remain active at promotion time; revoking a compromised lane therefore
invalidates its earlier receipts for future activation.
Regression, integrity,
conflict scan and a tested restore path are mandatory.

The activation command binds both candidate SHA-256 and verification-bundle
SHA-256 and is idempotent. The persisted bundle contains the exact candidate,
effective period, activation scope, checks, rollback target and a digest of the
complete rule-test definitions plus their latest runs; the promoter cannot
supply replacements for those fields. The four source-derived test classes are
an exact deterministic projection of the candidate payload. Added, removed or
edited definitions and later test runs invalidate promotion instead of
borrowing an unrelated PASS. The database locks the candidate and rule,
recomputes digests across all executable rule fields, the explanation and test
state, and compares that content with the sealed candidate and bundle before
any activation is written. Source authorization can be revoked immediately through
a guarded lifecycle transition. The row trigger captures its effective
retirement time with a wall-clock reading after the policy row lock is held and
overrides any caller-supplied backdated validity end.

When the same knowledge item already has an active version, activation closes
the prior Canon and runtime rows in the same transaction. Their exact IDs are
required in the restore-tested bundle and retained in the promotion receipt.
This RPC admits only an effective `valid_from` at or before transaction time.
A future-dated transition requires a separate scheduler; rejecting it here
prevents the current version from being superseded before the replacement is
actually effective.
The existing runtime invariant remains unchanged:
`CANON_CONFLICT` stops and does not call WORLD; only a recorded `CANON_GAP`
permits the WORLD lookup.

Implementation boundaries:

- `video_canon_evidence.py` seals exact source, speech, logic and tests;
- `video_canon_ai_promotion.py` evaluates the 16-check bundle;
- `video_canon_auto_pipeline.py` produces promotion commands or explicit gaps;
- the Diana quality layer appends every generated Video-to-Canon candidate to
  the shared `candidate_staging_records` stream consumed by the database
  persister;
- the same integration layer explicitly routes full-board proofs,
  source-bound logic proofs and correction-review receipts from the analysis
  master to the strict DDS and learning-feedback validators; malformed or
  absent proof produces a gap, never an inferred success;
- migration `0322_workflow_video_canon_ai_promotion.sql` separates verifier and promoter
  roles and performs the atomic database activation;
- the Diana v4.2 quality layer invokes the pipeline when a complete
  `video_canon_*` input bundle is present.

## Explanation is part of knowledge

A teachable rule needs a source-bound explanation candidate containing the
reasoning chain, prerequisites, rejected alternatives, an example and a
counterexample. The analyzer must preserve the teacher's reason rather than
generate a plausible replacement. A rule observation without such evidence
creates an explicit `EXPLANATION_MISSING` gap. It may still be reviewed as a
rule candidate, but it is not a complete teachable knowledge unit.

The analyzer now extracts an explanation candidate directly when the same
source-bound transcript segment is attributed to the teacher with confidence
at least 0.8 and contains an explicit causal marker such as “потому что”,
“поэтому” or “так как”. The exact speech, speaker evidence and timestamps are
preserved. This first pass intentionally records a partial explanation rather
than completing missing premises, alternatives or examples with generated text.

Explanation covers both cause and purpose. Explicit links are stored as typed
relations: `CAUSE` (why), `PURPOSE` (what for), `CONSEQUENCE`, and
`ALTERNATIVE_CONSEQUENCE`. Each relation retains the exact left and right
clauses around the teacher's connective. The target teachable logic is:

`conditions -> cause/purpose -> conclusion -> action -> consequences -> rejected alternatives`

Missing links remain explicit completeness gaps; plausible model-generated
links are not evidence.

## Offline DDS consequence comparison

When a full board is independently verified, the analyzer can stage a
`DDS_DECISION_COMPARISON` linking the player's source-bound logic and chosen
play/defense action to DDS3 alternatives. It stores a hash and source references
for the full deal, but never the deal or hidden hands in the student-visible
payload. DDS is explicitly `offline_only`: it measures consequences after the
fact and does not validate a bidding rule, become Canon evidence, or enter a
live resolver request. The integrated postprocessor reruns the exact bounded
position request through the pinned DDS3 implementation and validates the
freshly returned result before recording `OFFLINE_EVALUATED`; caller-supplied
DDS labels or moves are neither accepted nor used. Before starting an isolated
position worker, the production executor requires the deployed binary's
SHA-256 to equal `DDS3_POSITION_WORKER_SHA256`; the verified digest is retained
in result provenance. Every allowed public-context
field has a bridge-specific value validator, so PBN/hand strings cannot hide in
an otherwise permitted field. Raw board, logic and correction proof collections
are transient validator inputs and are removed before the quality artifact is
serialized; only sanitized results or explicit gaps survive.

The same value-level firewall applies before a teacher-video Canon candidate is
placed in staging. Seat and spelled actor markers require a real left token boundary in both
English and Russian, so prose such as `Explanation: Q is an abbreviation` or
`Порука партнера: Q — подпись поручителя` is not mistaken for a disclosed
seat/partner card. Full PBN encodings and labelled partner/opponent card
payloads—including `♠♥♦♣` suit-symbol notation in any suit order, including a
single unambiguous card (`Q`, `q`, `T`, `t`, `10`, or a directly
attached rank `2`–`9`)—are rejected anywhere in the complete payload.
Ordinary quantitative bridge prose such as `5 cards`, `5 hearts`, `3
trumps`, `7 losers`, points, controls, winners, stoppers and their Russian
equivalents remains allowed when it is a length/count description rather than
a disclosed suit group. The firewall still rejects fragments with omitted
suits/cards, including the source-bound
teacher statement and otherwise innocent keys such as `notes`. Candidate
staging identity includes the canonical payload SHA-256, so a corrected
assertion becomes a preserved new revision instead of colliding with the old
row.

## Learning feedback loop

Evidence-bound corrections for ASR, speaker, card, auction, extraction and
pedagogy are emitted as immutable versioned `ANALYZER_TRAINING_EXAMPLE`
records. Human corrections remain useful but are not a prerequisite for every
Canon rule. A self-hash and claimed reviewer are insufficient: the receipt must
resolve byte-for-byte through a trusted review-store resolver supplied outside
the analyzer master. Without that trust dependency the correction becomes an
explicit gap and is not training-eligible. The production Diana entrypoint
resolves through the worker's read-only access to the append-only
`bidding.video_correction_review_receipt` store. Only the authenticated control
verifier capability may attest a receipt there; app and worker roles cannot
insert or mutate it. Each receipt binds both the active capability role and the
authenticated login principal. Resolution requires the registry lane to remain
active and the recorded login principal to retain current membership in that
`CORRECTION_REVIEW` capability. A candidate model is only represented by a
`MODEL_IMPROVEMENT_PROPOSAL` when a named holdout compares it with a baseline
and records a rollback model version. Model deployment remains a separate
gate; a model passing holdout is not itself permission to change Canon.

## Promotion-time state and restoration

Semantic, bridge and hidden-information receipts carry the authenticated
database login principal in addition to verifier family/version; the three
high-assurance executions must come from distinct principals. The four
state-dependent checks (`CANON_REGRESSION`, `CANON_INTEGRITY`,
`CANON_CONFLICT_SCAN`, `ROLLBACK_RESTORE`) carry the same deterministic active
Canon snapshot SHA-256. The snapshot includes active rules, knowledge versions,
all active Canon activations, open conflicts, latest test runs and every active
Canon version's source bindings, including versions without a runtime row. Activation
serializes by school and acquires write-intent table locks on every table later
written—including candidate and promotion-receipt tables—before final
wall-clock expiry checks, so no later write can wait on a lock upgrade past an
authority boundary. It also rejects a stale digest. Immediately before writes,
each receipt's recorded login must still exist, be login-capable and retain
membership in its active verifier capability.

Promotion binds the whole authoritative knowledge version, not only its rule.
The version must be attached to the globally unique deterministic
knowledge-item key derived from the complete sealed candidate payload hash and
must be a single-rule candidate whose content
is the exact sealed candidate payload; system, level, effective interval,
agreement scope, method, source locator and deterministic provenance must all
match the sealed inputs. Exactly one `derived_from` source binding is allowed,
and both its source ID and transcript locator must equal the sealed teacher
assertion. A database trigger resolves both immutable Canon activation IDs from the
promotion receipt—the newly promoted version and, when present, its
predecessor—and freezes both source sets as soon as the receipt exists; the
worker cannot add, replace or delete provenance afterward. The promotion
receipt stores the predecessor's exact ordered source-row snapshot, while a
digest of the complete immutable promoted-version projection is retained in
the receipt and activation provenance.

Rollback is operational, not documentary. A dedicated restorer capability
locks the predecessor Canon row, every recorded runtime target and the
restore-receipt table before final policy/expiry checks. It validates all
targets without mutation, re-resolves predecessor attestor memberships, repeats
every finite predecessor and outgoing Canon/source/runtime wall-clock boundary
in both predecessor and no-predecessor paths, then performs the bounded set of mutations and can emit the receipt-bound
restore record with the exact promotion bundle and restore
evidence hashes. The transaction revokes the new Canon/runtime activation,
restores the exact superseded activation IDs and their original validity ends,
then emits an append-only restore receipt. No application, worker, verifier or
promoter receives direct activation-table writes. Before restoration succeeds,
the same rule/test/source/conflict tables are locked and the restored rule must
pass the current runtime activation gates.
When the prior target came from an earlier AI video promotion, restoration also
requires its exact source policy to remain active by wall clock and long enough
for the restored validity interval; a revoked or expired teacher-video
authorization cannot be reactivated through rollback. For every predecessor, regardless of whether it originated from AI promotion,
the restorer compares the current complete source-row set byte-for-byte with
the immutable snapshot captured by the replacing promotion before any
reactivation. It also recomputes the predecessor's full knowledge-version
digest and sealed rule-test-state digest. The outgoing Canon and runtime rows receive
one shared wall-clock revocation timestamp captured immediately before both
updates. Restoration also requires every original attestor login
to retain its active verifier capability before reactivation.
