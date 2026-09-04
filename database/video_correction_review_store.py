"""Read-only resolver for authenticated video correction review receipts."""
from __future__ import annotations

import re
from typing import Any, Mapping

import psycopg

from database.runtime_worker_preflight import normalize_dsn


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DatabaseCorrectionReceiptResolver:
    """Resolve only receipts attested into the append-only database store."""

    def __init__(self, raw_dsn: str) -> None:
        self._dsn = normalize_dsn(raw_dsn)
        if not self._dsn:
            raise ValueError("trusted correction review database is not configured")

    def __call__(self, receipt_sha256: str) -> Mapping[str, Any] | None:
        value = str(receipt_sha256 or "").strip().lower()
        if not _SHA256.fullmatch(value):
            return None
        with psycopg.connect(
            self._dsn,
            connect_timeout=10,
            application_name="bridge-video-correction-review-reader",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT to_regclass('bidding.video_correction_review_receipt')"
                )
                if cursor.fetchone()[0] is None:
                    return None
                cursor.execute(
                    """
                    SELECT receipt.receipt_payload
                      FROM bidding.video_correction_review_receipt receipt
                      JOIN bidding.video_canon_verifier_registry registry
                        ON registry.database_role=receipt.recorded_by_role
                       AND registry.status='active'
                       AND 'CORRECTION_REVIEW'=ANY(registry.allowed_check_ids)
                      JOIN pg_catalog.pg_roles attestor
                        ON attestor.rolname=receipt.recorded_by_principal
                       AND attestor.rolcanlogin
                      JOIN pg_catalog.pg_roles capability
                        ON capability.rolname=registry.database_role
                       AND pg_has_role(attestor.oid,capability.oid,'MEMBER')
                     WHERE receipt.receipt_sha256=%s
                    """,
                    (value,),
                )
                row = cursor.fetchone()
        if not row or not isinstance(row[0], Mapping):
            return None
        return dict(row[0])


__all__ = ["DatabaseCorrectionReceiptResolver"]
