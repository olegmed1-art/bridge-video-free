"""Actual disposable PostgreSQL producer→publisher→pinned Teacher integration.

One canonical synthetic fixture; real SQL/RPC metadata, synthetic catalog and
trusted authorization only. This is not a production read lane or admission.
"""
from contextlib import contextmanager
from copy import deepcopy
import os
from pathlib import Path
import re
import pytest
import psycopg
from psycopg.rows import dict_row
from fastapi.testclient import TestClient
from bridge_contracts.book_material import digest
from bridge_contracts.book_world import render_teacher_book, world_uuid
from database.book_world_persistence import persist_book_world, publish_book_world, retire_book_world
from database.book_world_event_readback import read_book_world_event
from database import outbox_publisher
from evolutionary_course import book_teacher_consumer as legacy
from evolutionary_course import book_teacher_delivery_consumer_v2 as teacher
import bridge_school_api.knowledge as knowledge
from bridge_school_api.main import app, EXPECTED_SCHOOL
from test_book_canonical_v2 import canonical

pytestmark = pytest.mark.skipif(os.environ.get("BOOK_WORLD_PG_TEST") != "1",
                               reason="disposable PostgreSQL test not enabled")
DSN = "postgresql://postgres@127.0.0.1:5432/book_world_test"


def test_one_canonical_fixture_actual_producer_publisher_teacher_and_retirement(monkeypatch):
    f = canonical()
    school, source = f["school_id"], f["source_id"]
    change = f["import_receipt"]["changeset_id"]
    auth = f["trusted_authorization"]
    with psycopg.connect(DSN, autocommit=True) as setup:
        setup.execute(Path("tests/fixtures/book_world_postgres.sql").read_text())
        for filename, function in [("0003_event_publication_order.sql", "allocate_event_position"),
                                   ("0004_integrity_guards.sql", "publish_outbox_event")]:
            definition = re.search(r"CREATE OR REPLACE FUNCTION " + function + r"\(.*?\n\$\$;",
                                   Path("database/migrations", filename).read_text(), re.S)
            assert definition is not None
            setup.execute(definition.group(0))
        setup.execute("INSERT INTO public.school VALUES(%s,%s)", (school, EXPECTED_SCHOOL))
        # Disposable existing-RPC privileges; no UPDATE on event payload/changeset.
        setup.execute("CREATE ROLE synthetic_book_publisher")
        setup.execute("GRANT USAGE ON SCHEMA public TO synthetic_book_publisher")
        setup.execute("GRANT SELECT ON public.domain_event,public.changeset,public.outbox_message TO synthetic_book_publisher")
        setup.execute("GRANT UPDATE ON public.outbox_message TO synthetic_book_publisher")
        setup.execute("GRANT SELECT,INSERT,UPDATE ON public.event_position_allocator TO synthetic_book_publisher")
        setup.execute("GRANT UPDATE(event_position) ON public.domain_event TO synthetic_book_publisher")
        assert not setup.execute("SELECT has_column_privilege('synthetic_book_publisher','public.domain_event','payload','UPDATE')").fetchone()[0]
        assert setup.execute("SELECT has_column_privilege('synthetic_book_publisher','public.domain_event','event_position','UPDATE')").fetchone()[0]
        assert not setup.execute("SELECT has_table_privilege('synthetic_book_publisher','public.domain_event','UPDATE')").fetchone()[0]
        assert not setup.execute("SELECT has_table_privilege('synthetic_book_publisher','public.changeset','UPDATE')").fetchone()[0]
        setup.execute("INSERT INTO public.source(source_id,school_id,canonical_locator) VALUES(%s,%s,%s)",
                      (source, school, f["bundle"]["source"]["locator"]))

    def normalize(raw):
        assert raw == DSN
        return DSN
    monkeypatch.setattr(outbox_publisher, "normalize_dsn", normalize)
    @contextmanager
    def connect_read():
        with psycopg.connect(DSN, row_factory=dict_row) as conn:
            yield conn
    monkeypatch.setattr(knowledge, "connect", connect_read)
    monkeypatch.setenv("BRIDGE_API_TOKEN", "SYNTHETIC-CANONICAL-API-TOKEN")
    client = TestClient(app)
    headers = {"Authorization": "Bearer SYNTHETIC-CANONICAL-API-TOKEN"}
    mapping = legacy.build_card_map(f["packet"])
    queries, delivery_queries, authorization_queries = [], [], []
    def query_world(**kwargs):
        queries.append(deepcopy(kwargs))
        return knowledge.query_knowledge(**kwargs)
    def metadata(*, event_id=None, changeset_id=None):
        with psycopg.connect(DSN) as conn:
            with conn.cursor() as cur:
                return read_book_world_event(cur, school_id=school, event_id=event_id,
                                             changeset_id=changeset_id)
    def authorize(query):
        assert set(query) == {"recorded_event"}
        actual = query["recorded_event"]
        assert actual["event_type"] == "BookWorldRecorded" and actual["school_id"] == school
        assert actual["payload"] == f["recorded_payload"]
        assert actual["payload_hash"] == digest(actual["payload"])
        authorization_queries.append(deepcopy(query))
        return deepcopy(auth)
    def publish_outbox(change_id, provider=None):
        real_connect = psycopg.connect
        def limited_connect(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            conn.execute("SET ROLE synthetic_book_publisher")
            conn.commit()
            return conn
        from unittest.mock import patch
        with patch.object(outbox_publisher.psycopg, "connect", limited_connect):
            return outbox_publisher.publish_changeset_outbox(DSN,change_id,authorization_provider=provider)

    published_receipt = recorded_delivery = published_delivery = None
    def read_delivery(query):
        assert query == {"recorded_event_id": f["published_payload"]["recorded_event_id"],
            "recorded_changeset_id": change, "publication_hash": f["recorded_payload"]["publication_hash"],
            "source_id": source, "expected_school_id": school}
        delivery_queries.append(deepcopy(query))
        return {"schema": teacher.READBACK_SCHEMA,
                "recorded_event": metadata(event_id=query["recorded_event_id"]),
                "published_event": metadata(changeset_id=f["publication_receipt"]["changeset_id"]),
                "recorded_outbox_receipt": deepcopy(recorded_delivery),
                "published_outbox_receipt": deepcopy(published_delivery),
                "publication_receipt": deepcopy(published_receipt)}
    def consume(mapping, label, reader=read_delivery):
        context = {"topic": legacy.TOPICS[label], "stage": "INITIAL_PLAN", "dummy_exposed": True,
                   "opening_lead_made": True, "asks_for": "PRINCIPLE_EXPLANATION"}
        return teacher.consume_card_v2(mapping,label,context,query_knowledge=query_world,
            render_teacher_book=render_teacher_book,read_delivery=reader,test_only=True)

    assert consume(mapping,"E01")["reason"] == "IMPORT_NOT_COMMITTED"
    assert not queries and not delivery_queries
    with psycopg.connect(DSN) as writer:
        actual = persist_book_world(writer,f["bundle"],f["review_receipt"],school_id=school,
                                    source_id=source,delivery_authorization=auth)
    assert actual == f["import_receipt"]
    recorded = metadata(changeset_id=change)
    assert recorded["payload"] == f["recorded_payload"] and recorded["event_position"] is None
    bound = teacher.bind_import_receipt_v2(mapping,actual,recorded,expected_school_id=school,test_only=True)
    assert consume(bound,"E01")["status"] == "GAP"
    with pytest.raises(RuntimeError,match="OUTBOX_BOOK_EVENT_INVALID"):
        publish_outbox(change)  # No provider ≠ permission.
    recorded_delivery = publish_outbox(change,authorize)
    assert publish_outbox(change,authorize) == recorded_delivery
    assert consume(bound,"E01")["status"] == "GAP" and not queries
    receipt = {k: f["published_payload"][k] for k in teacher.PUBLISHED_BASE}
    with psycopg.connect(DSN) as writer:
        with pytest.raises(ValueError,match="AUTHORIZATION"):
            publish_book_world(writer,school_id=school,changeset_id=change,delivery_receipt=receipt)
        published_receipt = publish_book_world(writer,school_id=school,changeset_id=change,
            delivery_receipt=receipt,delivery_authorization=auth)
    assert published_receipt == f["publication_receipt"]
    audit = metadata(changeset_id=published_receipt["changeset_id"])
    assert audit["payload"] == f["published_payload"]
    assert audit["payload"]["recorded_payload_hash"] == recorded["payload_hash"]
    assert consume(bound,"E01")["status"] == "GAP" and not queries
    published_delivery = publish_outbox(published_receipt["changeset_id"],authorize)
    assert published_delivery["event_positions"][0] > recorded_delivery["event_positions"][0]
    assert publish_outbox(published_receipt["changeset_id"],authorize) == published_delivery

    def counts():
        with psycopg.connect(DSN) as conn:
            return [conn.execute("SELECT count(*) FROM public."+table).fetchone()[0]
                    for table in ("knowledge_item","knowledge_version","changeset","domain_event",
                                  "outbox_message","canon_activation")]
    before = counts()
    for card in bound["cards"]:
        label = card["card_label"]
        d0 = len(delivery_queries)
        result = consume(bound,label)
        assert result["status"] == "EXPLANATION_READY"
        assert len(delivery_queries) == d0+2
        assert result["production_ready"] is False
        assert result["book_response"]["knowledge_version_id"] == card["knowledge_version_id"]
        assert result["book_response"]["citations"] == [card["expected_content"]["citation"]]
        assert all(x in result["explanation_with_page"] for key in ("conditions","exceptions")
                   for x in card["expected_content"][key])
        response = client.get("/v1/knowledge/teacher/book",
            params={"stable_key":card["stable_key"]},headers=headers)
        assert response.status_code == 200 and response.json() == result["book_response"]
    assert counts() == before
    assert all(c["knowledge_item_id"] is None for c in bound["cards"])
    for field in ("recorded_event_id","recorded_changeset_id","recorded_payload_hash"):
        def wrong_parent(query):
            snap=read_delivery(query)
            snap["published_event"]["payload"][field] = ("f"*64 if field.endswith("hash") else source)
            snap["published_event"]["payload_hash"] = digest(snap["published_event"]["payload"])
            return snap
        q0=len(queries)
        assert consume(bound,"E01",wrong_parent)["reason"] == "PUBLISHED_RECORDED_CAUSAL_BINDING"
        assert len(queries) == q0
    with psycopg.connect(DSN) as writer:
        replay = persist_book_world(writer,f["bundle"],f["review_receipt"],school_id=school,
                                   source_id=source,delivery_authorization=auth)
        assert replay["new_versions"] == 0 and replay["version_ids"] == actual["version_ids"]
        with pytest.raises(ValueError,match="BOOK_WORLD_RUN_CONFLICT"):
            persist_book_world(writer,f["bundle"],f["review_receipt"],school_id=school,source_id=source)
        assert retire_book_world(writer,school_id=school,version_ids=actual["version_ids"]) == 3
    assert consume(bound,"E01")["reason"] == "BOOK_WORLD_GAP"
    assert counts()[-1] == 0 and authorization_queries
