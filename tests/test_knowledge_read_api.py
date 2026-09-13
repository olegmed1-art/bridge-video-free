from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi import HTTPException

import bridge_school_api.knowledge as knowledge
from bridge_school_api.main import app, require_api_token


class FakeCursor:
    def __init__(
        self,
        *,
        rows: list[dict] | None = None,
        conflict: dict | None = None,
        sync_state: dict | None = None,
    ):
        self.rows = rows or []
        self.conflict = conflict
        self.sync_state = sync_state
        self.executions: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql: str, params: tuple[object, ...]) -> None:
        self.executions.append((sql, params))

    def fetchone(self):
        if self.executions and "knowledge_relation relation" in self.executions[-1][0]:
            return self.conflict
        if self.executions and "FROM ai.sync_state" in self.executions[-1][0]:
            return self.sync_state
        return None

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, cursor: FakeCursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def cursor(self) -> FakeCursor:
        return self._cursor


def install_fake_connect(monkeypatch, cursor: FakeCursor) -> None:
    @contextmanager
    def fake_connect():
        yield FakeConnection(cursor)

    monkeypatch.setattr(knowledge, "connect", fake_connect)


def query(monkeypatch, lane: knowledge.AuthorityLane, cursor: FakeCursor) -> dict:
    install_fake_connect(monkeypatch, cursor)
    return knowledge.query_knowledge(
        lane=lane,
        system_profile="SCHOOL_L1_DB_V1" if lane is not knowledge.AuthorityLane.WORLD_EXTERNAL else "SYSTEM_NEUTRAL",
        stable_key=None,
        scope_key="default",
        limit=100,
        offset=0,
    )


def test_knowledge_routes_are_mounted_with_api_token_dependency() -> None:
    mounted = [
        route
        for route in app.routes
        if getattr(getattr(route, "include_context", None), "included_router", None)
        is knowledge.router
    ]
    assert len(mounted) == 1

    include_context = mounted[0].include_context
    assert any(
        dependency.dependency is require_api_token
        for dependency in include_context.dependencies
    )
    assert {
        route.path for route in mounted[0].original_router.routes
    } == {"/v1/knowledge/query", "/v1/knowledge/runtime/l1"}


def test_source_lane_reads_only_approved_source_facts(monkeypatch) -> None:
    cursor = FakeCursor(
        rows=[{"stable_key": "FACT-L1-OPEN-1MAJOR"}],
        sync_state={"status": "NEEDS_REVIEW"},
    )
    result = query(monkeypatch, knowledge.AuthorityLane.SOURCE, cursor)

    sql, params = cursor.executions[0]
    assert "FROM ai.knowledge_fact" in sql
    assert "f.review_status = 'APPROVED_SOURCE'" in sql
    assert "profile_rule.system_version = %s" in sql
    assert "public.canon_activation" not in sql
    assert params[1] == "SCHOOL_L1_DB_V1"
    assert params[2] == "SCHOOL_L1_DB_V1"
    assert result["authority_lane"] == "SOURCE"
    assert result["retrieval_status"] == "SOURCE_MATCH"
    assert result["fallback_performed"] is False
    assert result["sync_state"] == {"status": "NEEDS_REVIEW"}


def test_world_lane_cannot_read_school_or_unreviewed_versions(monkeypatch) -> None:
    cursor = FakeCursor(rows=[{"stable_key": "RKA-0001"}])
    result = query(monkeypatch, knowledge.AuthorityLane.WORLD_EXTERNAL, cursor)

    sql, params = cursor.executions[0]
    assert params[1] == "external"
    assert "kv.review_status = 'reviewed'" in sql
    assert "kv.status IN ('candidate', 'active')" in sql
    assert "activation.status = 'active'" not in sql
    assert result["authority_lane"] == "WORLD_EXTERNAL"
    assert result["retrieval_status"] == "WORLD_MATCH"
    assert result["fallback_performed"] is False


def test_candidate_lane_excludes_active_canon(monkeypatch) -> None:
    cursor = FakeCursor()
    result = query(monkeypatch, knowledge.AuthorityLane.SCHOOL_CANON_CANDIDATE, cursor)

    sql, params = cursor.executions[0]
    assert params[1] == "school_canon"
    assert "kv.status = 'candidate'" in sql
    assert "NOT EXISTS" in sql
    assert "activation.status = 'active'" in sql
    assert "analysis_candidate" not in sql
    assert result["retrieval_status"] == "CANDIDATE_GAP"
    assert result["candidate_activation_performed"] is False


def test_active_canon_gap_does_not_fall_back_to_world(monkeypatch) -> None:
    cursor = FakeCursor()
    result = query(monkeypatch, knowledge.AuthorityLane.ACTIVE_SCHOOL_CANON, cursor)

    assert len(cursor.executions) == 3
    snapshot_sql, snapshot_params = cursor.executions[0]
    assert snapshot_sql == (
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    )
    assert snapshot_params == ()
    assert "knowledge_relation relation" in cursor.executions[1][0]
    sql, params = cursor.executions[2]
    assert params[1] == "school_canon"
    assert "JOIN public.canon_activation activation" in sql
    assert "activation.status = 'active'" in sql
    assert result["retrieval_status"] == "CANON_GAP"
    assert result["items"] == []
    assert result["fallback_performed"] is False


def test_active_canon_conflict_stops_before_retrieval(monkeypatch) -> None:
    cursor = FakeCursor(conflict={"left_key": "RULE-A", "right_key": "RULE-B"})
    install_fake_connect(monkeypatch, cursor)

    with pytest.raises(HTTPException) as error:
        knowledge.query_knowledge(
            lane=knowledge.AuthorityLane.ACTIVE_SCHOOL_CANON,
            system_profile="SCHOOL_L1_DB_V1",
            stable_key=None,
            scope_key="default",
            limit=100,
            offset=0,
        )

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "CANON_CONFLICT"
    assert len(cursor.executions) == 2
    assert cursor.executions[0] == (
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        (),
    )
    assert "knowledge_relation relation" in cursor.executions[1][0]


def test_runtime_snapshot_is_explicit_and_not_a_db_activation() -> None:
    result = knowledge.l1_runtime_catalog(
        system_profile="SCHOOL_L1_DB_V1",
        rule_id=None,
        limit=200,
        offset=0,
    )

    assert result["authority_lane"] == "SCHOOL_CANON"
    assert result["lifecycle"] == "RUNTIME_SNAPSHOT"
    assert result["formal_db_activation_asserted"] is False
    assert result["count"] == 111
    assert sum(rule["executable"] for rule in result["rules"]) == 110
    assert result["fallback_performed"] is False


def test_runtime_rejects_other_system_profiles() -> None:
    with pytest.raises(HTTPException) as error:
        knowledge.l1_runtime_catalog(
            system_profile="SCHOOL_TOURNAMENT_CURRENT_V1",
            rule_id=None,
            limit=200,
            offset=0,
        )

    assert error.value.status_code == 404
    assert error.value.detail["code"] == "RUNTIME_PROFILE_GAP"
