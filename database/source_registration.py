"""Shared source registration protocol; no schema changes or identity rewrites.

All participating writers acquire the source barrier before application write/row
locks or advisory locks. It remains held through the caller's transaction.
External/old writers still require release admission; this is not a DB constraint.
"""
import re
from uuid import UUID


class SourceRegistrationError(ValueError):
    """A source identity or transaction does not satisfy the shared protocol."""


_DRIVE_FILE = re.compile(
    r"(?:https://drive\.google\.com/file/d/([A-Za-z0-9_-]+)/view"
    r"|drive:([A-Za-z0-9_-]+)|gdrive:file:([A-Za-z0-9_-]+))\Z"
)


def require_drive_file_id(value):
    """Keep native-key namespaces disjoint; reject coercion and repair of IDs."""
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise SourceRegistrationError("SOURCE_REGISTRATION_DRIVE_ID_INVALID")
    return value


def source_aliases(locator):
    """Detect aliases only; never canonicalize or authorize a stored locator."""
    if not isinstance(locator, str) or not locator:
        raise SourceRegistrationError("SOURCE_REGISTRATION_LOCATOR_INVALID")
    match = _DRIVE_FILE.fullmatch(locator)
    if not match:
        if locator.startswith(("drive:", "gdrive:file:", "https://drive.google.com/file/d/")):
            raise SourceRegistrationError("SOURCE_REGISTRATION_LOCATOR_INVALID")
        return (locator,)
    file_id = next(part for part in match.groups() if part is not None)
    return tuple(dict.fromkeys((locator, "drive:" + file_id,
        "https://drive.google.com/file/d/" + file_id + "/view",
        "gdrive:file:" + file_id)))


def lock_source_registration(cur):
    """Start the shared ordering in a fresh or otherwise read-only RC transaction.

    A caller composing writers must call this before its first application write,
    row lock or advisory lock. A preheld source barrier permits later composition.
    This cannot repair lock ordering already performed by external/older code.
    """
    cur.execute("SHOW transaction_isolation")
    if cur.fetchone()[0] != "read committed":
        raise SourceRegistrationError("SOURCE_REGISTRATION_REQUIRES_READ_COMMITTED")
    cur.execute("""SELECT
        COALESCE(bool_or(l.granted AND l.locktype='relation'
            AND l.relation='public.source'::pg_catalog.regclass
            AND l.mode IN ('ShareRowExclusiveLock','ExclusiveLock','AccessExclusiveLock')),false),
        COALESCE(bool_or(l.granted AND (l.locktype='advisory'
            OR (l.locktype='relation' AND pg_catalog.left(n.nspname,3)<>'pg_'
                AND n.nspname<>'information_schema' AND l.mode<>'AccessShareLock'))),false)
        FROM pg_catalog.pg_locks l
        LEFT JOIN pg_catalog.pg_class c ON c.oid=l.relation
        LEFT JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE l.pid=pg_catalog.pg_backend_pid()""")
    barrier_held, prior_write_lock = cur.fetchone()
    if prior_write_lock and not barrier_held:
        raise SourceRegistrationError("SOURCE_REGISTRATION_LOCK_ORDER_REQUIRED")
    cur.execute("LOCK TABLE public.source IN SHARE ROW EXCLUSIVE MODE")


def source_registration_rows(cur, school_id, source_id, locator):
    """Read canonical/native file aliases plus the global UUID under the barrier.

    The native-key table is optional only for older/minimal schema surfaces. When
    present, both exact Drive key namespaces participate; arbitrary attributes or
    participant/provider keys do not establish file equivalence. EXISTS returns
    each source once even when both native-key forms are stored for that UUID.
    """
    school_id, source_id = str(UUID(str(school_id))), str(UUID(str(source_id)))
    sql = """SELECT s.source_id,s.school_id,s.canonical_locator,s.status,s.source_type,s.title
        FROM public.source AS s
        WHERE (s.school_id=%s AND s.canonical_locator=ANY(%s)) OR s.source_id=%s"""
    params = [school_id, list(source_aliases(locator)), source_id]
    match = _DRIVE_FILE.fullmatch(locator)
    if match:
        file_id = next(part for part in match.groups() if part is not None)
        cur.execute("SELECT pg_catalog.to_regclass('public.source_identity')")
        if cur.fetchone()[0] is not None:
            sql += """ OR (s.school_id=%s AND EXISTS (
                SELECT 1 FROM public.source_identity i WHERE i.source_id=s.source_id
                    AND i.source_native_key=ANY(%s)))"""
            params.extend((school_id, ["google-drive:" + file_id,
                                      "google-drive:file:" + file_id]))
    cur.execute(sql + " FOR SHARE OF s", params)
    return cur.fetchall()


def check_source_registration(cur, *, school_id, source_id, locator,
                              expected_stored=None, source_type=None):
    """Return the same UUID/file identity, or None; never adopt another UUID.

    Without an explicit receipt locator, equivalent known aliases may be reused
    in their original stored form. A receipt-bound caller requires exact equality.
    """
    rows = source_registration_rows(cur, school_id, source_id, locator)
    if len(rows) > 1:
        raise SourceRegistrationError("SOURCE_REGISTRATION_DUPLICATE_ALIAS")
    if not rows:
        return None
    row = rows[0]
    stored_matches = (row[2] in source_aliases(locator) if expected_stored is None
                      else row[2] == expected_stored)
    if ((str(row[0]), str(row[1]), row[3]) !=
            (str(UUID(str(source_id))), str(UUID(str(school_id))), "active")
            or not stored_matches
            or (source_type is not None and row[4] != source_type)):
        raise SourceRegistrationError("SOURCE_REGISTRATION_CONFLICT")
    return row


def register_source(cur, *, school_id, source_id, source_type, title, locator,
                    trust_class=None, rights_notes=None):
    """Insert one exact source or reuse it without updating any stored field.

    Incoming title drift is deliberately ignored on replay, matching existing
    video/tournament behavior. Book callers retain their stricter receipt policy.
    """
    lock_source_registration(cur)
    row = check_source_registration(cur, school_id=school_id, source_id=source_id,
                                    locator=locator, source_type=source_type)
    if row is not None:
        return 0
    cur.execute("""SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_trigger t
        WHERE t.tgrelid='public.source'::pg_catalog.regclass
          AND NOT t.tgisinternal AND t.tgenabled<>'D'
          AND (t.tgtype::integer & 4) <> 0)
        OR EXISTS(SELECT 1 FROM pg_catalog.pg_rewrite r
          WHERE r.ev_class='public.source'::pg_catalog.regclass AND r.ev_type='3')""")
    if cur.fetchone()[0]:
        raise SourceRegistrationError("SOURCE_REGISTRATION_EFFECT_REVIEW_REQUIRED")
    cur.execute("""INSERT INTO public.source
        (source_id,school_id,source_type,title,canonical_locator,trust_class,rights_notes,status)
        VALUES(%s,%s,%s,%s,%s,%s,%s,'active')""",
        (source_id, school_id, source_type, title, locator, trust_class, rights_notes))
    if cur.rowcount != 1:
        raise SourceRegistrationError("SOURCE_REGISTRATION_BUDGET")
    check_source_registration(cur, school_id=school_id, source_id=source_id,
                              locator=locator, source_type=source_type)
    return 1
