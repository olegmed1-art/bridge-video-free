#!/usr/bin/env python3
"""Publish committed Bridge Video outbox rows through the guarded DB boundary."""
from __future__ import annotations

import uuid

import psycopg

from database.runtime_worker_preflight import normalize_dsn


def publish_changeset_outbox(raw_dsn: str, changeset_id: str | uuid.UUID) -> dict[str, object]:
    """Publish one committed changeset unless an event explicitly blocks it.

    ``public.publish_outbox_event`` owns event-position allocation and rejects
    non-committed changesets. Replaying this helper is safe: already-published
    rows retain their existing partition-local event position. An event payload
    with ``publication_authorization_status="blocked"`` rejects the entire batch
    before any publication call, including retries of already-published events.
    Other values and legacy events without the field keep their existing behavior.
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
                       e.payload->>'publication_authorization_status'
                  FROM locked_outbox o
                  LEFT JOIN public.domain_event e ON e.event_id=o.event_id
                 ORDER BY o.created_at, o.outbox_id
                """,
                (change_id,),
            )
            outbox_rows = cursor.fetchall()
            # Lock outbox bindings before the join so a worker cannot rebind an
            # event between the status check and publish_outbox_event's reread.
            # A missing/invisible event is not a legacy event without a status.
            if any(event_id is None for _, event_id, _ in outbox_rows):
                raise RuntimeError("OUTBOX_EVENT_UNAVAILABLE")
            # Check the whole batch first: a later blocked event must not allow
            # earlier rows to reach the publication boundary. Do not log payloads.
            if any(status == "blocked" for _, _, status in outbox_rows):
                raise RuntimeError("OUTBOX_PUBLICATION_AUTHORIZATION_BLOCKED")
            for outbox_id, _, _ in outbox_rows:
                cursor.execute(
                    "SELECT public.publish_outbox_event(%s)",
                    (outbox_id,),
                )
                row = cursor.fetchone()
                if not row or row[0] is None:
                    raise RuntimeError("OUTBOX_PUBLISH_POSITION_MISSING")
                positions.append(int(row[0]))
        connection.commit()
    return {
        "changeset_id": str(change_id),
        "published_count": len(positions),
        "event_positions": positions,
    }


__all__ = ["publish_changeset_outbox"]
