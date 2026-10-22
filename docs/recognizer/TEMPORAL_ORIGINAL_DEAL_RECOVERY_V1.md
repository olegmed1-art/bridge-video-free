# Original deal recovery, 2026-10-22

Status: LOCAL CANDIDATE / PRODUCTION HOLD. Governance: ASSURED, algorithmically
independent enumeration (I2) plus separate adversarial review (I1). No live
recognizer, server, database, permissions, key, schedule or paid inference was
changed. Geometry remains OFF. Model uniqueness is conditional on the evidence;
it does not prove that a supplied visual or spoken premise is true.

## Existing implementation and exact scope

Base main: `2eec5ba690057463e0c5e89b2a429f46521c640c`. Existing partial
observer, native detector, partial accumulator and r265 teacher extractor are
reused. Legacy partial tracks remain review output; the recovery envelope is
separate. Physical-slot visual scoring, implicit speech context and cursor or
colour grounding are not established by solver uniqueness or source hashes.

## Actual callsites and activation

`accumulate_gambler_partial_frames(..., recovery_evidence=...)` passes through the
existing native detector and `accumulate_detected_frames` / `accumulate_partial_frames`
to `accumulate_recovery_frames`. Native retention mode preserves conflicting
accepted observations instead of losing them in an exception. Default native
behavior remains unchanged. A score alone never enters the hard solver.

`run_drive_3_1_free_r265.main(token_func, recovery_provider=...)` passes the optional
dependency through `bridge_runtime_hardening_r26_5.run/install` to the existing
r265 wrapper. The wrapper still captures the existing `obtain_transcript` before
`derive_deals_decisions`; it does not run a second transcriber. Reinstallation
with a different provider requires a fresh base object and fails explicitly.

The default GitHub workflow still selects r26.3. The r265 OIDC caller supplies no
provider. The candidate runner does not install r265. Therefore this patch is
NOT evidence of active production reconstruction. No environment flag bypasses
the evidence boundary. Without a provider, output is `CLOSED_UNKNOWN`.

Provider signature:

```python
provider(current_deal, existing_teacher_extraction, target) -> ReviewedRecoveryBatch
# target: source_video_sha256 and target_sha256
```

The wrapper hashes the current source file supplied to the existing transcript
call. Target digest covers exact current deal, job, source hash and matching
shot metadata. The batch must match target and evidence IDs, and each independent
frame receipt must explicitly name `output_deal_sha256` and an
`output_deal_binding_ref`. Matching a screenshot ID or nearby timestamp alone
does not bind a batch. A conflicting current visual owner blocks the envelope.
The wrapper adds `temporal_recovery` and `verified_recovery`; it never rewrites
legacy `hands` or `canonical_deal` with inferred cards.

## Evidence boundary and temporal identity

`ReviewedRecoveryEvidence(source_path, manifest)` accepts an independently
authored, frozen review manifest. It hashes actual source bytes. Its semantic
receipts are review assertions, NOT authenticated human identity or automatic
pixel/PTS verification. Never build receipts from ACCEPTED_VISUAL, confidence,
the expected answer, a teacher role score or model completion.

Manifest version `bridge-temporal-recovery-v1`, authority `INDEPENDENT_REVIEW`,
reviewer reference, source SHA and events are mandatory. Each event binds exact
source/frame SHA, episode, board occurrence (`board_epoch`), board number,
timestamp in milliseconds, observation index, image dimensions, recognizer,
reference, data class and pixel observer version. It separately attests settled
frame state, source PTS mapping, deal identity and clockwise screen-to-compass
orientation. Card proof binds the accepted-observation ordinal, exact card,
screen seat, physical role and independent evidence reference.

Any unknown binding closes scope. Board/episode/scene/orientation/model changes
reset scope. Joining requires exact predecessor event ID, continuous index,
increasing time and independent continuity reference. No elapsed-time threshold
proves continuity. Exact redelivery is idempotent; changed payload at one event
ID is CONFLICT. Repeated pixels count as one visual unit and zero independent
votes. Missing cards never imply absence. PLAYED requires seat/time evidence;
later physical in-hand occurrences, including order-inferred slots, conflict.

## Logical model and bounded search

The original deal has 52 unique cards and four 13-card hands. Capacity matching
computes all feasible owner domains by forced-edge feasibility, at most 209
matchings for the unary model. No witness assignment is exposed. Multiple
solutions preserve domains; infeasibility returns CONFLICT and no inferred deal.

Reviewed physical same-suit ordered rows may contain UNKNOWN slots. Both anchor
observations, slot boxes, layout/scale, sorting direction, suit and no-occlusion
receipts are required. Distance never invents a slot. `A ? Q -> K`, `Q ? T -> J`,
`9 ? 7 -> 8`, `A ? ? J -> K Q`; `A ? J` retains K/Q. A physical slot below 3
forces 2, and one above K forces A; a row ending at 3 adds nothing. Ascending
order is supported explicitly. Results use `INFERRED_ORDER_GAP` or
`INFERRED_ORDER_BOUNDARY`, with frame, slot, anchor and row provenance.

Rank alternatives and original-owner domains are tested jointly, including
same-frame physical uniqueness and played lifecycle. Global feasibility probes
are equivalent to repeated logical propagation: proving a gap can fill a hand,
which constrains the other owners and remaining slots. Proven derived facts
have full premise references; no confidence is recycled and no hypothesis is an
anchor. Cross-constraint owner deductions use `INFERRED_ORDER_CONSTRAINT`.

Limits: 2048 frame events, 52 accepted observations per event, 16 MiB manifest,
128 KiB frame record, 64 MiB snapshot, 16 GiB source file, at most 52 reviewed
physical slots per frame, 64 rank intervals per solve and 10000 total rank-search
nodes. Budget exhaustion never eliminates an alternative. Initial feasibility
unknown returns `UNRESOLVED_LIMIT`; incomplete domain probes retain supersets
and mark `propagation_complete=false` / outer approximations. No arbitrary deal
is selected at a limit.

## Speech, colour and provenance

Existing segments use floating `start`/`end` seconds, text, speaker,
unreliable flag and stable segment ID/ordinal/source assigned by
`run_master_3_1_free.assign_ids`. The universal runner saves this shape as
`transcript.jsonl`; source/job pins live in the outer evidence manifest/receipt,
not automatically in every segment. The adapter preserves these fields and
requires exact declaration digest, source, board occurrence and audio/speaker
review references. Both the speech interval and target frame must lie inside
the same proved board interval. The old +/-120 seconds is not identity evidence.

Deterministic RU/EN modality taxonomy rejects negation, hypotheticals, questions,
corrections, quoted/other-board statements and unsupported/uncertain contexts.
It is conservative pattern coverage, not complete NLP or a truth classifier.
An extracted ASSERTION remains `CANDIDATE_REQUIRES_INDEPENDENT_BINDING`.
Verified speech fills visually unknown cards only and remains `TEACHER_SPEECH`.
No negative phrase creates positive ownership or exclusion.

Colour is separate from speech and visual weights. Raw colour, cursor and
scores are retained but never constrain ownership. A separately reviewed
measurement may report a calibrated explicit theme palette, SUIT_SYMBOL ROI,
measurement/calibration references and exact card/slot binding. Only this
qualified form is checked for a conflict, not used to guess an owner. Four-colour
or nonstandard themes are represented by their explicit palette; unknown
palettes/backgrounds/card backs/highlights abstain. A qualified colour/card
contradiction returns CONFLICT. This is a contract for calibrated measurements,
not a restored pixel colour extractor.

Envelope `bridge-original-deal-recovery-v1` contains status, exact or explicitly
conservative domains, observed/direct support, evidence sources, inferred proofs,
constraints, conflicts and per-hand `observed_cards`, `declared_cards`,
`inferred_cards`, combined cards and unknown count. `canonical_promotion_allowed`
is false. A consistent but incorrect reviewed premise can still yield a unique
model; the tests explicitly retain this limitation.

## PBN handoff

`temporal_recovery_export.reconstruction_by_row_sha256(original_row, temporal)`
uses the PBN thread's installed-row key: SHA256 of UTF-8 JSON with
`ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False`.
It requires an independent `input_row_sha256` and `input_row_binding_ref` receipt.
It preserves the complete scoped recovery, alternatives, rank proofs, source pins
and conflicts. No serializer or scale file is modified.

The receiving `reconstruction_by_row_sha256` API stores this under
`reconstruction_unvalidated_claim`. The separate native API now accepts only
objects returned by `recovery_pbn.load_reviewed_recovery`. The producer helper
`reviewed_pbn_handoff(original_row, temporal, producer_pins=...)` prepares its
exact `bridge-reviewed-ownership-sidecar-v1` envelope, target binding and a digest
captured from the direct producer result. It never returns a qualified object.
The full result remains unchanged, including uncertainty and derivations.
The caller separately verifies producer pins, sidecar bytes and review bytes;
the result digest must not be copied from an untrusted sidecar. The loader requires one
complete scope and exactly one matching target event; the helper refuses to
slice a multi-scope result to make it pass.

Extended PBN premise validation preserves `INFERRED_ORDER_GAP`,
`INFERRED_ORDER_BOUNDARY`, `INFERRED_ORDER_CONSTRAINT` and
`INFERRED_COMPONENT_CONSTRAINT`. It binds their derivation to the complete
constraint snapshot, row/slot/anchor/frame/event references and exact source
partition. Played ownership uses the receipt's calibrated zone/slot map;
raw screen position remains separate. The serializer does not run another solver
or relabel inferred facts as VISUAL. Extended derived and zone-aware validation requires an explicitly enrolled
producer profile. All admissions still require the externally expected producer-pin
map and independent result digest.

A machine observation without proved canonical seats, original-hand inventory
and exact source/target binding cannot supply qualified ownership. `Deal=?`
is review output, not full-deal recognition. Never manufacture a qualified input.

## Frozen validation inputs versus autonomous runtime evidence

The independent review manifests used by these tests are FROZEN SYNTHETIC
VALIDATION FIXTURES. They are not an intended product workflow that makes the
owner manually label every card of every new video. Code review, source hashes
and palette/layout calibration validate a method; they do not prove a particular
future card, board boundary or ASR assertion.

The current reviewed-evidence adapter nevertheless requires per-event semantic
assertions for every admitted visual/component/speech premise. That is a real
limitation of this local candidate: an autonomous calibrated runtime producer
for those semantic assertions has NOT been qualified. The automatic runtime
transport provider described below is now implemented; it deliberately does
not fabricate these missing assertions. The current
`INDEPENDENT_REVIEW` authority must not be mass-generated from classifier scores
or a completed solver answer to pretend otherwise. A future audited runtime
evidence authority needs a distinct contract shared with PBN; it may reuse frozen
calibration assets but must produce source/PTS/board/orientation, physical-slot,
component and speech grounding for each event, with abstention and replay tests.
The r265 optional provider remains a reviewed replay seam. The Diana20 runtime
provider uses a distinct non-review contract and the same temporal accumulator.
No request for routine manual video labelling is made.

## Automatic Diana20 provider and saved linked replay

`video_adapters.diana20.runtime_recovery.Diana20RuntimeProvider.observe_saved_row` consumes the
actual persisted `{sequence, receipt, frame_evidence, payload, ...}` row without
rebuilding or discarding fields. `observe_frame_evidence` also accepts the
existing candidate's `FrameEvidence.observation` with its receipt and sequence.
It never uses the lossy `detector_result()` projection or starts its client.
This is automatic runtime evidence extraction, not a generated review manifest.

The provider reuses `recognizer_update.effective_cards/effective_unknown`,
existing factor diagnostics, source/frame hashes, actual decoder PTS, pixel and
ROI hashes, layout and seat claims. Candidate component domains remain separate
from hard domains. Missing semantic bindings stay UNKNOWN even if input fields
claim review authority. `RuntimeRecoveryEvidence` is a distinct closed authority;
the existing `accumulate_recovery_frames` records its gates and rejects integrity
conflicts. All observations survive, with zero independent votes.

`tools.diana20_recovery_replay` defines a local candidate-to-provider-to-recovery
interface. The caller must pin the pure response validator and FrameEvidence ABI,
then pass the exact persisted row and corresponding frame bytes. This source-only
interface does not establish installed wiring or authorize a live runner.

Existing transcript segments use floating seconds and source identity in their
outer envelope. Associate them using exact decimal conversion and actual decoder
PTS, not requested extraction times or an arbitrary time-radius extension.
An overlap never proves board identity or a supported speech assertion. Source
video integrity, encoded/pixel/ROI association and semantic authority are separate
checks. A saved source SHA claim is not verification of original video bytes.
Unknown board, orientation, settled state, continuity or slot/played-zone meaning
keeps runtime evidence CLOSED_UNKNOWN even when byte association is valid.
No replay inventory, real observation results or lesson excerpts are part of this
public source contract. Synthetic tests do not establish recognizer accuracy or
any complete real hand.

Input limits: 2 MiB per row, 2,048 rows, 64 MiB total observations, 16 MiB
transcript, 2 MiB frame manifest, 256 MiB archive and 16 MiB per frame. JPEG
dimensions are bounded before pixel decode. Pinned file reads are bounded and
checked for mutation; ZIP members are read in memory and never extracted.
Duplicate delivery cannot bypass frame hashing or clear a previous conflict.
No geometry, worker, network, server launch or new transcription is performed.

## Validation, release gate and rollback

Tests cover exact/ambiguous/impossible ownership, independent exhaustive small
models, review/source identity, reset, duplicates, rotation, repeated frames,
played cards, malformed inputs, speech modality, runtime target binding,
physical order/boundaries, capacity cascade, joint contradictions, colour and
PBN row handoff. CI extends the existing offline partial-shadow job only; no
deploy step, permission, secret, dispatch or production runner selection changes.
The RED/GREEN artifact records five baseline r265 false-positive sentences and
their corrected abstention, against exact base source bytes.

Synthetic fixtures test association, abstention and provenance retention.
They contain no human-labelled lesson data and establish neither real-video
accuracy nor deployed runtime qualification. Independent source/semantic evidence
is required before any real ownership result can be admitted.

Before publication: report exact changed paths, local test evidence, remote CI
state and target. Intended next target is a source-only review branch/ordinary
offline CI, then independently pinned replay. Remote CI and deployment remain
NOT_RUN. Production activation is pending recovered/reconciled historical
weighted behavior, real source/PTS/board/orientation evidence, held-out replay
and reviewed native PBN qualification. Do not alter HOLD or activate r265 by
merely creating these files.

Rollback: remove the explicit provider from the caller or revert the isolated
source patch after checking current main. Preserve output/evidence for review.
No database, server, resource, credential or ACL restoration is necessary.

## Component and played-zone additions requested during review

The actual factor producer was inspected at
`experiments/recognizer_factor_tie/frozen_rank_suit_candidate.py.txt`, together
with `factor_identity_diagnostic.py` and the tie diagnostic schema. It stores
rank/suit margins, same-registration ROI and hashes, with a whole-card or
component candidate; it does not certify separate components. The Gambler and
native adapters now retain these existing diagnostics instead of dropping them
from UNKNOWN records. No additional classifier, threshold or scale algorithm
was introduced.

`component_observations` preserves separate rank/suit/owner domains, candidate
domains, raw margins and provenance. Confidence is null when uncalibrated;
margin is not relabelled as confidence. A review may bind an exact UNKNOWN
record digest and physical slot to independent rank/suit/owner domains. Known
rank K with unknown suit retains four kings; known suit S retains thirteen
spades; known rank+suit with unknown owner remains unassigned. Component slots
join the SAME joint slot/owner solver; components on an existing order slot
intersect that interval rather than create a second slot. Global constraint
probes update every component domain, with full immutable constraint snapshot
references and no circular confidence. Inferred combined identities use
`INFERRED_COMPONENT_CONSTRAINT` and never become observed cards.

Played zones support `previous_trick`, `current_trick` and
`played_in_front_of_hand`. Each requires its own calibrated layout, orientation,
four slot boxes and explicit slot-to-canonical-seat map. No top/right/bottom/left
mapping or bottom-left rectangle is built in. When an observation has a box,
its reviewed zone box must match it. Unknown mapping abstains. Four displayed
cards belong to their respective mapped initial owners, not to the trick winner.

Played facts retain `initial_owner` separately from `membership=PLAYED` and their
display zone. `played_events` deduplicates by board occurrence, trick, card and
owner; repeated/current-to-previous displays add evidence references, not events
or votes. New played zones require an explicit board interval containing both
play time and display time. Different cards from one seat in one trick, or
different tricks or play times for the same card, conflict. Malformed optional
legacy trick IDs abstain. Native
retention mode keeps multiple visible instances for review without counting
duplicate cards in a hand. Raw screen-position conflicts are diagnostics; only
the independently mapped owners constrain reconstruction. Board resets isolate
play memory; a table-orientation scope change also resets conservatively.

These additions have local synthetic contracts. Coordinates, palettes, source
PTS and semantic component proofs for actual Gambler video still require a
frozen independent replay fixture before activation.

## R3 automatic current-pixel semantic measurements (conditional only)

The source-specific provider is now `video_adapters/diana20/runtime_recovery.py`;
`bridge_vision/runtime_recovery_evidence.py` contains student-independent byte,
ROI and JSON checks plus the closed machine-evidence gate. Core imports never
refer back to a source-specific adapter. The unchanged universal-video CI grep
gate continues to reject student names in the generic core. The provider's
persisted `bridge-diana20-runtime-evidence-v1` ABI is retained; the generic gate
provenance version is now `bridge-runtime-evidence-v1`.

The optional `video_adapters/diana20/runtime_semantics.py` accepts pinned raster
references, an explicit layout profile and ROI pins. It checks raw/pixel/ROI
digests before reuse. Its `_same_mark` comparison uses grayscale mean error <= 2,
standard deviation >= 2 and correlation >= .99. Three distinct compass glyphs
and two table landmarks must match one reference group. A unique compass rotation
is only a conditional mapping, with the fourth direction explicitly inferred.
This similarity candidate does not replace the exact-pixel gate. Calibration
labels remain claims until their source/PTS/semantic authority is qualified.

Current board-number-tile and table pixel hashes are recorded independently.
Adjacent observed snapshots receive elapsed time and equality/difference fields.
Neither equal hashes nor an unchanged compass prove the same board occurrence,
continuous coverage, a settled frame or an absence of an intervening rotation.
No board number is invented; board epoch and settled state stay UNKNOWN.
Conditional semantic measurements do not alter any hard semantic gate, proof,
card fact, vote, existing candidate payload or original-row digest.

A runtime continuity test requires a contiguous original-source decoder export
with actual PTS/frame indices, source SHA, encoded/pixel hashes and unmodified UI.
Sparse matching snapshots cannot establish the unobserved interval between them.
Decoder input alone does not qualify board semantics or card ownership. Frozen
method validation remains distinct from runtime measurements and held-out tests;
no routine per-card human labelling is implied by this source contract.

The presence of a candidate, replay helper or caller proposal proves no installed
launcher/provider wiring, readiness, delivery or ACK. Those runtime conditions
must be verified separately by the authorized owner before integration.
