"""Keep unapproved producer events pending at the publication boundary."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


def publish_if_authorized(
    raw_dsn: str,
    persisted: Mapping[str, Any],
    publisher: Callable[[str, str], dict[str, object]],
) -> dict[str, object]:
    """Invoke the guarded publisher only for an explicit allowed result.

    This is a producer-side decision. The publisher must still inspect the
    immutable domain-event payload and reject a blocked or unavailable event.
    A returned pending state never means that source COMMIT was authorized.
    """
    changeset_id = str(persisted["changeset_id"])
    status = persisted.get("publication_authorization_status")
    if status != "allowed":
        return {
            "changeset_id": changeset_id,
            "published_count": 0,
            "event_positions": [],
            "publication_state": "BLOCKED_PENDING" if status == "blocked" else "UNKNOWN_PENDING",
        }

    result = publisher(raw_dsn, changeset_id)
    count = result["published_count"]
    positions = result["event_positions"]
    if not isinstance(count, int) or isinstance(count, bool) or count < 0 or not isinstance(positions, list) or len(positions) != count:
        raise RuntimeError("OUTBOX_PUBLICATION_RESULT_INVALID")
    return {**result, "publication_state": "PUBLISHED" if count else "NO_EVENTS"}


__all__ = ["publish_if_authorized"]
