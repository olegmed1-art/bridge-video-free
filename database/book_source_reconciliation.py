"""Bounded source/asset reconciliation; caller owns admission and connection.

No source updates, DDL, grants, credentials or network calls. Dry-run executes the
same inserts in a forced rollback. Unknown identity-table effects fail closed.
"""
import re
from uuid import UUID, NAMESPACE_URL, uuid5
from bridge_contracts.book_source_identity import book_source_matches
from bridge_contracts.book_world import build_world_publication
from bridge_contracts.book_source_review import validate_identity_review
from database.source_registration import (
    SourceRegistrationError, check_source_registration, lock_source_registration,
)


_DRIVE_VIEW = re.compile(r"https://drive\.google\.com/file/d/([A-Za-z0-9_-]{20,128})/view\Z")


def new_book_source_id(school_id, locator):
    """Stable identity for a new source row; existing registry UUIDs take precedence."""
    school_id = str(UUID(school_id))
    return str(uuid5(NAMESPACE_URL, "bridge-book-source:" + school_id + ":" + locator))


def _source_aliases(locator):
    match = _DRIVE_VIEW.fullmatch(locator)
    return (locator, "drive:" + match[1]) if match else (locator, locator)


def _register_missing_source(cur, school_id, source_id, source, identity_receipt):
    """Insert one exact source inside the caller's identity transaction, or reuse it."""
    cur.execute("SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s,0))",
                ("book-source:" + school_id + ":" + source["locator"],))
    # The table lock also serializes writers that do not use our advisory key.
    cur.execute("LOCK TABLE public.source IN SHARE ROW EXCLUSIVE MODE")
    cur.execute("""SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_trigger t
        WHERE t.tgrelid='public.source'::pg_catalog.regclass
          AND NOT t.tgisinternal AND t.tgenabled<>'D'
          AND (t.tgtype::integer & 4) <> 0)
        OR EXISTS(SELECT 1 FROM pg_catalog.pg_rewrite r
          WHERE r.ev_class='public.source'::pg_catalog.regclass
            AND r.ev_type='3')""")
    if cur.fetchone()[0]:
        raise ValueError("BOOK_SOURCE_IDENTITY_EFFECT_REVIEW_REQUIRED")
    cur.execute("SELECT status FROM public.school WHERE school_id=%s FOR SHARE", (school_id,))
    school = cur.fetchone()
    if school is None or school[0] != "active":
        raise ValueError("BOOK_SOURCE_SCHOOL_INACTIVE")
    cur.execute("""SELECT source_id,canonical_locator,status,source_type,title
        FROM public.source WHERE school_id=%s AND canonical_locator IN (%s,%s)
        FOR SHARE""", (school_id, *_source_aliases(source["locator"])))
    rows = cur.fetchall()
    if len(rows) > 1:
        raise ValueError("BOOK_SOURCE_DUPLICATE_LOCATOR")
    if rows:
        found_id, stored, status, source_type, title = rows[0]
        if (str(found_id) != source_id or stored != identity_receipt["stored_locator"]
                or status != "active" or source_type != "book" or title != source["title"]):
            raise ValueError("BOOK_SOURCE_REGISTRY_CONFLICT")
        return 0
    if (source_id != new_book_source_id(school_id, source["locator"])
            or identity_receipt["stored_locator"] != source["locator"]):
        raise ValueError("BOOK_SOURCE_IDENTITY_DECISION_CONFLICT")
    cur.execute("""INSERT INTO public.source
        (source_id,school_id,source_type,title,canonical_locator,status)
        VALUES(%s,%s,'book',%s,%s,'active')""",
        (source_id, school_id, source["title"], source["locator"]))
    if cur.rowcount != 1:
        raise ValueError("BOOK_SOURCE_IDENTITY_BUDGET")
    return 1


def source_locator(cur, school_id, source_id, citation, *, expected_stored=None):
    cur.execute("SELECT canonical_locator FROM public.source WHERE source_id=%s "
                "AND school_id=%s AND status='active' FOR SHARE", (source_id, school_id))
    row = cur.fetchone()
    if row is None or not book_source_matches(row[0], citation):
        raise ValueError("BOOK_WORLD_SOURCE_BINDING_MISSING")
    if expected_stored is not None and row[0] != expected_stored:
        raise ValueError("BOOK_SOURCE_IDENTITY_REVIEW_BINDING")
    return row[0]


def _source_pdf_bindings(cur, school_id, source_id, source, receipt, *, asset_id=None):
    """Inspect every PDF/embodies link, including links with a different SHA."""
    cur.execute("""SELECT a.asset_id,sa.relation_type,a.school_id,a.asset_type,a.mime_type,
        a.byte_size,a.checksum_algorithm,a.checksum_value,a.immutable_flag
        FROM public.source_asset sa JOIN public.asset a ON a.asset_id=sa.asset_id
        WHERE sa.source_id=%s AND (sa.relation_type='embodies' OR a.asset_type='pdf'
            OR a.mime_type='application/pdf') FOR SHARE OF a,sa""", (source_id,))
    rows = cur.fetchall()
    for row in rows:
        if ((asset_id is not None and str(row[0]) != asset_id) or
                (row[1], str(row[2]), row[3], row[4], row[5],
                 row[6], row[7], row[8]) != (
                "embodies", school_id, "pdf", "application/pdf",
                receipt["source_size_bytes"], "sha256", source["rendition_sha256"], True)):
            raise ValueError("BOOK_SOURCE_ASSET_CONFLICT")
    if len(rows) > 1:
        raise ValueError("BOOK_SOURCE_ASSET_CONFLICT")
    return rows


def require_source_asset(cur, school_id, source_id, source, receipt, *, asset_id=None):
    rows = _source_pdf_bindings(cur, school_id, source_id, source, receipt,
                                asset_id=asset_id)
    if len(rows) != 1:
        raise ValueError("BOOK_SOURCE_ASSET_BINDING_REQUIRED")
    found = str(rows[0][0])
    return found


def reconcile_book_source_asset(connection, bundle, receipt, *, school_id, source_id,
                                identity_receipt, dry_run=False, before_commit=None,
                                register_missing_source=False):
    """At most one source, asset and link; exact replay inserts none.

    Requires a fully bound trusted source review. Never creates/publishes an atom.
    Errors/dry-runs roll back. On later visibility retirement, retain identity
    evidence rather than deleting shared source/asset records.
    """
    from psycopg.rows import tuple_row
    publication = build_world_publication(bundle, receipt, identity_receipt)
    school_id, source_id = str(UUID(school_id)), str(UUID(source_id))
    if (type(dry_run) is not bool or type(register_missing_source) is not bool
            or connection.info.transaction_status != 0):
        raise ValueError("BOOK_SOURCE_REQUIRES_IDLE_CONNECTION")
    source = publication["source"]
    validate_identity_review(source, receipt, identity_receipt, school_id=school_id, source_id=source_id)
    added_source = added_asset = added_link = 0
    with connection.transaction(force_rollback=dry_run):
        with connection.cursor(row_factory=tuple_row) as cur:
            # The table locks must be followed by fresh statement snapshots.
            # A caller's REPEATABLE READ snapshot could miss a competing link.
            cur.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            cur.execute("SET LOCAL lock_timeout='5s'")
            cur.execute("SET LOCAL statement_timeout='15s'")
            # Every participating writer starts with the same source barrier.
            lock_source_registration(cur)
            try:
                check_source_registration(cur, school_id=school_id, source_id=source_id,
                    locator=source["locator"], expected_stored=identity_receipt["stored_locator"],
                    source_type="book" if register_missing_source else None)
            except SourceRegistrationError as exc:
                code = ("BOOK_SOURCE_DUPLICATE_LOCATOR" if str(exc) ==
                        "SOURCE_REGISTRATION_DUPLICATE_ALIAS" else
                        "BOOK_SOURCE_REGISTRY_CONFLICT")
                raise ValueError(code) from None
            if register_missing_source:
                added_source = _register_missing_source(
                    cur, school_id, source_id, source, identity_receipt)
            cur.execute("SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s,0))",
                        ("book-source-asset:" + source["rendition_sha256"],))
            source_locator(cur, school_id, source_id, source["locator"],
                           expected_stored=identity_receipt["stored_locator"])
            # Serialize even writers that do not use our advisory key. INSERTs
            # into either table need ROW EXCLUSIVE and wait until this commits.
            cur.execute("LOCK TABLE public.asset,public.source_asset IN SHARE ROW EXCLUSIVE MODE")
            cur.execute("""SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_trigger t
                WHERE t.tgrelid IN ('public.asset'::pg_catalog.regclass,'public.source_asset'::pg_catalog.regclass)
                  AND NOT t.tgisinternal AND t.tgenabled<>'D')
                OR EXISTS(SELECT 1 FROM pg_catalog.pg_rewrite r
                  WHERE r.ev_class IN ('public.asset'::pg_catalog.regclass,'public.source_asset'::pg_catalog.regclass))""")
            if cur.fetchone()[0]:
                raise ValueError("BOOK_SOURCE_IDENTITY_EFFECT_REVIEW_REQUIRED")
            _source_pdf_bindings(cur, school_id, source_id, source, receipt,
                                 asset_id=identity_receipt["asset_id"])
            cur.execute("""SELECT asset_id,school_id,byte_size,immutable_flag,mime_type
                FROM public.asset WHERE checksum_algorithm='sha256' AND checksum_value=%s
                FOR SHARE""", (source["rendition_sha256"],))
            rows = cur.fetchall()
            if rows:
                if len(rows) != 1 or (str(rows[0][1]), rows[0][2], rows[0][3], rows[0][4]) != (
                        school_id, receipt["source_size_bytes"], True, "application/pdf"):
                    raise ValueError("BOOK_SOURCE_ASSET_CONFLICT")
                asset_id = str(rows[0][0])
                if asset_id != identity_receipt["asset_id"]:
                    raise ValueError("BOOK_SOURCE_IDENTITY_DECISION_CONFLICT")
            else:
                if identity_receipt["asset_action"] != "CREATE":
                    raise ValueError("BOOK_SOURCE_IDENTITY_DECISION_CONFLICT")
                asset_id = identity_receipt["asset_id"]
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
            require_source_asset(cur, school_id, source_id, source, receipt,
                                 asset_id=identity_receipt["asset_id"])
            if before_commit:
                before_commit()
    return {"asset_id": asset_id, "source_id": source_id,
            "new_source_rows": added_source, "new_asset_rows": added_asset,
            "new_link_rows": added_link, "dry_run": dry_run, "committed": not dry_run,
            "source_updated": False, "publication_performed": False}
