#!/usr/bin/env python3
"""Record non-sensitive recovery evidence in production Neon."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

import psycopg

EXPECTED_HOST_SUFFIX = ".neon.tech"
EXPECTED_DATABASE = "neondb"


def fail(message: str) -> None:
    print(f"RECOVERY_REGISTRY: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def normalize_dsn(raw: str) -> str:
    value = raw.strip()
    if not value:
        fail("NEON_DATABASE_URL is not configured")
    if not value.startswith(("postgresql://", "postgres://")):
        fail("NEON_DATABASE_URL must be a complete PostgreSQL URI")

    parsed = urlsplit(value)
    if not parsed.username or not parsed.password:
        fail("NEON_DATABASE_URL must include user and password")
    if not (parsed.hostname or "").endswith(EXPECTED_HOST_SUFFIX):
        fail("NEON_DATABASE_URL must target a Neon host")
    if parsed.path != f"/{EXPECTED_DATABASE}":
        fail("NEON_DATABASE_URL uses the wrong database")
    return value


def require_text(record: dict, key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        fail(f"record.{key} must be a non-empty string")
    return value.strip()


def require_dict(record: dict, key: str) -> dict:
    value = record.get(key)
    if not isinstance(value, dict):
        fail(f"record.{key} must be an object")
    return value


def load_record(path: Path) -> dict:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        fail(f"cannot read record: {exc}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON record: {exc}")
    if not isinstance(record, dict):
        fail("record root must be an object")
    return record


def validate_record(record: dict) -> None:
    if require_text(record, "schema") != "bridge-school-recovery-registry-record-v1":
        fail("unsupported recovery registry record schema")
    if require_text(record, "checkpoint_type") not in {"branch", "snapshot", "export", "other"}:
        fail("checkpoint_type is not allowed")
    if require_text(record, "verification_type") not in {"read", "branch_compare", "restore_test", "checksum", "other"}:
        fail("verification_type is not allowed")
    if require_text(record, "result") not in {"success", "failure", "partial"}:
        fail("result is not allowed")
    require_text(record, "provider")
    require_text(record, "external_ref")
    require_dict(record, "source_fingerprint")
    require_dict(record, "observed_fingerprint")
    require_dict(record, "details")
    restore_target = record.get("restore_target_ref")
    if restore_target is not None and (not isinstance(restore_target, str) or not restore_target.strip()):
        fail("restore_target_ref must be null or a non-empty string")


def main() -> None:
    record_path = Path(os.environ.get("RECOVERY_RECORD_PATH", "")).resolve()
    if not record_path.is_file():
        fail("RECOVERY_RECORD_PATH must point to a checked-in JSON record")
    if not re.match(r"^[A-Za-z0-9_./-]+$", str(record_path)):
        fail("RECOVERY_RECORD_PATH contains unsupported characters")

    record = load_record(record_path)
    validate_record(record)
    dsn = normalize_dsn(os.environ.get("NEON_DATABASE_URL", ""))

    with psycopg.connect(dsn, connect_timeout=10, application_name="bridge-school-recovery-registry") as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT school_id FROM public.school WHERE stable_name = %s",
                ("Школа спортивного бриджа",),
            )
            rows = cur.fetchall()
            if len(rows) != 1:
                fail("expected exactly one bridge school registry row")
            school_id = rows[0][0]

            cur.execute(
                """
                INSERT INTO public.recovery_checkpoint(
                    school_id, checkpoint_type, provider, external_ref,
                    source_fingerprint, retention_until, notes
                ) VALUES (
                    %s, %s, %s, %s, %s::jsonb, %s, %s
                )
                ON CONFLICT (school_id, provider, external_ref)
                DO UPDATE SET
                    source_fingerprint = EXCLUDED.source_fingerprint,
                    retention_until = EXCLUDED.retention_until,
                    notes = EXCLUDED.notes
                RETURNING recovery_checkpoint_id
                """,
                (
                    school_id,
                    require_text(record, "checkpoint_type"),
                    require_text(record, "provider"),
                    require_text(record, "external_ref"),
                    json.dumps(require_dict(record, "source_fingerprint"), sort_keys=True),
                    record.get("retention_until"),
                    record.get("notes"),
                ),
            )
            checkpoint_id = cur.fetchone()[0]

            cur.execute(
                """
                INSERT INTO public.recovery_verification(
                    recovery_checkpoint_id, verification_type, result,
                    observed_fingerprint, restore_target_ref, details
                ) VALUES (%s, %s, %s, %s::jsonb, %s, %s::jsonb)
                RETURNING recovery_verification_id
                """,
                (
                    checkpoint_id,
                    require_text(record, "verification_type"),
                    require_text(record, "result"),
                    json.dumps(require_dict(record, "observed_fingerprint"), sort_keys=True),
                    record.get("restore_target_ref"),
                    json.dumps(require_dict(record, "details"), sort_keys=True),
                ),
            )
            verification_id = cur.fetchone()[0]

            cur.execute("SELECT count(*) FROM public.recovery_checkpoint")
            checkpoint_count = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM public.recovery_verification")
            verification_count = cur.fetchone()[0]

    print(
        "RECOVERY_REGISTRY: PASS "
        f"checkpoint_id={checkpoint_id} verification_id={verification_id} "
        f"checkpoint_count={checkpoint_count} verification_count={verification_count}"
    )


if __name__ == "__main__":
    main()
