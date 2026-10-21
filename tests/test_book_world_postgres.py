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
        setup.execute("INSERT INTO public.school VALUES (%s,%s) ON CONFLICT (stable_name) DO NOTHING",
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


def _synthetic_pipeline_pdf():
    """Three valid fixture pages; no published book content or external parser."""
    stream = b"BT /F1 12 Tf 20 100 Td (Synthetic conditional claim. iv) Tj ET"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R 5 0 R 7 0 R] /Count 3 >>"]
    for page in range(3):
        content_id = 4 + page * 2
        objects.extend([
            ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
             "/Resources << /Font << /F1 9 0 R >> >> /Contents %d 0 R >>" % content_id).encode(),
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        ])
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    data, offsets = b"%PDF-1.7\n", [0]
    for number, value in enumerate(objects, 1):
        offsets.append(len(data))
        data += str(number).encode() + b" 0 obj\n" + value + b"\nendobj\n"
    xref = len(data)
    data += b"xref\n0 10\n0000000000 65535 f \n"
    data += b"".join(("%010d 00000 n \n" % offset).encode() for offset in offsets[1:])
    return data + b"trailer\n<< /Size 10 /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n"


def test_real_storage_source_staging_review_to_teacher_consumer(monkeypatch, tmp_path):
    """Real local stores/readers; synthetic reviews and delivery, never pilot admission."""
    import hashlib
    import json
    import sqlite3
    from bridge_contracts.book_material import produce_bundle
    from bridge_contracts.book_material_store import BookStagingStore
    from database.source_registration import register_source, SourceRegistrationError
    from database.book_source_reconciliation import reconcile_book_source_asset
    from evolutionary_course import book_teacher_consumer as consumer

    pdf = tmp_path / "synthetic-pipeline.pdf"
    pdf.write_bytes(_synthetic_pipeline_pdf())
    template, review = reviewed_fixture()
    source = {**template["source"], "edition_id": str(uuid4()),
              "locator": "https://example.invalid/synthetic-pipeline-" + uuid4().hex,
              "rendition_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()}
    records = [record(page=page) for page in range(3)]
    for candidate in records:
        candidate["content"]["conditions"] = ["Synthetic visible-information condition", "Synthetic opening-lead condition"]
        candidate["content"]["exceptions"] = ["Synthetic no-contract-guarantee limit", "Synthetic entry-and-tempo limit"]
    value = produce_bundle(source, {**template["run"], "run_id": str(uuid4())}, records)
    review.update(rendition_sha256=source["rendition_sha256"], source_size_bytes=pdf.stat().st_size)
    claim = deepcopy(review["claims"][0])
    review["claims"] = [{**deepcopy(claim), "object_id": obj["object_id"],
                         "content_sha256": digest(obj["content"]),
                         "anchor_sha256": digest(obj["anchor"])} for obj in value["objects"]]
    source_id = str(uuid4())
    with psycopg.connect(DSN) as setup:
        setup.execute(Path("tests/fixtures/book_world_postgres.sql").read_text())
        setup.commit()  # DDL is a separate disposable-fixture operation.
        setup.execute("INSERT INTO public.school VALUES(%s,%s) ON CONFLICT(stable_name) DO NOTHING",
                      (str(uuid4()), EXPECTED_SCHOOL))
        school_id = str(setup.execute("SELECT school_id FROM public.school WHERE stable_name=%s",
                                     (EXPECTED_SCHOOL,)).fetchone()[0])
        args = dict(school_id=school_id, source_id=source_id, source_type="book",
                    title=source["title"], locator=source["locator"])
        with setup.cursor() as cur:
            with pytest.raises(SourceRegistrationError, match="SOURCE_REGISTRATION_LOCK_ORDER_REQUIRED"):
                register_source(cur, **args)
        assert setup.execute("SELECT count(*) FROM public.source WHERE source_id=%s", (source_id,)).fetchone()[0] == 0
        setup.commit()  # Release school setup locks before the source barrier.
        with setup.cursor() as cur:
            assert register_source(cur, **args) == 1
            assert register_source(cur, **args) == 0

    staged_path = tmp_path / "book-staging.sqlite"
    with sqlite3.connect(staged_path) as local:
        staged = BookStagingStore(local).stage(value)
        assert staged["new_ids"] == staged["new_observations"] == 3
        assert staged["publication"] == "NOT_PUBLISHED"
    with sqlite3.connect(staged_path) as local:
        stored, local_status = local.execute("SELECT bundle_json,status FROM book_batch WHERE run_id=?",
                                            (value["run"]["run_id"],)).fetchone()
        value = json.loads(stored)
        assert local_status == "COMMITTED_LOCAL" and value["publication"] == "NOT_PUBLISHED"
        assert all(obj["review"] == "NOT_REVIEWED" and obj["verification"] == "M0" for obj in value["objects"])

    publication = build_world_publication(value, review)
    expected_receipt = {"schema": publication["schema"], "changeset_id": None,
        "version_ids": [None] * 3, "new_versions": None, "authority_lane": "WORLD_EXTERNAL",
        "publication_hash": None, "outbox_delivery": "REQUIRES_READBACK",
        "canon_activation_performed": False, "dry_run": False, "committed": None}
    packet = {"content_contract": publication["schema"],
        "import_state": {"knowledge_import_committed": False, "observed_import_receipt": None},
        "source": {**source, "registry_source_id": source_id}, "run": value["run"],
        "review_evidence_summary": {"synthetic_only": True, "not_real_E01_E03": True},
        "expected_import_receipt": expected_receipt, "cards": []}
    for label, projected in zip(consumer.LABELS, publication["records"], strict=True):
        content, key = projected["content"], projected["stable_key"]
        citation = deepcopy(content["citation"])
        packet["cards"].append({"card_label": label, "candidate_id": "SYNTHETIC-" + label,
            "name": "Synthetic " + label, "skill": "Synthetic skill", "stable_key": key, "object_id": key,
            **{field: deepcopy(content[field]) for field in ("statement", "conditions", "exceptions", "origin", "verification")},
            "citation": citation, "source_pages": {"pdf_page_index_0based": citation["pdf_page_index"],
                "pdf_page_number_1based": citation["pdf_page_index"] + 1,
                "printed_page_label": citation["printed_page_label"]},
            "source_review_id": review["review_id"], "knowledge_item_id": None, "knowledge_version_id": None})
    unbound = consumer.build_card_map(packet)
    reads = []

    @contextmanager
    def connect_test():
        with psycopg.connect(DSN, row_factory=dict_row) as connection:
            yield connection
    monkeypatch.setattr(knowledge, "connect", connect_test)
    monkeypatch.setenv("BRIDGE_API_TOKEN", "synthetic-pipeline-token")
    client = TestClient(app)
    headers = {"Authorization": "Bearer synthetic-pipeline-token"}

    def query_actual(**kwargs):
        reads.append(kwargs)
        return knowledge.query_knowledge(**kwargs)

    from bridge_contracts.book_world import render_teacher_book
    def consume(mapping, label):
        context = {"topic": consumer.TOPICS[label], "stage": "INITIAL_PLAN",
            "dummy_exposed": True, "opening_lead_made": True, "asks_for": "PRINCIPLE_EXPLANATION"}
        return consumer.consume_card(mapping, label, context,
            query_knowledge=query_actual, render_teacher_book=render_teacher_book)

    assert consume(unbound, "E01")["reason"] == "IMPORT_NOT_COMMITTED" and reads == []
    identity_review = reviewed_identity_fixture(value, review, school_id, source_id, source["locator"])
    with psycopg.connect(DSN) as writer:
        identity_result = reconcile_book_source_asset(writer, value, review, school_id=school_id,
            source_id=source_id, identity_receipt=identity_review)
        assert identity_result["new_source_rows"] == 0 and identity_result["new_asset_rows"] == 1
        actual = persist_book_world(writer, value, review, school_id=school_id,
            source_id=source_id, identity_receipt=identity_review)
        assert actual["committed"] and actual["new_versions"] == 3
    with psycopg.connect(DSN) as read:
        event = read.execute("SELECT payload FROM public.domain_event WHERE changeset_id=%s "
                             "AND event_type='BookWorldRecorded'", (actual["changeset_id"],)).fetchone()[0]
        observed_ids = {str(row[0]) for row in read.execute(
            "SELECT knowledge_version_id FROM public.knowledge_version WHERE knowledge_version_id=ANY(%s::uuid[])",
            (actual["version_ids"],)).fetchall()}
        assert observed_ids == set(actual["version_ids"])
    bound = consumer.bind_import_receipt(unbound, actual, event)
    assert consume(bound, "E01")["reason"] == "BOOK_WORLD_GAP"  # A committed staging receipt is insufficient.
    first_key = bound["cards"][0]["stable_key"]
    assert client.get("/v1/knowledge/teacher/book", params={"stable_key": first_key}, headers=headers).status_code == 404

    with psycopg.connect(DSN) as delivery:
        # Only this disposable fixture simulates external delivery. No catalog or pilot receipt is asserted.
        delivery.execute("UPDATE public.outbox_message SET status='published' WHERE changeset_id=%s",
                         (actual["changeset_id"],))
    with psycopg.connect(DSN) as writer:
        published = publish_book_world(writer, school_id=school_id, changeset_id=actual["changeset_id"],
            delivery_receipt={"schema": "book-world-delivery-v1", "publication_hash": actual["publication_hash"],
                              "catalog_receipt_ref": "synthetic-disposable-delivery-only"})
        assert published["publication"] == "WORLD_VERIFIED"

    def counts():
        with psycopg.connect(DSN) as read:
            return tuple(read.execute("SELECT count(*) FROM public." + table).fetchone()[0]
                for table in ("source", "asset", "source_asset", "knowledge_item", "knowledge_version",
                              "changeset", "domain_event", "outbox_message", "canon_activation"))
    before_reads = counts()
    for card in bound["cards"]:
        result = consume(bound, card["card_label"])
        assert result["status"] == "EXPLANATION_READY"
        assert result["book_response"]["knowledge_version_id"] == card["knowledge_version_id"]
        assert result["book_response"]["citations"] == [card["expected_content"]["citation"]]
        assert all(text in result["explanation_with_page"]
                   for field in ("conditions", "exceptions") for text in card["expected_content"][field])
        assert result["canon_activation_performed"] is result["domain_writes_performed"] is False
        response = client.get("/v1/knowledge/teacher/book", params={"stable_key": card["stable_key"]}, headers=headers)
        assert response.status_code == 200 and response.json() == result["book_response"]
    assert counts() == before_reads
    with psycopg.connect(DSN) as writer:
        assert retire_book_world(writer, school_id=school_id, version_ids=actual["version_ids"]) == 3
    assert consume(bound, "E01")["reason"] == "BOOK_WORLD_GAP"
    assert client.get("/v1/knowledge/teacher/book", params={"stable_key": first_key}, headers=headers).status_code == 404
    with psycopg.connect(DSN) as read:
        assert read.execute("SELECT count(*) FROM public.source_asset WHERE source_id=%s", (source_id,)).fetchone()[0] == 1
        assert read.execute("SELECT count(*) FROM public.canon_activation WHERE knowledge_version_id=ANY(%s::uuid[])",
                            (actual["version_ids"],)).fetchone()[0] == 0
