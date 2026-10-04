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
        setup.execute("INSERT INTO public.school VALUES (%s,%s) ON CONFLICT(stable_name) DO NOTHING",
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
        args = dict(school_id=school_id, source_id=source_id)
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
        with pytest.raises(ValueError, match="PUBLICATION_SCOPE"):
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


def test_identity_interruption_and_unknown_trigger_roll_back():
    from database.book_source_reconciliation import reconcile_book_source_asset
    bundle, receipt, school_id, source_id, _ = legacy_identity_fixture()
    args = dict(school_id=school_id, source_id=source_id)
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
