# Canonical Books event and delivery v2

This supersedes the unpublished nested Books v2 proposal. There is one flat wire
contract shared with the unchanged, SHA-pinned Teacher consumer. Acceptance here
is local integration only. No production authorization, installation, WORLD
activation, real catalog delivery, or common Teacher provider admission is implied.

The canonical synthetic fixture is tests/fixtures/book_world_canonical_v2.json.
It contains no book text or production data. Both producer and Teacher tests use
these same bytes. Schema: schemas/book-world-delivery-v2.schema.json.

## Exact wire fields

Recorded (8):
schema, publication_hash, run, source_id, stable_keys, version_ids,
event_contract, publication_authorization_status.
schema = book-world-reviewed-v1; event_contract = book-world-recorded-v2.

Published (8):
schema, publication_hash, catalog_receipt_ref, event_contract,
publication_authorization_status, recorded_event_id, recorded_changeset_id,
recorded_payload_hash.
schema = book-world-delivery-v1; event_contract = book-world-published-v2.

Status is exactly allowed, blocked, or unknown. There is no default allowed.
The entire flat payload is hashed with the existing canonical digest, including
markers, authorization status, and Published causal references.

Import receipt: unchanged 10 fields plus
receipt_contract = book-world-import-receipt-v2 and publication_authorization_status.
Publication receipt: unchanged 6 fields plus
receipt_contract = book-world-publication-receipt-v2 and publication_authorization_status.
Each receipt status equals its corresponding persisted event status.

Published causal references must equal the actual persisted Recorded event_id,
changeset_id and payload_hash. publication_hash alone is insufficient. The
actual Recorded must be committed and delivered. Its complete flat payload hash
is verified before constructing or delivering Published.

## Trusted host APIs

persist_book_world(..., delivery_authorization=trusted_receipt) emits flat v2.
Omitting authorization preserves exact legacy v1 six-field events and ten-field
receipts. blocked/unknown receipts may be staged but cannot be delivered.

publish_book_world(..., delivery_authorization=trusted_receipt) requires an
explicit allowed receipt for v2 and builds Published from actual SQL metadata.
It preserves legacy v1 six-field publication receipts and three-field events.
Malformed legacy event fields and the obsolete nested proposal are rejected.

publish_changeset_outbox(dsn, changeset_id, *, authorization_provider=None)
requires an explicit trusted host callback for Books v2:
    authorization_provider({"recorded_event": actual_metadata11}) -> trusted_receipt
The callback receives a defensive copy. Published resolves its actual Recorded
parent through SQL before authorization; no scope is inferred from a catalog ref.
Missing callback, callback failure, malformed receipt, scope mismatch and
blocked/unknown/missing wire status reject the whole mixed batch before any RPC.
A bare allowed wire value is never a grant.

Trusted receipt (not part of the flat wire):
schema=book-world-outbox-authorization-v1, authorization_ref, decision, purpose,
scope. purpose=BOOK_WORLD_OUTBOX_DELIVERY_ONLY. scope contains exactly school_id,
source_id, run, publication_hash, stable_keys, version_ids, recorded_changeset_id.
Every field and the paired key/version order must equal actual Recorded scope.
Receipt issuer authenticity and provenance are responsibilities of the trusted
host; no browser input, automatic approval or production issuer is implemented.
Authorization refs are no longer hashed into the wire. Different genuine host
approval refs for the same scope/status produce the same event bytes; hosts must
retain their own authorization audit. Changing the wire status still conflicts
with the immutable same-run event.

## Readback and consumer

read_book_world_event(cursor, *, school_id, event_id=None, changeset_id=None)
performs only SELECT on the supplied trusted connection. Supply exactly one
locator. It returns the Teacher exact 11 selected metadata fields:
event_id, event_type, aggregate_version, school_id, changeset_id, partition_key,
payload, payload_hash, changeset_status, outbox_status, event_position.
It requires matching event/changeset/outbox school and changeset joins. It does
not fabricate receipts or establish application-to-database target identity.
No new row locks or privilege grants are introduced. Existing locked outbox
bindings, immutable-event DB guards and publication RPC checks remain required.

Teacher APIs are unchanged:
bind_import_receipt_v2(map, receipt12, recorded_metadata11,
                       expected_school_id=school, test_only=True)
consume_card_v2(..., read_delivery=trusted_reader, test_only=True)
read_delivery receives exactly recorded_event_id, recorded_changeset_id,
publication_hash, source_id, expected_school_id. Its snapshot has schema,
recorded_event, recorded_outbox_receipt, publication_receipt, published_event,
published_outbox_receipt. The existing Teacher snapshot/map labels ending in
-proposal remain byte-identical; no second event protocol is introduced.
Before WORLD and again after a successful WORLD read, delivery must be valid and
unchanged. The native test implements this reader on disposable SQL using actual
producer receipts and actual publisher results. A production host adapter and
authorization issuer are not installed or admitted.

## Identity, compatibility and verification

UUID namespace remains uuid5(NAMESPACE_URL,
"bridge-book-world:{kind}:{school_id}:{key}"). aggregate_version remains exact
integer 1, not the wire version. Content/version hashes are unchanged. Existing
run IDs cannot be upgraded in place: same-run different payload fails closed.
Legacy v1 is not upgraded or rewritten, and v2 is not silently passed to v1.

The shared PostgreSQL test uses the actual repository allocate_event_position
and publish_outbox_event functions, with the existing SECURITY INVOKER semantics.
Its disposable publisher role can update event_position and allocator state as
required by those RPCs, but cannot update event payload or changeset. This proves
no additional privilege requirement from readback; production grants remain unknown.
Catalog receipts and operator authorizations in tests are explicitly synthetic.
WORLD remains staged/GAP after transport, requires separate reviewed publication,
and retirement returns GAP. Canon is never activated.

Pinned Teacher consumer SHA256:
ec90bee83dc0d77a38b9023759a838098133a4004f03e65e85c02059089098ef
Pinned legacy consumer SHA256:
335c7b381c63530a5b27dff024c2bd80f8c0252b333c371b56e571a874dcb0a0
