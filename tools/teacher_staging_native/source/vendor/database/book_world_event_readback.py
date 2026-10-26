"""Read selected Books event metadata from the caller's trusted DB connection.

No connection provisioning, endpoint, grants, inferred receipts or permission.
"""
from uuid import UUID
from bridge_contracts.book_world_events import BookWorldEventError

COLUMNS = ("event_id", "event_type", "aggregate_version", "school_id",
           "changeset_id", "partition_key", "payload", "payload_hash",
           "changeset_status", "outbox_status", "event_position")


def read_book_world_event(cursor, *, school_id, event_id=None, changeset_id=None):
    try:
        school = str(UUID(str(school_id)))
        key = str(UUID(str(event_id if event_id is not None else changeset_id)))
    except (ValueError, TypeError, AttributeError):
        raise BookWorldEventError("BOOK_WORLD_OUTBOX_METADATA") from None
    if (event_id is None) == (changeset_id is None):
        raise BookWorldEventError("BOOK_WORLD_OUTBOX_METADATA")
    where = "e.event_id" if event_id is not None else "e.changeset_id"
    cursor.execute("""
        SELECT e.event_id,e.event_type,e.aggregate_version,e.school_id,
               e.changeset_id,e.partition_key,e.payload,e.payload_hash,
               c.status AS changeset_status,o.status AS outbox_status,e.event_position
          FROM public.domain_event e
          JOIN public.changeset c ON c.changeset_id=e.changeset_id AND c.school_id=e.school_id
          JOIN public.outbox_message o ON o.event_id=e.event_id AND o.changeset_id=e.changeset_id
         WHERE e.school_id=%s AND """ + where + """=%s
        """, (school, key))
    rows = cursor.fetchall()
    if len(rows) != 1:
        raise BookWorldEventError("BOOK_WORLD_OUTBOX_EVENT_UNAVAILABLE")
    event = dict(zip(COLUMNS, rows[0], strict=True))
    for field in ("event_id", "school_id", "changeset_id"):
        event[field] = str(event[field])
    return event
