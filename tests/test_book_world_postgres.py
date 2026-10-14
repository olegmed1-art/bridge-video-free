"""Independent PostgreSQL engine rehearsal; opt-in disposable local CI DB only."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import os
from pathlib import Path
from queue import Queue
import time
from uuid import UUID, uuid4

import pytest
import psycopg
from psycopg.rows import dict_row
from fastapi.testclient import TestClient

from bridge_contracts.book_material import InvalidBookMaterial, canonical_json, digest, identity
from bridge_contracts.book_world import build_world_publication, publication_version, world_uuid
from database.book_world_persistence import persist_book_world, publish_book_world, retire_book_world
import bridge_school_api.knowledge as knowledge
from bridge_school_api.main import app, EXPECTED_SCHOOL
from test_book_world import reviewed_fixture, reviewed_identity_fixture
from test_book_material_staging import bundle as material_bundle, record

pytestmark = pytest.mark.skipif(os.environ.get("BOOK_WORLD_PG_TEST") != "1", reason="disposable PostgreSQL test not enabled")
DSN = "postgresql://postgres@127.0.0.1:5432/book_world_test"


@pytest.mark.parametrize("malformed", ["conditions_null", "provenance_list", "claims_string"])
def test_publication_admission_and_transaction_are_atomic_for_two_candidates(monkeypatch, malformed):
    bundle, receipt = reviewed_fixture()
    bundle["objects"].append(material_bundle([record(page=1)])["objects"][0])
    bundle["source"]["edition_id"] = str(uuid4())
    bundle["run"]["run_id"] = str(uuid4())
    template = deepcopy(receipt["claims"][0])
    receipt["claims"] = []
    for obj in bundle["objects"]:
        obj["object_id"] = "BOOK-" + identity(bundle["source"], obj)
        receipt["claims"].append({**deepcopy(template), "object_id": obj["object_id"],
                                  "anchor_sha256": digest(obj["anchor"])})
    publication = build_world_publication(bundle, receipt)
    source_id = str(uuid4())
    with psycopg.connect(DSN, autocommit=True) as setup:
        setup.execute(Path("tests/fixtures/book_world_postgres.sql").read_text())
        setup.execute("INSERT INTO public.school(school_id,stable_name) VALUES (%s,%s) ON CONFLICT (stable_name) DO NOTHING",
                      (str(uuid4()), EXPECTED_SCHOOL))
        school_id = str(setup.execute("SELECT school_id FROM public.school WHERE stable_name=%s",
                                     (EXPECTED_SCHOOL,)).fetchone()[0])
        setup.execute("INSERT INTO public.source(source_id,school_id,canonical_locator) VALUES (%s,%s,%s)",
                      (source_id, school_id, bundle["source"]["locator"]))

    @contextmanager
    def connect_test():
        with psycopg.connect(DSN, row_factory=dict_row) as read:
            yield read
    monkeypatch.setattr(knowledge, "connect", connect_test)
    monkeypatch.setenv("BRIDGE_API_TOKEN", "synthetic-postgres-test-token")
    client = TestClient(app)
    headers = {"Authorization": "Bearer synthetic-postgres-test-token"}
    path = "/v1/knowledge/teacher/book"

    with psycopg.connect(DSN) as publisher:
        staged = persist_book_world(publisher, bundle, receipt, school_id=school_id, source_id=source_id)
        versions = sorted(staged["version_ids"])
        assert staged["new_versions"] == len(versions) == 2
        publish_id = world_uuid("publish", school_id, staged["changeset_id"])
        args = dict(school_id=school_id, changeset_id=staged["changeset_id"],
            delivery_receipt={"schema": "book-world-delivery-v1",
                "publication_hash": staged["publication_hash"], "catalog_receipt_ref": "synthetic-delivery"})

        def snapshot():
            # Fresh connection observes committed state, including any accidental partial commit.
            with psycopg.connect(DSN, autocommit=True) as read:
                return {
                    "versions": read.execute("SELECT knowledge_version_id,status,content,provenance "
                        "FROM public.knowledge_version WHERE knowledge_version_id=ANY(%s::uuid[]) "
                        "ORDER BY knowledge_version_id", (versions,)).fetchall(),
                    "links": read.execute("SELECT * FROM public.knowledge_version_source "
                        "WHERE knowledge_version_id=ANY(%s::uuid[]) ORDER BY knowledge_version_id",
                        (versions,)).fetchall(),
                    "changesets": read.execute("SELECT * FROM public.changeset "
                        "WHERE changeset_id=ANY(%s::uuid[]) ORDER BY changeset_id",
                        ([staged["changeset_id"], publish_id],)).fetchall(),
                    "events": read.execute("SELECT * FROM public.domain_event "
                        "WHERE changeset_id=ANY(%s::uuid[]) ORDER BY event_id",
                        ([staged["changeset_id"], publish_id],)).fetchall(),
                    "outbox": read.execute("SELECT * FROM public.outbox_message "
                        "WHERE changeset_id=ANY(%s::uuid[]) ORDER BY outbox_id",
                        ([staged["changeset_id"], publish_id],)).fetchall(),
                    "canon": read.execute("SELECT * FROM public.canon_activation "
                        "WHERE knowledge_version_id=ANY(%s::uuid[])", (versions,)).fetchall(),
                }

        with psycopg.connect(DSN, autocommit=True) as fixture:
            # Simulated delivery applies only to this disposable fixture's recorded event.
            fixture.execute("UPDATE public.outbox_message SET status='published' WHERE changeset_id=%s",
                            (staged["changeset_id"],))
            content, provenance, key = fixture.execute(
                "SELECT kv.content,kv.provenance,ki.stable_key FROM public.knowledge_version kv "
                "JOIN public.knowledge_item ki USING (knowledge_item_id) WHERE knowledge_version_id=%s",
                (versions[1],)).fetchone()  # ORDER BY UUID makes the first candidate valid.
            original_content, original_provenance = deepcopy(content), deepcopy(provenance)
            if malformed == "conditions_null":
                content["conditions"] = None
            elif malformed == "provenance_list":
                provenance = ["synthetic-malformed"]
            else:
                provenance["review_receipt"]["claims"] = "synthetic-malformed"
            if isinstance(provenance, dict):
                provenance["version_hash"] = publication_version(
                    {"stable_key": key, "content": content}, provenance["review_receipt"])
            fixture.execute("UPDATE public.knowledge_version SET content=%s::jsonb,provenance=%s::jsonb "
                            "WHERE knowledge_version_id=%s", (canonical_json(content), canonical_json(provenance), versions[1]))
        damaged = snapshot()
        assert all(row[1] == "staged" for row in damaged["versions"])
        assert len(damaged["changesets"]) == len(damaged["events"]) == len(damaged["outbox"]) == 1
        assert damaged["canon"] == []
        with pytest.raises(InvalidBookMaterial):
            publish_book_world(publisher, **args)
        assert publisher.info.transaction_status == 0
        assert snapshot() == damaged
        for projected in publication["records"]:
            assert client.get(path, params={"stable_key": projected["stable_key"]}, headers=headers).status_code == 404

        with psycopg.connect(DSN, autocommit=True) as fixture:
            fixture.execute("UPDATE public.knowledge_version SET content=%s::jsonb,provenance=%s::jsonb "
                "WHERE knowledge_version_id=%s", (canonical_json(original_content), canonical_json(original_provenance), versions[1]))
        restored = snapshot()

        def fail_after_publication_writes():
            assert publisher.execute("SELECT count(*) FROM public.knowledge_version "
                "WHERE knowledge_version_id=ANY(%s::uuid[]) AND status='active'", (versions,)).fetchone()[0] == 2
            assert publisher.execute("SELECT count(*) FROM public.changeset WHERE changeset_id=%s "
                "AND status='committed'", (publish_id,)).fetchone()[0] == 1
            assert publisher.execute("SELECT count(*) FROM public.domain_event WHERE changeset_id=%s "
                "AND event_type='BookWorldPublished'", (publish_id,)).fetchone()[0] == 1
            assert publisher.execute("SELECT count(*) FROM public.outbox_message WHERE changeset_id=%s",
                (publish_id,)).fetchone()[0] == 1
            raise RuntimeError("synthetic failure after publication writes")

        with pytest.raises(RuntimeError, match="synthetic failure after publication writes"):
            publish_book_world(publisher, **args, before_commit=fail_after_publication_writes)
        assert publisher.info.transaction_status == 0
        assert snapshot() == restored
        result = publish_book_world(publisher, **args)
        assert result["publication"] == "WORLD_VERIFIED"
        assert sorted(result["version_ids"]) == versions
        committed = snapshot()
        assert all(row[1] == "active" for row in committed["versions"])
        assert len(committed["changesets"]) == len(committed["events"]) == len(committed["outbox"]) == 2
        assert committed["links"] == restored["links"] and committed["canon"] == []
        assert [row[2:] for row in committed["versions"]] == [row[2:] for row in restored["versions"]]
        publish_book_world(publisher, **args)
        assert snapshot() == committed
        for projected in publication["records"]:
            response = client.get(path, params={"stable_key": projected["stable_key"]}, headers=headers)
            assert response.status_code == 200, response.text
            answer = response.json()
            expected_id = world_uuid("version", school_id,
                projected["stable_key"] + ":" + publication_version(projected, receipt))
            assert answer["knowledge_version_id"] == expected_id
            assert answer["citations"] == [projected["content"]["citation"]]
            assert answer["fallback_performed"] is answer["canon_activation_performed"] is False


def test_real_postgres_runtime_identity_queries_are_read_only_and_rolled_back():
    from bridge_school_api.book_runtime_identity import _read_identity_rows
    with psycopg.connect(DSN) as conn:
        session, tags = _read_identity_rows(conn)
        assert session[:3] == ("postgres", "book_world_test", "on")
        assert tags == []  # Plain PostgreSQL cannot manufacture Neon attestation.
        assert conn.info.transaction_status == 0
        # SET TRANSACTION READ ONLY was scoped to the transaction, then rolled back.
        assert conn.execute("SHOW transaction_read_only").fetchone()[0] == "off"
        conn.rollback()


def test_postgres_persistence_protected_teacher_replay_failure_and_rollback(monkeypatch):
    bundle, receipt = reviewed_fixture()
    school_id, source_id = str(uuid4()), str(uuid4())
    with psycopg.connect(DSN, autocommit=True) as setup:
        setup.execute(Path("tests/fixtures/book_world_postgres.sql").read_text())
        setup.execute("INSERT INTO public.school(school_id,stable_name) VALUES (%s,%s) ON CONFLICT (stable_name) DO NOTHING", (school_id, EXPECTED_SCHOOL))
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



def legacy_identity_fixture():
    """Random synthetic hash/IDs, never a production source."""
    bundle, receipt = reviewed_fixture()
    from bridge_contracts.book_material import identity
    source_id = str(uuid4())
    file_id = "Synthetic_" + uuid4().hex
    locator = "https://drive.google.com/file/d/" + file_id + "/view"
    bundle["source"].update(locator=locator, edition_id=str(uuid4()),
                            rendition_sha256=uuid4().hex + uuid4().hex)
    bundle["run"]["run_id"] = str(uuid4())
    receipt["rendition_sha256"] = bundle["source"]["rendition_sha256"]
    obj = bundle["objects"][0]
    obj["object_id"] = "BOOK-" + identity(bundle["source"], obj)
    receipt["claims"][0]["object_id"] = obj["object_id"]
    with psycopg.connect(DSN, autocommit=True) as setup:
        setup.execute(Path("tests/fixtures/book_world_postgres.sql").read_text())
        setup.execute("INSERT INTO public.school(school_id,stable_name) VALUES (%s,%s) ON CONFLICT(stable_name) DO NOTHING",
                      (str(uuid4()), EXPECTED_SCHOOL))
        school_id = str(setup.execute("SELECT school_id FROM public.school WHERE stable_name=%s",
                                     (EXPECTED_SCHOOL,)).fetchone()[0])
        setup.execute("INSERT INTO public.source(source_id,school_id,canonical_locator) VALUES(%s,%s,%s)",
                      (source_id, school_id, "drive:" + file_id))
    return bundle, receipt, school_id, source_id, "drive:" + file_id


def test_legacy_identity_dry_run_replay_stage_publish_and_visibility_rollback(monkeypatch):
    from database.book_source_reconciliation import reconcile_book_source_asset
    bundle, receipt, school_id, source_id, stored = legacy_identity_fixture()
    with psycopg.connect(DSN) as conn:
        identity_review = reviewed_identity_fixture(bundle, receipt, school_id, source_id, stored)
        args = dict(school_id=school_id, source_id=source_id, identity_receipt=identity_review)
        with pytest.raises(ValueError, match="ASSET_BINDING_REQUIRED"):
            persist_book_world(conn, bundle, receipt, **args)
        dry_identity = reconcile_book_source_asset(conn, bundle, receipt, **args, dry_run=True)
        assert dry_identity["new_asset_rows"] == dry_identity["new_link_rows"] == 1
        assert not dry_identity["committed"]
        with psycopg.connect(DSN, autocommit=True) as read:
            assert read.execute("SELECT count(*) FROM public.source_asset WHERE source_id=%s",
                                (source_id,)).fetchone()[0] == 0
        identity_result = reconcile_book_source_asset(conn, bundle, receipt, **args)
        replay_identity = reconcile_book_source_asset(conn, bundle, receipt, **args)
        assert identity_result["asset_id"] == replay_identity["asset_id"]
        assert replay_identity["new_asset_rows"] == replay_identity["new_link_rows"] == 0
        dry_stage = persist_book_world(conn, bundle, receipt, **args, dry_run=True)
        assert not dry_stage["committed"]
        with psycopg.connect(DSN, autocommit=True) as read:
            assert read.execute("SELECT count(*) FROM public.changeset WHERE changeset_id=%s",
                                (dry_stage["changeset_id"],)).fetchone()[0] == 0
        staged = persist_book_world(conn, bundle, receipt, **args)
        assert staged["version_ids"] == dry_stage["version_ids"]
        assert persist_book_world(conn, bundle, receipt, **args)["new_versions"] == 0
        with psycopg.connect(DSN, autocommit=True) as delivery:
            delivery.execute("UPDATE public.outbox_message SET status='published' WHERE changeset_id=%s",
                             (staged["changeset_id"],))  # Disposable fixture only.
        delivered = {"schema": "book-world-delivery-v1", "publication_hash": staged["publication_hash"],
                     "catalog_receipt_ref": "synthetic-delivery"}
        pub_args = dict(school_id=school_id, changeset_id=staged["changeset_id"],
                        delivery_receipt=delivered)
        dry_pub = publish_book_world(conn, **pub_args, dry_run=True)
        assert dry_pub["publication"] == "DRY_RUN_NOT_PUBLISHED" and not dry_pub["committed"]
        with psycopg.connect(DSN, autocommit=True) as read:
            assert read.execute("SELECT status FROM public.knowledge_version WHERE knowledge_version_id=%s",
                                (staged["version_ids"][0],)).fetchone()[0] == "staged"
        publish_book_world(conn, **pub_args)
        publish_book_world(conn, **pub_args)
        @contextmanager
        def connect_test():
            with psycopg.connect(DSN, row_factory=dict_row) as read:
                yield read
        monkeypatch.setattr(knowledge, "connect", connect_test)
        monkeypatch.setenv("BRIDGE_API_TOKEN", "synthetic-postgres-test-token")
        key = bundle["objects"][0]["object_id"]
        client = TestClient(app)
        request_args = dict(params={"stable_key": key},
                            headers={"Authorization": "Bearer synthetic-postgres-test-token"})
        response = client.get("/v1/knowledge/teacher/book", **request_args)
        assert response.status_code == 200, response.text
        assert response.json()["citations"][0]["locator"] == bundle["source"]["locator"]
        assert response.json()["fallback_performed"] is False
        with psycopg.connect(DSN, autocommit=True) as mutate:
            mutate.execute("UPDATE public.asset SET immutable_flag=false WHERE asset_id=%s",
                           (identity_result["asset_id"],))
        assert client.get("/v1/knowledge/teacher/book", **request_args).status_code == 409
        with pytest.raises(ValueError, match="ASSET_BINDING_REQUIRED"):
            publish_book_world(conn, **pub_args)
        with psycopg.connect(DSN, autocommit=True) as restore:
            restore.execute("UPDATE public.asset SET immutable_flag=true WHERE asset_id=%s",
                            (identity_result["asset_id"],))
        assert retire_book_world(conn, school_id=school_id, version_ids=staged["version_ids"]) == 1
        assert client.get("/v1/knowledge/teacher/book", **request_args).status_code == 404
        with psycopg.connect(DSN, autocommit=True) as read:
            assert read.execute("SELECT canonical_locator FROM public.source WHERE source_id=%s",
                                (source_id,)).fetchone()[0] == stored
            assert read.execute("SELECT count(*) FROM public.source_asset WHERE source_id=%s",
                                (source_id,)).fetchone()[0] == 1
            assert read.execute("SELECT count(*) FROM public.canon_activation WHERE knowledge_version_id=%s",
                                (staged["version_ids"][0],)).fetchone()[0] == 0


def test_missing_book_source_and_asset_bind_in_one_transaction():
    from database.book_source_reconciliation import (
        new_book_source_id, reconcile_book_source_asset,
    )
    bundle, receipt, school_id, old_source_id, _ = legacy_identity_fixture()
    with psycopg.connect(DSN, autocommit=True) as setup:
        setup.execute("DELETE FROM public.source WHERE source_id=%s", (old_source_id,))
    source_id = new_book_source_id(school_id, bundle["source"]["locator"])
    identity = reviewed_identity_fixture(
        bundle, receipt, school_id, source_id, bundle["source"]["locator"])
    args = dict(school_id=school_id, source_id=source_id,
                identity_receipt=identity, register_missing_source=True)
    with psycopg.connect(DSN) as conn:
        # The adapter must override a caller's snapshot isolation before its
        # advisory lock, so later source lookup sees concurrent commits.
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        dry = reconcile_book_source_asset(conn, bundle, receipt, **args, dry_run=True)
        assert (dry["new_source_rows"], dry["new_asset_rows"], dry["new_link_rows"]) == (1, 1, 1)
        with psycopg.connect(DSN, autocommit=True) as read:
            assert read.execute("SELECT count(*) FROM public.source WHERE source_id=%s",
                                (source_id,)).fetchone()[0] == 0
        def interrupted():
            raise RuntimeError("synthetic source-binding interruption")
        with pytest.raises(RuntimeError, match="source-binding interruption"):
            reconcile_book_source_asset(conn, bundle, receipt, **args, before_commit=interrupted)
        with psycopg.connect(DSN, autocommit=True) as read:
            assert read.execute("SELECT count(*) FROM public.source WHERE source_id=%s",
                                (source_id,)).fetchone()[0] == 0
        first = reconcile_book_source_asset(conn, bundle, receipt, **args)
        replay = reconcile_book_source_asset(conn, bundle, receipt, **args)
        assert (first["new_source_rows"], first["new_asset_rows"], first["new_link_rows"]) == (1, 1, 1)
        assert (replay["new_source_rows"], replay["new_asset_rows"], replay["new_link_rows"]) == (0, 0, 0)
        assert first["source_id"] == replay["source_id"] == source_id
    with psycopg.connect(DSN, autocommit=True) as read:
        assert read.execute("SELECT source_type,title,canonical_locator FROM public.source "
                            "WHERE source_id=%s AND school_id=%s", (source_id, school_id)).fetchone() == (
                                "book", bundle["source"]["title"], bundle["source"]["locator"])
        assert read.execute("SELECT count(*) FROM public.source_asset WHERE source_id=%s "
                            "AND asset_id=%s AND relation_type='embodies'",
                            (source_id, identity["asset_id"])).fetchone()[0] == 1
        assert read.execute("SELECT count(*) FROM public.canon_activation ca "
                            "JOIN public.knowledge_version_source kvs "
                            "ON kvs.knowledge_version_id=ca.knowledge_version_id "
                            "WHERE kvs.source_id=%s", (source_id,)).fetchone()[0] == 0


def test_source_registration_allows_update_only_guard():
    from database.book_source_reconciliation import new_book_source_id, reconcile_book_source_asset
    bundle, receipt, school_id, old_source_id, _ = legacy_identity_fixture()
    source_id = new_book_source_id(school_id, bundle["source"]["locator"])
    identity = reviewed_identity_fixture(bundle, receipt, school_id, source_id,
                                         bundle["source"]["locator"])
    with psycopg.connect(DSN, autocommit=True) as setup:
        setup.execute("DELETE FROM public.source WHERE source_id=%s", (old_source_id,))
        setup.execute("CREATE OR REPLACE FUNCTION public.synthetic_book_update_guard() "
                      "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$")
        setup.execute("CREATE TRIGGER synthetic_book_update_guard BEFORE UPDATE ON public.source "
                      "FOR EACH ROW EXECUTE FUNCTION public.synthetic_book_update_guard()")
    try:
        with psycopg.connect(DSN) as conn:
            result = reconcile_book_source_asset(conn, bundle, receipt, school_id=school_id,
                source_id=source_id, identity_receipt=identity, register_missing_source=True)
            assert result["new_source_rows"] == 1
    finally:
        with psycopg.connect(DSN, autocommit=True) as cleanup:
            cleanup.execute("DROP TRIGGER IF EXISTS synthetic_book_update_guard ON public.source")
            cleanup.execute("DROP FUNCTION IF EXISTS public.synthetic_book_update_guard()")


def test_source_registration_sees_noncooperating_writer_after_table_lock():
    from database.book_source_reconciliation import new_book_source_id, reconcile_book_source_asset
    bundle, receipt, school_id, old_source_id, _ = legacy_identity_fixture()
    source_id = new_book_source_id(school_id, bundle["source"]["locator"])
    identity = reviewed_identity_fixture(bundle, receipt, school_id, source_id,
                                         bundle["source"]["locator"])
    with psycopg.connect(DSN, autocommit=True) as setup:
        setup.execute("DELETE FROM public.source WHERE source_id=%s", (old_source_id,))
    competitor_id = str(uuid4())
    registrar_pids = Queue()

    def register():
        with psycopg.connect(DSN) as conn:
            conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            registrar_pids.put(conn.info.backend_pid)
            return reconcile_book_source_asset(conn, bundle, receipt, school_id=school_id,
                source_id=source_id, identity_receipt=identity, register_missing_source=True)

    with psycopg.connect(DSN) as blocker:
        blocker.execute("LOCK TABLE public.source IN SHARE ROW EXCLUSIVE MODE")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(register)
            registrar_pid = registrar_pids.get(timeout=5)
            deadline = time.monotonic() + 5
            while not blocker.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_locks WHERE pid=%s "
                "AND relation='public.source'::regclass AND mode='ShareRowExclusiveLock' "
                "AND NOT granted)", (registrar_pid,)).fetchone()[0]:
                assert time.monotonic() < deadline, "registrar did not reach the source lock"
                time.sleep(0.05)
            blocker.execute("INSERT INTO public.source(source_id,school_id,canonical_locator,title) "
                            "VALUES(%s,%s,%s,%s)",
                            (competitor_id, school_id, bundle["source"]["locator"],
                             bundle["source"]["title"]))
            blocker.commit()
            with pytest.raises(ValueError, match="BOOK_SOURCE_REGISTRY_CONFLICT"):
                future.result(timeout=10)
    with psycopg.connect(DSN, autocommit=True) as read:
        assert read.execute("SELECT source_id FROM public.source WHERE school_id=%s "
                            "AND canonical_locator=%s", (school_id,
                            bundle["source"]["locator"])).fetchall() == [(UUID(competitor_id),)]
        assert read.execute("SELECT count(*) FROM public.asset WHERE checksum_value=%s",
                            (bundle["source"]["rendition_sha256"],)).fetchone()[0] == 0


def test_identity_interruption_and_unknown_trigger_roll_back():
    from database.book_source_reconciliation import reconcile_book_source_asset
    bundle, receipt, school_id, source_id, stored = legacy_identity_fixture()
    identity_review = reviewed_identity_fixture(bundle, receipt, school_id, source_id, stored)
    args = dict(school_id=school_id, source_id=source_id, identity_receipt=identity_review)
    def fail():
        raise RuntimeError("synthetic interruption")
    with psycopg.connect(DSN) as conn:
        with pytest.raises(RuntimeError, match="synthetic interruption"):
            reconcile_book_source_asset(conn, bundle, receipt, **args, before_commit=fail)
        with psycopg.connect(DSN, autocommit=True) as read:
            assert read.execute("SELECT count(*) FROM public.asset WHERE checksum_value=%s",
                                (bundle["source"]["rendition_sha256"],)).fetchone()[0] == 0
        with psycopg.connect(DSN, autocommit=True) as setup:
            setup.execute("CREATE FUNCTION public.synthetic_asset_guard() RETURNS trigger "
                          "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$")
            setup.execute("CREATE TRIGGER synthetic_asset_guard BEFORE INSERT ON public.asset "
                          "FOR EACH ROW EXECUTE FUNCTION public.synthetic_asset_guard()")
        # Deliberately shadow catalogs on the idle caller's session.
        conn.execute('CREATE TEMP TABLE pg_trigger(tgrelid oid,tgisinternal boolean,tgenabled "char")')
        conn.execute('CREATE TEMP TABLE pg_rewrite(ev_class oid)')
        conn.commit()
        assert conn.execute("SELECT count(*) FROM pg_trigger").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM pg_catalog.pg_trigger "
                            "WHERE tgrelid='public.asset'::regclass AND NOT tgisinternal").fetchone()[0] > 0
        conn.rollback()
        try:
            with pytest.raises(ValueError, match="IDENTITY_EFFECT_REVIEW_REQUIRED"):
                reconcile_book_source_asset(conn, bundle, receipt, **args, dry_run=True)
        finally:
            with psycopg.connect(DSN, autocommit=True) as cleanup:
                cleanup.execute("DROP TRIGGER synthetic_asset_guard ON public.asset")
                cleanup.execute("DROP FUNCTION public.synthetic_asset_guard()")
        with psycopg.connect(DSN, autocommit=True) as read:
            assert read.execute("SELECT count(*) FROM public.asset WHERE checksum_value=%s",
                                (bundle["source"]["rendition_sha256"],)).fetchone()[0] == 0



@pytest.mark.parametrize("change", ["locator", "source_uuid", "school_uuid", "source_descriptor"])
def test_identity_review_cannot_be_reused_for_another_registry_binding(change):
    from database.book_source_reconciliation import reconcile_book_source_asset
    bundle, receipt, school_id, source_id, stored = legacy_identity_fixture()
    identity = reviewed_identity_fixture(bundle, receipt, school_id, source_id, stored)
    candidate = deepcopy(bundle)
    args = dict(school_id=school_id, source_id=source_id, identity_receipt=identity)
    if change == "locator":
        candidate["source"]["locator"] = "https://drive.google.com/file/d/Synthetic_other_file_0123456789/view"
    if change == "source_descriptor":
        candidate["source"]["source_id"] = "SYNTHETIC-OTHER-CATALOG-SOURCE"
    if change == "source_uuid":
        other_id = str(uuid4())
        with psycopg.connect(DSN, autocommit=True) as setup:
            setup.execute("INSERT INTO public.source(source_id,school_id,canonical_locator) VALUES(%s,%s,%s)",
                          (other_id, school_id, stored))  # Same URL, different registry identity.
        args["source_id"] = other_id
    if change == "school_uuid":
        args["school_id"] = str(uuid4())
    with psycopg.connect(DSN) as conn:
        with pytest.raises(ValueError, match="IDENTITY_REVIEW_BINDING"):
            reconcile_book_source_asset(conn, candidate, receipt, **args)
    with psycopg.connect(DSN, autocommit=True) as read:
        assert read.execute("SELECT count(*) FROM public.asset WHERE checksum_value=%s",
                            (bundle["source"]["rendition_sha256"],)).fetchone()[0] == 0


@pytest.mark.parametrize("mutation", ["asset", "link", "source", "citation", "citation_delete"])
def test_publication_holds_identity_rows_against_concurrent_mutation(mutation):
    from database.book_source_reconciliation import reconcile_book_source_asset
    bundle, receipt, school_id, source_id, stored = legacy_identity_fixture()
    identity = reviewed_identity_fixture(bundle, receipt, school_id, source_id, stored)
    args = dict(school_id=school_id, source_id=source_id, identity_receipt=identity)
    with psycopg.connect(DSN) as publisher:
        reconcile_book_source_asset(publisher, bundle, receipt, **args)
        staged = persist_book_world(publisher, bundle, receipt, **args)
        with psycopg.connect(DSN, autocommit=True) as delivery:
            delivery.execute("UPDATE public.outbox_message SET status='published' WHERE changeset_id=%s",
                             (staged["changeset_id"],))
        attempted = []
        def contender_before_commit():
            with psycopg.connect(DSN, autocommit=True) as contender:
                contender.execute("SET lock_timeout='150ms'")
                contender.execute("SET statement_timeout='2s'")
                statements = {
                    "asset": ("UPDATE public.asset SET immutable_flag=false WHERE asset_id=%s",
                              (identity["asset_id"],)),
                    "link": ("DELETE FROM public.source_asset WHERE source_id=%s AND asset_id=%s "
                             "AND relation_type='embodies'", (source_id, identity["asset_id"])),
                    "source": ("UPDATE public.source SET canonical_locator=%s WHERE source_id=%s",
                               (bundle["source"]["locator"], source_id)),
                    "citation": ("UPDATE public.knowledge_version_source SET source_locator='{}'::jsonb "
                                 "WHERE knowledge_version_id=%s AND source_id=%s "
                                 "AND relation_type='derived_from'", (staged["version_ids"][0], source_id)),
                    "citation_delete": ("DELETE FROM public.knowledge_version_source "
                                        "WHERE knowledge_version_id=%s AND source_id=%s "
                                        "AND relation_type='derived_from'", (staged["version_ids"][0], source_id)),
                }
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    contender.execute(*statements[mutation])
                attempted.append(mutation)
        result = publish_book_world(publisher, school_id=school_id, changeset_id=staged["changeset_id"],
            delivery_receipt={"schema": "book-world-delivery-v1",
                              "publication_hash": staged["publication_hash"],
                              "catalog_receipt_ref": "synthetic-delivery"},
            before_commit=contender_before_commit)
        assert result["publication"] == "WORLD_VERIFIED" and attempted == [mutation]
    with psycopg.connect(DSN, autocommit=True) as read:
        assert read.execute("SELECT immutable_flag FROM public.asset WHERE asset_id=%s",
                            (identity["asset_id"],)).fetchone()[0] is True
        assert read.execute("SELECT count(*) FROM public.source_asset WHERE source_id=%s AND asset_id=%s",
                            (source_id, identity["asset_id"])).fetchone()[0] == 1
        assert read.execute("SELECT canonical_locator FROM public.source WHERE source_id=%s",
                            (source_id,)).fetchone()[0] == stored
        assert read.execute("SELECT source_locator FROM public.knowledge_version_source "
                            "WHERE knowledge_version_id=%s AND source_id=%s "
                            "AND relation_type='derived_from'",
                            (staged["version_ids"][0], source_id)).fetchone()[0] == build_world_publication(bundle, receipt, identity)["records"][0]["content"]["citation"]
        assert read.execute("SELECT count(*) FROM public.domain_event WHERE changeset_id=%s "
                            "AND event_type='BookWorldPublished'", (result["changeset_id"],)).fetchone()[0] == 1
