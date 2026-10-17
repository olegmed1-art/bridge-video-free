"""Only the existing loopback PostgreSQL fixture; never a live fallback."""
from .resident_preflight import Refused, idle, capabilities, catalog_path


def inspect_disposable(conn):
    idle(conn)
    if (conn.info.host not in ("127.0.0.1", "localhost") or conn.info.port != 55432
            or conn.info.hostaddr not in ("127.0.0.1", "::1")):
        raise Refused("disposable_loopback_required")
    with conn.transaction(force_rollback=True):
        conn.execute("SET TRANSACTION READ ONLY")
        catalog_path(conn)
        if conn.execute("SELECT pg_catalog.current_database()").fetchone() != ("tournament_rehearsal",):
            raise Refused("disposable_database_required")
        checks = capabilities(conn)
    return checks
