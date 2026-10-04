"""Independent PostgreSQL engine rehearsal; opt-in disposable local CI DB only."""
from contextlib import contextmanager
from copy import deepcopy
import os
from pathlib import Path
from uuid import uuid4

import pytest
import psycopg
from psycopg.rows import dict_row
from fastapi.testclient import TestClient

from bridge_contracts.book_material import digest
from bridge_contracts.book_world import build_world_publication
from database.book_world_persistence import persist_book_world, publish_book_world, retire_book_world
import bridge_school_api.knowledge as knowledge
from bridge_school_api.main import app, EXPECTED_SCHOOL
from test_book_world import reviewed_fixture

pytestmark = pytest.mark.skipif(os.environ.get("BOOK_WORLD_PG_TEST") != "1", reason="disposable PostgreSQL test not enabled")
DSN = "postgresql://postgres@127.0.0.1:5432/book_world_test"


def test_postgres_persistence_protected_teacher_replay_failure_and_rollback(monkeypatch):
    bundle, receipt = reviewed_fixture()
    school_id, source_id = str(uuid4()), str(uuid4())
    with psycopg.connect(DSN, autocommit=True) as setup:
        setup.execute(Path("tests/fixtures/book_world_postgres.sql").read_text())
        setup.execute("INSERT INTO public.school VALUES (%s,%s) ON CONFLICT (stable_name) DO NOTHING", (school_id, EXPECTED_SCHOOL))
        school_id = str(setup.execute("SELECT school_id FROM public.school WHERE stable_name=%s", (EXPECTED_SCHOOL,)).fetchone()[0])
        setup.execute("INSERT INTO public.source (source_id,school_id,canonical_locator,title) VALUES (%s,%s,%s,%s)",
                      (source_id, school_id, bundle["source"]["locator"], bundle["source"]["title"]))
    bundle["run"]["run_id"] = str(uuid4())
    # Distinct fixture identity allows repeated CI runs in the disposable database.
    bundle["source"]["edition_id"] = str(uuid4())
    from bridge_contracts.book_material import identity
    obj = bundle["objects"][0]
    obj["object_id"] = "BOOK-" + identity(bundle["source"], obj)
    receipt["claims"][0]["object_id"] = obj["object_id"]
    with psycopg.connect(DSN) as conn:
        result = persist_book_world(conn, bundle, receipt, school_id=school_id, source_id=source_id)
        assert result["new_versions"] == 1
        assert persist_book_world(conn, bundle, receipt, school_id=school_id, source_id=source_id)["new_versions"] == 0
        new_run = deepcopy(bundle)
        new_run["run"]["run_id"] = str(uuid4())
        new_run["run"]["model_version"] = "new-synthetic-model"
        assert persist_book_world(conn, new_run, receipt, school_id=school_id, source_id=source_id)["version_ids"] == result["version_ids"]
        bad = deepcopy(bundle)
        bad["run"]["model_version"] = "changed-under-same-run"
        with pytest.raises(ValueError, match="RUN_CONFLICT"):
            persist_book_world(conn, bad, receipt, school_id=school_id, source_id=source_id)

        @contextmanager
        def connect_test():
            with psycopg.connect(DSN, row_factory=dict_row) as read_connection:
                yield read_connection
        monkeypatch.setattr(knowledge, "connect", connect_test)
        monkeypatch.setenv("BRIDGE_API_TOKEN", "synthetic-postgres-test-token")
        client = TestClient(app)
        stable_key = build_world_publication(bundle, receipt)["records"][0]["stable_key"]
        path = "/v1/knowledge/teacher/book"
        assert client.get(path, params={"stable_key": stable_key}).status_code == 401
        assert client.get(path, params={"stable_key": stable_key}, headers={"Authorization": "Bearer synthetic-postgres-test-token"}).status_code == 404
        delivery = {"schema": "book-world-delivery-v1", "publication_hash": result["publication_hash"],
                    "catalog_receipt_ref": "synthetic-delivery-not-a-real-catalog"}
        with pytest.raises(ValueError, match="DELIVERY_NOT_COMPLETE"):
            publish_book_world(conn, school_id=school_id, changeset_id=result["changeset_id"], delivery_receipt=delivery)
        # Simulated external delivery in the disposable fixture, not a production receipt.
        with psycopg.connect(DSN, autocommit=True) as delivery_conn:
            delivery_conn.execute("UPDATE public.outbox_message SET status='published' WHERE changeset_id=%s", (result["changeset_id"],))
        publish_book_world(conn, school_id=school_id, changeset_id=result["changeset_id"], delivery_receipt=delivery)
        publish_book_world(conn, school_id=school_id, changeset_id=result["changeset_id"], delivery_receipt=delivery)
        response = client.get(path, params={"stable_key": stable_key}, headers={"Authorization": "Bearer synthetic-postgres-test-token"})
        assert response.status_code == 200, response.text
        assert response.json()["knowledge_version_id"] == result["version_ids"][0]
        assert response.json()["citations"][0]["segment_sha256"] == obj["anchor"]["segment_sha256"]

        interrupted = deepcopy(bundle)
        interrupted["run"]["run_id"] = str(uuid4())
        def fail():
            with psycopg.connect(DSN) as observer:
                # Pending writes and outbox rows must remain invisible to another connection.
                assert observer.execute("SELECT count(*) FROM public.domain_event WHERE idempotency_key=%s", (interrupted["run"]["run_id"],)).fetchone()[0] == 0
            raise RuntimeError("synthetic interruption")
        with pytest.raises(RuntimeError, match="synthetic interruption"):
            persist_book_world(conn, interrupted, receipt, school_id=school_id, source_id=source_id, before_commit=fail)
        assert persist_book_world(conn, interrupted, receipt, school_id=school_id, source_id=source_id)["new_versions"] == 0
        assert retire_book_world(conn, school_id=school_id, version_ids=result["version_ids"]) == 1
        assert client.get(path, params={"stable_key": stable_key}, headers={"Authorization": "Bearer synthetic-postgres-test-token"}).status_code == 404
        with pytest.raises(ValueError, match="RETIRED"):
            persist_book_world(conn, bundle, receipt, school_id=school_id, source_id=source_id)
