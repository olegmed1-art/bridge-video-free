"""Synchronous Drive readback for one approved fixed-subset batch.

The existing Oracle resident reads the source directly from Google Drive. This
collector records that fresh provider response for the exact leased job; no
GitHub comment, external callback, or new provider credential is involved.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import UUID, uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .drive_adapter import access_token, file_metadata


class SubsetDriveObservationError(RuntimeError):
    pass


def subset_sync_enabled() -> bool:
    value = os.getenv("UNIVERSAL_VIDEO_SUBSET_SYNC", "0")
    if value not in ("0", "1"):
        raise SubsetDriveObservationError("VIDEO_SUBSET_MODE_INVALID")
    return value == "1"


@dataclass(frozen=True)
class SubsetDriveConfig:
    approval_id: str
    batch_id: str
    collector_dsn: str
    expected_host: str
    expected_database: str

    @classmethod
    def from_env(cls) -> "SubsetDriveConfig":
        try:
            approval_id = str(UUID(os.environ["BRIDGE_VIDEO_SUBSET_APPROVAL_ID"].strip()))
            batch_id = str(UUID(os.environ["BRIDGE_VIDEO_SUBSET_BATCH_ID"].strip()))
            file_name = Path(os.environ["BRIDGE_VIDEO_SUBSET_COLLECTOR_DATABASE_URL_FILE"])
            if not file_name.is_absolute() or not file_name.is_file():
                raise ValueError("collector DSN path")
            collector_dsn = file_name.read_text(encoding="utf-8").strip()
            if not collector_dsn:
                raise ValueError("collector DSN empty")
            expected_host = os.environ["BRIDGE_VIDEO_SUBSET_EXPECTED_HOST"].strip().lower()
            expected_database = os.environ["BRIDGE_VIDEO_SUBSET_EXPECTED_DATABASE"].strip()
            if not expected_host or not expected_database:
                raise ValueError("target missing")
        except (KeyError, ValueError, OSError) as exc:
            raise SubsetDriveObservationError("VIDEO_SUBSET_BINDING_MISSING") from exc
        return cls(approval_id, batch_id, collector_dsn,
                   expected_host, expected_database)

    def validate_target(self, worker_dsn: str) -> None:
        for dsn in (worker_dsn, self.collector_dsn):
            try:
                parts = conninfo_to_dict(dsn)
            except psycopg.ProgrammingError as exc:
                raise SubsetDriveObservationError("VIDEO_SUBSET_TARGET_MISMATCH") from exc
            host = (parts.get("host") or "").lower()
            if (host != self.expected_host
                    or parts.get("dbname") != self.expected_database
                    or str(parts.get("port") or "5432") != "5432"):
                raise SubsetDriveObservationError("VIDEO_SUBSET_TARGET_MISMATCH")
            if host not in ("localhost", "127.0.0.1", "::1") and (
                parts.get("sslmode") not in ("require", "verify-full")
                or parts.get("channel_binding") != "require"
            ):
                raise SubsetDriveObservationError("VIDEO_SUBSET_TLS_BINDING_REQUIRED")


def restricted_identity(dsn: str, capability: str) -> str:
    """Read-only role check before any claim or observation mutation."""
    capabilities = ("video_subset_approver", "video_subset_collector",
                    "video_subset_admitter", "video_subset_worker_v2")
    if capability not in capabilities:
        raise SubsetDriveObservationError("VIDEO_SUBSET_CAPABILITY_INVALID")
    with psycopg.connect(dsn, connect_timeout=8, row_factory=dict_row,
                         options="-c statement_timeout=10000 -c lock_timeout=2000") as conn:
        row = conn.execute("""
            SELECT session_user::text AS login, r.rolsuper, r.rolcreatedb,
                   r.rolcreaterole, r.rolreplication, r.rolbypassrls,
                   pg_has_role(session_user,'video_subset_owner','MEMBER') AS owner_member,
                   ARRAY(SELECT q.name FROM unnest(%s::text[]) AS q(name)
                         WHERE pg_has_role(session_user,q.name,'MEMBER') ORDER BY q.name) AS capabilities
              FROM pg_roles AS r WHERE r.rolname=session_user
        """, (list(capabilities),)).fetchone()
    if (row is None or not isinstance(row["login"], str)
            or not row["login"] or row["login"] in capabilities
            or any(row[key] for key in ("rolsuper", "rolcreatedb", "rolcreaterole",
                                       "rolreplication", "rolbypassrls", "owner_member"))
            or row["capabilities"] != [capability]):
        raise SubsetDriveObservationError("VIDEO_SUBSET_RESTRICTED_IDENTITY_REQUIRED")
    return row["login"]


class DriveSubsetObservationProvider:
    def __init__(self, config: SubsetDriveConfig, worker_key: str, *,
                 token_source: Callable[[], str] = access_token,
                 metadata_fetcher: Callable[[str, str], Mapping[str, Any]] = file_metadata):
        if not worker_key or not callable(token_source) or not callable(metadata_fetcher):
            raise SubsetDriveObservationError("VIDEO_SUBSET_BINDING_MISSING")
        self.config = config
        self.worker_key = worker_key
        self.token_source = token_source
        self.metadata_fetcher = metadata_fetcher

    def __call__(self, claim: Mapping[str, Any], worker_key: str, *,
                 boundary: str) -> str:
        if (boundary not in ("before_work", "before_finish")
                or worker_key != self.worker_key
                or str(claim.get("batch_id")) != self.config.batch_id
                or not claim.get("source_file_id")
                or not claim.get("job_id") or not claim.get("lease_token")
                or type(claim.get("attempt_count")) is not int):
            raise SubsetDriveObservationError("VIDEO_SUBSET_CLAIM_MISMATCH")
        source_id = str(claim["source_file_id"])
        raw = self.metadata_fetcher(source_id, self.token_source())
        if not isinstance(raw, Mapping) or raw.get("id") != source_id:
            raise SubsetDriveObservationError("VIDEO_SUBSET_DRIVE_READBACK_INVALID")
        observation_id = uuid4()
        # Use the database wall clock *after* the provider readback, within the
        # mutating statement. This avoids host clock skew and stale timestamps.
        with psycopg.connect(self.config.collector_dsn, autocommit=True,
                             row_factory=dict_row, connect_timeout=8,
                             options="-c statement_timeout=10000 -c lock_timeout=2000") as conn:
            row = conn.execute("""
                SELECT video_queue.observe_subset(
                    %s,%s,'ATTEMPT',%s,clock_timestamp(),NULL,%s,%s,%s,%s
                ) AS observation_id
            """, (observation_id, self.config.approval_id, Jsonb([dict(raw)]),
                  claim["job_id"], claim["lease_token"], claim["attempt_count"],
                  worker_key)).fetchone()
        if row is None or str(row["observation_id"]) != str(observation_id):
            raise SubsetDriveObservationError("VIDEO_SUBSET_OBSERVATION_RECEIPT_MISMATCH")
        return str(observation_id)
