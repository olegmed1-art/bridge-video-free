"""Bounded asset reconciliation; caller owns admission and existing connection.

No source updates, DDL, grants, credentials or network calls. Dry-run executes the
same two inserts in a forced rollback. Unknown identity-table effects fail closed.
"""
from uuid import UUID
from psycopg.rows import tuple_row
from bridge_contracts.book_source_identity import book_source_matches
from bridge_contracts.book_world import build_world_publication, world_uuid


def source_locator(cur, school_id, source_id, citation):
    cur.execute("SELECT canonical_locator FROM public.source WHERE source_id=%s "
                "AND school_id=%s AND status='active' FOR SHARE", (source_id, school_id))
    row = cur.fetchone()
    if row is None or not book_source_matches(row[0], citation):
        raise ValueError("BOOK_WORLD_SOURCE_BINDING_MISSING")
    return row[0]


def require_source_asset(cur, school_id, source_id, source, receipt):
    cur.execute("""SELECT a.asset_id FROM public.source_asset sa JOIN public.asset a
        ON a.asset_id=sa.asset_id WHERE sa.source_id=%s AND sa.relation_type='embodies' AND a.school_id=%s
        AND a.checksum_algorithm='sha256' AND a.checksum_value=%s
        AND a.byte_size=%s AND a.immutable_flag IS TRUE
        AND a.mime_type='application/pdf' LIMIT 2 FOR SHARE OF a,sa""",
        (source_id, school_id, source["rendition_sha256"], receipt["source_size_bytes"]))
    rows = cur.fetchall()
    if len(rows) != 1:
        raise ValueError("BOOK_SOURCE_ASSET_BINDING_REQUIRED")
    return str(rows[0][0])


def reconcile_book_source_asset(connection, bundle, receipt, *, school_id, source_id,
                                dry_run=False, before_commit=None):
    """At most one asset and one link; replay inserts neither.

    Requires a fully bound trusted source review. Never creates/publishes an atom.
    Errors/dry-runs roll back. On later visibility retirement, retain identity
    evidence rather than deleting shared source/asset records.
    """
    publication = build_world_publication(bundle, receipt)
    school_id, source_id = str(UUID(school_id)), str(UUID(source_id))
    if type(dry_run) is not bool or connection.info.transaction_status != 0:
        raise ValueError("BOOK_SOURCE_REQUIRES_IDLE_CONNECTION")
    source = publication["source"]
    added_asset = added_link = 0
    with connection.transaction(force_rollback=dry_run):
        with connection.cursor(row_factory=tuple_row) as cur:
            cur.execute("SET LOCAL lock_timeout='5s'")
            cur.execute("SET LOCAL statement_timeout='15s'")
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                        ("book-source-asset:" + source["rendition_sha256"],))
            source_locator(cur, school_id, source_id, source["locator"])
            # Prevent concurrent ALTER TABLE while inspecting identity effects.
            cur.execute("LOCK TABLE public.asset,public.source_asset IN ROW EXCLUSIVE MODE")
            cur.execute("""SELECT EXISTS(SELECT 1 FROM pg_trigger t
                WHERE t.tgrelid IN ('public.asset'::regclass,'public.source_asset'::regclass)
                  AND NOT t.tgisinternal AND t.tgenabled<>'D')
                OR EXISTS(SELECT 1 FROM pg_rewrite r
                  WHERE r.ev_class IN ('public.asset'::regclass,'public.source_asset'::regclass))""")
            if cur.fetchone()[0]:
                raise ValueError("BOOK_SOURCE_IDENTITY_EFFECT_REVIEW_REQUIRED")
            cur.execute("""SELECT asset_id,school_id,byte_size,immutable_flag,mime_type
                FROM public.asset WHERE checksum_algorithm='sha256' AND checksum_value=%s
                FOR SHARE""", (source["rendition_sha256"],))
            rows = cur.fetchall()
            if rows:
                if len(rows) != 1 or (str(rows[0][1]), rows[0][2], rows[0][3], rows[0][4]) != (
                        school_id, receipt["source_size_bytes"], True, "application/pdf"):
                    raise ValueError("BOOK_SOURCE_ASSET_CONFLICT")
                asset_id = str(rows[0][0])
            else:
                asset_id = world_uuid("asset", school_id, source["rendition_sha256"])
                cur.execute("""INSERT INTO public.asset(asset_id,school_id,asset_type,mime_type,
                    byte_size,checksum_algorithm,checksum_value,immutable_flag)
                    VALUES(%s,%s,'pdf','application/pdf',%s,'sha256',%s,true)""",
                    (asset_id, school_id, receipt["source_size_bytes"], source["rendition_sha256"]))
                added_asset = cur.rowcount
            cur.execute("""INSERT INTO public.source_asset(source_id,asset_id,relation_type)
                VALUES(%s,%s,'embodies') ON CONFLICT DO NOTHING""", (source_id, asset_id))
            added_link = cur.rowcount
            if added_asset not in (0, 1) or added_link not in (0, 1):
                raise ValueError("BOOK_SOURCE_IDENTITY_BUDGET")
            require_source_asset(cur, school_id, source_id, source, receipt)
            if before_commit:
                before_commit()
    return {"asset_id": asset_id, "source_id": source_id, "new_asset_rows": added_asset,
            "new_link_rows": added_link, "dry_run": dry_run, "committed": not dry_run,
            "source_updated": False, "publication_performed": False}
