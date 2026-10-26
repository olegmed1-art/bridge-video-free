#!/usr/bin/env python3
"""Publish committed Bridge Video outbox rows through the guarded DB boundary."""
from __future__ import annotations

from copy import deepcopy
import uuid

import psycopg

from database.runtime_worker_preflight import normalize_dsn


def publish_changeset_outbox(raw_dsn: str, changeset_id: str | uuid.UUID, *,
                             authorization_provider=None) -> dict[str, object]:
    """Publish one committed changeset only when every event is allowed.

    ``public.publish_outbox_event`` owns event-position allocation and rejects
    non-committed changesets. Replaying this helper is safe: already-published
    rows retain their existing partition-local event position. An event payload
    with ``publication_authorization_status="blocked"`` rejects the entire batch
    before any publication call, including retries of already-published events.
    Missing, null, or unrecognized status is also fail-closed for the batch.
    """
    dsn = normalize_dsn(raw_dsn)
    if not dsn:
        raise ValueError("BRIDGE_WORKER_DATABASE_URL is not configured")
    change_id = uuid.UUID(str(changeset_id))
    positions: list[int] = []
    with psycopg.connect(
        dsn,
        connect_timeout=10,
        application_name="bridge-video-outbox-publisher",
    ) as connection:
        with connection.transaction():
            result = publish_changeset_outbox_in_transaction(
                connection, change_id, authorization_provider=authorization_provider)
        connection.commit()
    return result


def publish_changeset_outbox_in_transaction(connection, changeset_id, *,
        authorization_provider=None, expected_book_event_id=None, before_publication=None):
    """Caller owns transaction/commit; authority guard must retain its locks.

    No DSN, connection creation or commit here. Legacy wrapper remains separate.
    """
    if connection.autocommit or connection.info.transaction_status != 2:
        raise ValueError("OUTBOX_AUTHORITY_ACTIVE_TRANSACTION_REQUIRED")
    change_id = uuid.UUID(str(changeset_id))
    positions = []
    with connection.cursor() as cursor:
        cursor.execute(
            """
            WITH locked_outbox AS MATERIALIZED (
                SELECT outbox_id, event_id, created_at
                  FROM public.outbox_message
                 WHERE changeset_id=%s
                 ORDER BY created_at, outbox_id
                 FOR UPDATE
            )
            SELECT o.outbox_id, e.event_id,
                   e.payload->>'publication_authorization_status',
                   e.event_type, e.school_id, e.changeset_id, e.payload
              FROM locked_outbox o
              LEFT JOIN public.domain_event e ON e.event_id=o.event_id
             ORDER BY o.created_at, o.outbox_id
            """,
            (change_id,),
        )
        outbox_rows = cursor.fetchall()
        if expected_book_event_id is not None:
            if len(outbox_rows) != 1 or str(outbox_rows[0][1]) != expected_book_event_id:
                raise RuntimeError("OUTBOX_AUTHORITY_EVENT_SCOPE")
        # Lock outbox bindings before the join so a worker cannot rebind an
        # event between the status check and publish_outbox_event's reread.
        # A missing/invisible event is not a legacy event without a status.
        if any(row[1] is None for row in outbox_rows):
            raise RuntimeError("OUTBOX_EVENT_UNAVAILABLE")
        # Check the whole batch first: a later blocked event must not allow
        # earlier rows to reach the publication boundary. Do not log payloads.
        if any(row[2] == "blocked" for row in outbox_rows):
            raise RuntimeError("OUTBOX_PUBLICATION_AUTHORIZATION_BLOCKED")
        if any(row[2] != "allowed" for row in outbox_rows):
            raise RuntimeError("OUTBOX_PUBLICATION_AUTHORIZATION_UNKNOWN")
        # Every Books row must pass actual metadata, trusted authorization
        # and causal-parent checks before ANY row reaches the RPC.
        for _, event_id, _, event_type, school_id, event_changeset, payload in outbox_rows:
            if event_type in ("BookWorldRecorded", "BookWorldPublished") or (
                    isinstance(payload, dict) and ("event_contract" in payload
                        or payload.get("schema") == "book-world-outbox-v2")):
                # Preserve the dependency surface of video-only packages.
                from bridge_contracts.book_world_events import (
                    BookWorldEventError, validate_book_outbox_event,
                )
                from database.book_world_event_readback import read_book_world_event
                try:
                    if str(event_changeset) != str(change_id):
                        raise BookWorldEventError("BOOK_WORLD_OUTBOX_CHANGESET")
                    actual = read_book_world_event(cursor, school_id=school_id, event_id=event_id)
                    if (actual["payload"] != payload or actual["event_type"] != event_type
                            or actual["changeset_id"] != str(change_id)):
                        raise BookWorldEventError("BOOK_WORLD_OUTBOX_METADATA")
                    parent = None
                    if event_type == "BookWorldPublished":
                        parent_id = payload.get("recorded_event_id") if isinstance(payload, dict) else None
                        parent = read_book_world_event(cursor, school_id=school_id, event_id=parent_id)
                    if not callable(authorization_provider):
                        raise BookWorldEventError("BOOK_WORLD_OUTBOX_AUTHORIZATION_REQUIRED")
                    # The callback is a trusted host service, not a payload input.
                    # It cannot mutate the actual metadata used for validation.
                    try:
                        authorization = authorization_provider({
                            "recorded_event": deepcopy(parent if parent is not None else actual)})
                    except Exception:
                        raise BookWorldEventError("BOOK_WORLD_OUTBOX_AUTHORIZATION_UNAVAILABLE") from None
                    validate_book_outbox_event(actual, authorization=authorization,
                                               recorded_event=parent)
                except BookWorldEventError:
                    raise RuntimeError("OUTBOX_BOOK_EVENT_INVALID") from None
        if before_publication is not None:
            before_publication()
        for outbox_id, *_ in outbox_rows:
            cursor.execute(
                "SELECT public.publish_outbox_event(%s)",
                (outbox_id,),
            )
            row = cursor.fetchone()
            if not row or row[0] is None:
                raise RuntimeError("OUTBOX_PUBLISH_POSITION_MISSING")
            positions.append(int(row[0]))
    return {
        "changeset_id": str(change_id),
        "published_count": len(positions),
        "event_positions": positions,
    }


__all__ = ["publish_changeset_outbox", "publish_changeset_outbox_in_transaction"]
