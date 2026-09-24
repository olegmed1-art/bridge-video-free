"""Read-only SQL catalog boundary. Rows are metadata, never evaluated answers."""
from __future__ import annotations

from uuid import UUID

from psycopg.rows import dict_row


def read_school_catalog(connection, school_id: UUID, scope_key: str) -> list[dict]:
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "SELECT * FROM bidding.get_school_runtime_rule_catalog(%s,%s)",
            (school_id, scope_key),
        )
        return cursor.fetchall()


def read_research_catalog(connection, school_id: UUID, scope_key: str) -> list[dict]:
    """Keep SQL authority_lane tags; research rows cannot become School answers."""
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "SELECT * FROM bidding.get_research_rule_catalog(%s,%s,true)",
            (school_id, scope_key),
        )
        return cursor.fetchall()
