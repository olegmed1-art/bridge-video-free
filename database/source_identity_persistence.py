#!/usr/bin/env python3
"""Idempotent SourceIdentity persistence for Google Drive-backed school sources."""
from __future__ import annotations

import uuid
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from database.runtime_worker_preflight import normalize_dsn


def _stable_uuid(kind: str, *parts: object) -> uuid.UUID:
    seed = "|".join(str(x) for x in parts)
    return uuid.uuid5(uuid.NAMESPACE_URL, f"bridge-school:{kind}:{seed}")


def drive_source_id(source_drive_id: str) -> uuid.UUID:
    return _stable_uuid("source", "google-drive", source_drive_id)


def drive_source_native_key(source_drive_id: str) -> str:
    return f"google-drive:{source_drive_id}"


def ensure_drive_source_identity(
    raw_dsn: str,
    *,
    source_drive_id: str,
    display_name: str | None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Bind one existing source row to its stable provider-native Drive key."""
    dsn = normalize_dsn(raw_dsn)
    if not dsn:
        raise ValueError("BRIDGE_WORKER_DATABASE_URL is not configured")
    source_drive_id = str(source_drive_id or "").strip()
    if not source_drive_id:
        raise ValueError("source_drive_id is required")

    source_id = drive_source_id(source_drive_id)
    native_key = drive_source_native_key(source_drive_id)
    identity_id = _stable_uuid("source-identity", source_id, native_key)
    attributes = {
        "provider": "google_drive",
        "drive_file_id": source_drive_id,
        "locator": f"gdrive:file:{source_drive_id}",
        "identity_kind": "provider_native_file",
        "job_id": job_id,
    }

    with psycopg.connect(
        dsn,
        connect_timeout=10,
        application_name="bridge-source-identity-persistence",
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM public.source WHERE source_id=%s",
                (source_id,),
            )
            if cur.fetchone() is None:
                raise RuntimeError("SOURCE_IDENTITY_SOURCE_NOT_FOUND")
            cur.execute(
                """
                INSERT INTO public.source_identity
                    (source_identity_id, source_id, source_native_key,
                     display_name, attributes, first_seen_at, last_seen_at)
                VALUES (%s, %s, %s, %s, %s, now(), now())
                ON CONFLICT (source_id, source_native_key) DO UPDATE
                   SET display_name=COALESCE(EXCLUDED.display_name, public.source_identity.display_name),
                       attributes=public.source_identity.attributes || EXCLUDED.attributes,
                       last_seen_at=now()
                RETURNING source_identity_id, first_seen_at, last_seen_at
                """,
                (
                    identity_id,
                    source_id,
                    native_key,
                    display_name,
                    Jsonb(attributes),
                ),
            )
            stored_id, first_seen_at, last_seen_at = cur.fetchone()

    return {
        "source_identity_id": str(stored_id),
        "source_id": str(source_id),
        "source_native_key": native_key,
        "first_seen_at": first_seen_at.isoformat(),
        "last_seen_at": last_seen_at.isoformat(),
    }
