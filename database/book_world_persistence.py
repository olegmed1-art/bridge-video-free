"""Bounded WORLD data migration into existing knowledge tables; no DDL or grants.

Caller owns connection provisioning and production approval. Importing this file
does not connect. There is deliberately no unauthenticated publication endpoint.
"""
from __future__ import annotations

from uuid import UUID

from psycopg.rows import tuple_row
from bridge_contracts.book_source_identity import needs_asset_binding
from .book_source_reconciliation import source_locator, require_source_asset

from bridge_contracts.book_material import canonical_json, digest
from bridge_contracts.book_world import build_world_publication, publication_version, world_uuid, WORLD_SCHEMA


def persist_book_world(connection, bundle: dict, receipt: dict, *, school_id: str,
                       source_id: str, before_commit=None, dry_run=False) -> dict:
    publication = build_world_publication(bundle, receipt)
    school_id, source_id = str(UUID(school_id)), str(UUID(source_id))
    if type(dry_run) is not bool or connection.info.transaction_status != 0:
        raise ValueError("BOOK_WORLD_REQUIRES_IDLE_CONNECTION")
    run_id = publication["run"]["run_id"]
    change_id = world_uuid("changeset", school_id, run_id)
    event_id = world_uuid("event", school_id, run_id)
    # A reused run ID is immutable even when its input changes.
    payload = {"schema": WORLD_SCHEMA, "publication_hash": publication["publication_hash"],
               "run": publication["run"], "source_id": source_id,
               "stable_keys": [r["stable_key"] for r in publication["records"]],
               "version_ids": [world_uuid("version", school_id, r["stable_key"] + ":" + publication_version(r, receipt))
                               for r in publication["records"]]}
    payload_hash = digest(payload)
    inserted, version_ids = 0, []
    with connection.transaction(force_rollback=dry_run):
        with connection.cursor(row_factory=tuple_row) as cur:
            cur.execute("SET LOCAL lock_timeout = '5s'")
            cur.execute("SET LOCAL statement_timeout = '15s'")
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"book-world-run:{school_id}:{run_id}",))
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"book-world:{school_id}:{source_id}",))
            stored_locator = source_locator(cur, school_id, source_id, publication["source"]["locator"])
            if needs_asset_binding(stored_locator):
                require_source_asset(cur, school_id, source_id, publication["source"], receipt)
            cur.execute("SELECT payload_hash FROM public.domain_event WHERE event_id=%s", (event_id,))
            prior = cur.fetchone()
            if prior and prior[0] != payload_hash:
                raise ValueError("BOOK_WORLD_RUN_CONFLICT")
            cur.execute("INSERT INTO public.changeset (changeset_id,command_id,school_id,status,correlation_id) VALUES (%s,%s,%s,'started',%s) ON CONFLICT (school_id,command_id) DO NOTHING",
                        (change_id, change_id, school_id, change_id))
            for record in publication["records"]:
                key, content = record["stable_key"], record["content"]
                item_id = world_uuid("item", school_id, key)
                version_hash = publication_version(record, receipt)
                version_id = world_uuid("version", school_id, key + ":" + version_hash)
                version_ids.append(version_id)
                provenance = {"schema": WORLD_SCHEMA, "version_hash": version_hash,
                              "review_receipt": receipt, "source_id": source_id}
                cur.execute("INSERT INTO public.knowledge_item (knowledge_item_id,school_id,stable_key,knowledge_type,title) VALUES (%s,%s,%s,'book_atom',%s) ON CONFLICT (school_id,stable_key) DO NOTHING",
                            (item_id, school_id, key, publication["source"]["title"]))
                cur.execute("SELECT knowledge_item_id FROM public.knowledge_item WHERE school_id=%s AND stable_key=%s", (school_id, key))
                row = cur.fetchone()
                if row is None or str(row[0]) != item_id:
                    raise ValueError("BOOK_WORLD_LEGACY_ID_CONFLICT")
                cur.execute("SELECT content,provenance,status FROM public.knowledge_version WHERE knowledge_version_id=%s", (version_id,))
                existing = cur.fetchone()
                if existing:
                    if existing[0] != content or existing[1] != provenance or existing[2] not in {"staged", "active"}:
                        raise ValueError("BOOK_WORLD_VERSION_CONFLICT_OR_RETIRED")
                else:
                    # No implicit replacement: any earlier version requires a separate reviewed migration.
                    cur.execute("SELECT 1 FROM public.knowledge_version WHERE knowledge_item_id=%s LIMIT 1", (item_id,))
                    if cur.fetchone():
                        raise ValueError("BOOK_WORLD_VERSION_MIGRATION_REQUIRED")
                    cur.execute("""INSERT INTO public.knowledge_version
                        (knowledge_version_id,knowledge_item_id,version_no,content,authority_class,
                         review_status,bidding_system_key,method_version,provenance,status)
                        VALUES (%s,%s,1,%s::jsonb,'external','reviewed','SYSTEM_NEUTRAL',%s,%s::jsonb,'staged')""",
                        (version_id, item_id, canonical_json(content), WORLD_SCHEMA, canonical_json(provenance)))
                    inserted += 1
                cur.execute("""INSERT INTO public.knowledge_version_source
                    (knowledge_version_id,source_id,relation_type,source_locator)
                    VALUES (%s,%s,'derived_from',%s::jsonb) ON CONFLICT DO NOTHING""",
                    (version_id, source_id, canonical_json(content["citation"])))
                cur.execute("SELECT source_locator FROM public.knowledge_version_source WHERE knowledge_version_id=%s AND source_id=%s AND relation_type='derived_from'", (version_id, source_id))
                if cur.fetchone()[0] != content["citation"]:
                    raise ValueError("BOOK_WORLD_LOCATOR_CONFLICT")
            cur.execute("""INSERT INTO public.domain_event
                (event_id,school_id,partition_key,event_type,aggregate_id,aggregate_type,aggregate_version,
                 changeset_id,correlation_id,idempotency_namespace,idempotency_key,payload_hash,payload)
                VALUES (%s,%s,'book-world','BookWorldRecorded',%s,'book_world_run',1,%s,%s,
                        'book-world',%s,%s,%s::jsonb) ON CONFLICT (event_id) DO NOTHING""",
                (event_id, school_id, change_id, change_id, change_id, run_id, payload_hash, canonical_json(payload)))
            cur.execute("INSERT INTO public.outbox_message (changeset_id,event_id) VALUES (%s,%s) ON CONFLICT (event_id) DO NOTHING", (change_id, event_id))
            if before_commit:
                before_commit()
            cur.execute("UPDATE public.changeset SET status='committed',committed_at=COALESCE(committed_at,now()) WHERE changeset_id=%s AND status IN ('started','committed')", (change_id,))
            if cur.rowcount != 1:
                raise ValueError("BOOK_WORLD_CHANGESET_NOT_COMMITTED")
    return {"schema": WORLD_SCHEMA, "changeset_id": change_id, "version_ids": version_ids,
            "new_versions": inserted, "authority_lane": "WORLD_EXTERNAL",
            "publication_hash": publication["publication_hash"],
            "outbox_delivery": "REQUIRES_READBACK", "canon_activation_performed": False,
            "dry_run": dry_run, "committed": not dry_run}


def publish_book_world(connection, *, school_id: str, changeset_id: str,
                       delivery_receipt: dict, dry_run=False, before_commit=None) -> dict:
    """Expose only a complete, delivered batch; trusted operator call, no HTTP route."""
    from bridge_contracts.book_material import _fields, _require, _text
    _fields(delivery_receipt, {"schema", "publication_hash", "catalog_receipt_ref"}, "DELIVERY_FIELDS")
    _require(delivery_receipt["schema"] == "book-world-delivery-v1"
             and _text(delivery_receipt["catalog_receipt_ref"]), "CATALOG_DELIVERY_RECEIPT_REQUIRED")
    school_id, changeset_id = str(UUID(school_id)), str(UUID(changeset_id))
    if type(dry_run) is not bool or connection.info.transaction_status != 0:
        raise ValueError("BOOK_WORLD_REQUIRES_IDLE_CONNECTION")
    publish_id = world_uuid("publish", school_id, changeset_id)
    event_id = world_uuid("publish-event", school_id, changeset_id)
    payload_hash = digest(delivery_receipt)
    with connection.transaction(force_rollback=dry_run):
        with connection.cursor(row_factory=tuple_row) as cur:
            cur.execute("SET LOCAL lock_timeout = '5s'")
            cur.execute("SET LOCAL statement_timeout = '15s'")
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"book-world-publish:{changeset_id}",))
            cur.execute("""SELECT e.payload FROM public.domain_event e
                JOIN public.changeset c ON c.changeset_id=e.changeset_id
                JOIN public.outbox_message o ON o.event_id=e.event_id
                WHERE e.changeset_id=%s AND e.school_id=%s AND c.school_id=%s
                  AND e.event_type='BookWorldRecorded' AND c.status='committed' AND o.status='published'""",
                (changeset_id, school_id, school_id))
            rows = cur.fetchall()
            if len(rows) != 1 or rows[0][0]["publication_hash"] != delivery_receipt["publication_hash"]:
                raise ValueError("BOOK_WORLD_DELIVERY_NOT_COMPLETE")
            versions = rows[0][0]["version_ids"]
            if not 0 < len(versions) <= 3 or len(set(versions)) != len(versions):
                raise ValueError("BOOK_WORLD_PUBLICATION_BUDGET")
            cur.execute("""SELECT kv.knowledge_version_id,kv.content,kv.provenance,ki.stable_key
                FROM public.knowledge_version kv
                JOIN public.knowledge_item ki ON ki.knowledge_item_id=kv.knowledge_item_id
                WHERE ki.school_id=%s AND kv.knowledge_version_id=ANY(%s::uuid[])
                  AND kv.authority_class='external' AND kv.review_status='reviewed'
                  AND kv.method_version=%s AND kv.status IN ('staged','active')
                  AND NOT EXISTS (SELECT 1 FROM public.canon_activation ca WHERE ca.knowledge_version_id=kv.knowledge_version_id)
                  AND EXISTS (SELECT 1 FROM public.knowledge_version_source link
                    JOIN public.source src ON src.source_id=link.source_id
                    WHERE link.knowledge_version_id=kv.knowledge_version_id AND src.status='active'
                      AND src.school_id=ki.school_id AND link.source_locator=kv.content->'citation'
                      AND (src.canonical_locator=kv.content->'citation'->>'locator'
                        OR (src.canonical_locator='drive:' || substring(
                          kv.content->'citation'->>'locator' FROM
                          '^https://drive[.]google[.]com/file/d/([A-Za-z0-9_-]{20,128})/view$')
                          AND EXISTS (SELECT 1 FROM public.source_asset sa JOIN public.asset a
                            ON a.asset_id=sa.asset_id WHERE sa.source_id=src.source_id AND sa.relation_type='embodies'
                            AND a.school_id=ki.school_id AND a.checksum_algorithm='sha256'
                            AND a.checksum_value=kv.content->'citation'->>'rendition_sha256'
                            AND to_jsonb(a.byte_size)=kv.provenance->'review_receipt'->'source_size_bytes'
                            AND a.immutable_flag IS TRUE AND a.mime_type='application/pdf'))))
                FOR UPDATE OF kv""", (school_id, versions, WORLD_SCHEMA))
            records = cur.fetchall()
            if len(records) != len(versions):
                raise ValueError("BOOK_WORLD_PUBLICATION_SCOPE")
            for _, content, provenance, key in records:
                if provenance["version_hash"] != publication_version({"stable_key": key, "content": content}, provenance["review_receipt"]):
                    raise ValueError("BOOK_WORLD_PUBLICATION_HASH")
            cur.execute("SELECT payload_hash FROM public.domain_event WHERE event_id=%s", (event_id,))
            prior = cur.fetchone()
            if prior and prior[0] != payload_hash:
                raise ValueError("BOOK_WORLD_DELIVERY_REPLAY_CONFLICT")
            cur.execute("INSERT INTO public.changeset (changeset_id,command_id,school_id,status,correlation_id) VALUES (%s,%s,%s,'started',%s) ON CONFLICT (school_id,command_id) DO NOTHING",
                        (publish_id, publish_id, school_id, publish_id))
            cur.execute("UPDATE public.knowledge_version SET status='active' WHERE knowledge_version_id=ANY(%s::uuid[])", (versions,))
            cur.execute("""INSERT INTO public.domain_event
                (event_id,school_id,partition_key,event_type,aggregate_id,aggregate_type,aggregate_version,
                 changeset_id,correlation_id,idempotency_namespace,idempotency_key,payload_hash,payload)
                VALUES (%s,%s,'book-world','BookWorldPublished',%s,'book_world_publication',1,%s,%s,
                        'book-world-publication',%s,%s,%s::jsonb) ON CONFLICT(event_id) DO NOTHING""",
                (event_id, school_id, publish_id, publish_id, publish_id, changeset_id, payload_hash, canonical_json(delivery_receipt)))
            cur.execute("INSERT INTO public.outbox_message (changeset_id,event_id) VALUES (%s,%s) ON CONFLICT(event_id) DO NOTHING", (publish_id, event_id))
            cur.execute("UPDATE public.changeset SET status='committed',committed_at=COALESCE(committed_at,now()) WHERE changeset_id=%s AND status IN ('started','committed')", (publish_id,))
            if cur.rowcount != 1:
                raise ValueError("BOOK_WORLD_PUBLICATION_CHANGESET")
            if before_commit:
                before_commit()
    return {"changeset_id": publish_id, "version_ids": versions, "publication": "DRY_RUN_NOT_PUBLISHED" if dry_run else "WORLD_VERIFIED",
            "canon_activation_performed": False, "dry_run": dry_run, "committed": not dry_run}


def retire_book_world(connection, *, school_id: str, version_ids: list[str]) -> int:
    """Rollback visibility without deleting evidence or restoring other versions."""
    school_id = str(UUID(school_id))
    if not 0 < len(version_ids) <= 3 or len(set(version_ids)) != len(version_ids):
        raise ValueError("BOOK_WORLD_ROLLBACK_BUDGET")
    version_ids = [str(UUID(v)) for v in version_ids]
    if connection.info.transaction_status != 0:
        raise ValueError("BOOK_WORLD_REQUIRES_IDLE_CONNECTION")
    with connection.transaction():
        with connection.cursor(row_factory=tuple_row) as cur:
            cur.execute("""SELECT kv.knowledge_version_id FROM public.knowledge_version kv
                JOIN public.knowledge_item ki ON ki.knowledge_item_id=kv.knowledge_item_id
                WHERE ki.school_id=%s AND kv.knowledge_version_id=ANY(%s::uuid[])
                  AND kv.authority_class='external' AND kv.method_version=%s
                  AND NOT EXISTS (SELECT 1 FROM public.canon_activation ca WHERE ca.knowledge_version_id=kv.knowledge_version_id)
                FOR UPDATE OF kv""", (school_id, version_ids, WORLD_SCHEMA))
            if len(cur.fetchall()) != len(version_ids):
                raise ValueError("BOOK_WORLD_ROLLBACK_SCOPE")
            cur.execute("UPDATE public.knowledge_version SET status='archived' WHERE knowledge_version_id=ANY(%s::uuid[])", (version_ids,))
            return cur.rowcount
