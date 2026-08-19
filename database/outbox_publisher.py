#!/usr/bin/env python3
"""Publish committed Bridge Video outbox rows through the guarded DB boundary."""
from __future__ import annotations

import uuid

import psycopg

from database.runtime_worker_preflight import normalize_dsn


def publish_changeset_outbox(raw_dsn: str, changeset_id: str | uuid.UUID) -> dict[str, object]:
    """Publish every outbox row for one committed changeset, idempotently.

    ``public.publish_outbox_event`` owns event-position allocation and rejects
    non-committed changesets. Replaying this helper is safe: already-published
    rows retain their existing partition-local event position.
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
                SELECT outbox_id
                  FROM public.outbox_message
                 WHERE changeset_id=%s
                 ORDER BY created_at, outbox_id
                """,
                (change_id,),
            )
            outbox_ids = [row[0] for row in cursor.fetchall()]
            for outbox_id in outbox_ids:
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