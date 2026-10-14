"""Select only Autopilot migration receipts from a held Neon snapshot.

The source table is shared with school migrations. A whole-table dump would
move unrelated records to Oracle. This module deliberately has no write path.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

KEY = re.compile(r"^03\d\d[a-z]?_autopilot_[a-z0-9_]+$")
MIGRATIONS = Path(__file__).resolve().parents[1] / "database" / "migrations"


def expected_keys(directory: Path = MIGRATIONS) -> set[str]:
    keys = {path.stem for path in directory.glob("03*_autopilot_*.sql")}
    if len(keys) < 10 or any(not KEY.fullmatch(key) for key in keys):
        raise ValueError("AUTOPILOT_MIGRATION_INVENTORY_INVALID")
    return keys


def select_rows(cursor, *, directory: Path = MIGRATIONS) -> list[tuple[str, str | None, str]]:
    """Call inside the SAME read-only repeatable-read transaction as pg_dump.

    A source branch may legitimately have an earlier migration generation.
    Every selected key must have a reviewed SQL file. Historic NULL checksums
    are preserved exactly, never forged from migration source files.
    The caller separately checks which migrations are required by its target.
    """
    reviewed = expected_keys(directory)
    cursor.execute("SELECT migration_key, checksum, extract(epoch from applied_at)::text "
                   "FROM public.schema_migration ORDER BY migration_key")
    rows = cursor.fetchall()
    selected = []
    seen = set()
    for key, checksum, applied_at in rows:
        if not isinstance(key, str):
            raise ValueError("AUTOPILOT_LEDGER_KEY_INVALID")
        if 'autopilot' in key.lower() and not KEY.fullmatch(key):
            raise ValueError("AUTOPILOT_LEDGER_UNREVIEWED_KEY_SHAPE")
        if not KEY.fullmatch(key):
            continue
        if (key not in reviewed or key in seen or not applied_at or
                (checksum is not None and not re.fullmatch(r"[0-9a-f]{64}", checksum))):
            raise ValueError("AUTOPILOT_LEDGER_KEY_OR_CHECKSUM_DRIFT")
        source = directory / f"{key}.sql"
        if source.is_symlink() or not source.is_file():
            raise ValueError("AUTOPILOT_MIGRATION_SOURCE_INVALID")
        if checksum is not None and hashlib.sha256(source.read_bytes()).hexdigest() != checksum:
            raise ValueError("AUTOPILOT_LEDGER_SOURCE_MISMATCH")
        seen.add(key)
        selected.append((key, checksum, applied_at))
    if not selected or "0300_autopilot_oracle_shadow" not in seen:
        raise ValueError("AUTOPILOT_LEDGER_EMPTY_OR_MISSING_BASE")
    return selected


def manifest(rows: list[tuple[str, str | None, str]]) -> dict:
    data = json.dumps(rows, separators=(",", ":"), ensure_ascii=True).encode()
    return {"format": "AUTOPILOT_SELECTIVE_LEDGER_V1", "rows": len(rows),
            "keys": [row[0] for row in rows], "sha256": hashlib.sha256(data).hexdigest(),
            "historical_null_checksum_keys": [row[0] for row in rows if row[1] is None],
            "migration_checksum_complete": all(row[1] is not None for row in rows),
            "includes_other_school_migrations": False}


def assert_restored(cursor, source_rows: list[tuple[str, str | None, str]]) -> None:
    """Check the isolated restore contains precisely the selected source rows."""
    cursor.execute("SELECT migration_key, checksum, extract(epoch from applied_at)::text "
                   "FROM public.schema_migration ORDER BY migration_key")
    if [tuple(row) for row in cursor.fetchall()] != source_rows:
        raise ValueError("AUTOPILOT_LEDGER_RESTORE_MISMATCH")
