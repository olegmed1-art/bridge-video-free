from __future__ import annotations
from typing import TYPE_CHECKING, Any

from .research_pipeline import ResearchKind

if TYPE_CHECKING:
    import psycopg


def submit_research_job(conn: psycopg.Connection, request: dict[str, Any]) -> dict[str, Any]:
    from .research_runtime import enqueue

    kind=ResearchKind(str(request.get('kind') or '').upper())
    payload=request.get('payload')
    if not isinstance(payload,dict): raise ValueError('payload must be an object')
    source=str(request.get('source') or 'CHAT')[:128]
    priority=int(request.get('priority',20))
    if priority not in {0,10,20,30}: raise ValueError('unsupported research priority')
    return enqueue(conn,kind=kind,payload=payload,source=source,priority=priority)

def refresh_research_job(conn: psycopg.Connection, research_id: str) -> dict[str, Any]:
    from .research_runtime import finalize

    return finalize(conn,research_id)
