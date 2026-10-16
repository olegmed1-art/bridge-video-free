# Offline book staging contract

Status: engineering preflight only. This change does not complete the production
book pipeline or approve source content. It implements a bounded producer for
already extracted records, semantic validation, a local reference store and a
citation preview. It performs no PDF download, OCR, LLM call, DDS execution,
production persistence, publication, or Canon activation.

`bridge_contracts/book_material.py` and the structural JSON schema define the
envelope. Both structural and semantic validation are required by future adapters.
Objects carry edition/rendition identity, zero-based PDF index, separate printed
page label (null if not visible), `[x0,y0,x1,y1]` bbox with explicit normalized or
PDF-point top-left units, and segment SHA-256 with a declared hash method. Legacy
pixel bboxes and one-based page labels need a reviewed conversion receipt; never
relabel them silently. Hash syntax is validated; actual source bytes and crop correctness are
not verified by this module.

The producer resets observations to M0/NOT_REVIEWED, with STAGED/NOT_PUBLISHED.
Source bytes are untrusted data. Nothing in source text can permit publication.
Stable identity uses edition, rendition, region, object kind and normalized
content. Run/model/prompt and review metadata do not change object identity.
Known card ranks are sorted before identity calculation; UNKNOWN stays unknown.
Changed meaningful content creates a new identity. Accepted legacy IDs must be
supplied through an explicit identity-to-ID mapping, not replaced automatically.
An anchor change also changes identity and needs reconciliation before migration.

The bounded dialect supports POSITION, EXERCISE, AUTHOR_ANSWER, ATOM, DDS_RESULT
and SINGLE_DUMMY objects. A full position requires 52 distinct exact cards and
four 13-card hands. Partial positions cannot be upgraded from structural counts.
DDS receipts are kept separate, require an exact position without errata, and
bind a canonical position input hash. The hash does not attest solver execution
or solve auction/play legality; those remain separate production gates. Current
position format has optional phase-aware remaining-inventory metadata, but no
auction/full-play validation and must not be called full PBN. In-trick counts
subtract one card only from seats that already acted. Unknown played ranks remain
UNKNOWN with AUTHOR/INFERENCE provenance; no missing card is manufactured.
Atoms are scoped to WORLD CARD_PLAY/DEFENSE; BIDDING is rejected in this adapter.

`BookStagingStore` accepts an explicitly supplied local SQLite connection. It is
a reference adapter, not a production database migration. A single transaction
writes object identities, observations, immutable run payload, local outbox and
COMMITTED_LOCAL marker. Replaying the same run creates nothing; a new run/model
adds observations while reusing identities. Reusing a run ID with changed content
fails. An interrupted transaction leaves every local table unchanged. Retirement
hides the batch from preview while retaining history. Outbox destination is
constrained to LOCAL_ONLY and has no delivery worker.

Preview returns an explicit NOT_PUBLISHED envelope with
`source_bytes_verified=false` and `student_delivery_allowed=false`. It requires
internal-use rights and exposes exact source/page/bbox/hash metadata. This is a
developer preview, not a real teacher response and not a retrieval fallback.

## Existing integration seams

- `bridge_school_api/knowledge.py`: existing authority-separated read API;
  WORLD requires reviewed versions, and projects source title/locator/provenance.
- `database/migrations/0010_knowledge_media.sql`: stable knowledge items,
  versioned content, source links and separate Canon activations.
- `database/outbox_publisher.py`: guarded production changeset publication.
  The local reference outbox must not be routed here without a reviewed adapter.
- `bridge_contracts/canon_source_domain_policy.py`: separate external card-play
  admission checks. Eligibility is not activation.

## Verification and remaining gates

Run `python -m unittest discover -s tests -p test_book_material_staging.py -v`.
Tests use only synthetic text/cards, stdlib SQLite and temporary local files.
They cover source anchors, card validity, UNKNOWN preservation, domain separation,
foreign keys, solver binding, replay, interrupted transaction, database reopen,
stored-payload corruption and soft retirement.

The subsequent WORLD adapter and protected teacher route are documented in
`BOOK_WORLD_ROLLOUT_V1.md`; this staging store remains a local reference only.

Before production: actual source hash/crop verification; legacy map for existing
objects; complete chapter manifests and coverage; claim-level review, exceptions,
counterexamples and errata; source rights and evidence verification; independent
I2 assurance; production schema/adapter and source-specific lease/checkpoint;
cross-system outbox failure/recovery tests; real cited teacher readback. Production
DDL/DML, merge and deployment require coordination with the owning task. Existing
Canon pilot work is outside this change. Rollback of this unintegrated code is
reverting these added files; local batches can be retired without deletion.
