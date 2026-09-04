import database.video_correction_review_store as store


class FakeCursor:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def execute(self, query, params=None):
        self.calls.append((query, params))

    def fetchone(self):
        return next(self.rows)


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def cursor(self):
        return self._cursor


def test_resolves_exact_payload_only_from_database(monkeypatch):
    receipt_sha = "a" * 64
    payload = {"receipt_sha256": receipt_sha, "status": "VERIFIED"}
    cursor = FakeCursor([("bidding.video_correction_review_receipt",), (payload,)])
    monkeypatch.setattr(store, "normalize_dsn", lambda _: "postgresql://trusted")
    monkeypatch.setattr(store.psycopg, "connect", lambda *_, **__: FakeConnection(cursor))
    resolver = store.DatabaseCorrectionReceiptResolver("configured")
    assert resolver(receipt_sha) == payload
    assert cursor.calls[-1][1] == (receipt_sha,)
    assert "registry.status='active'" in cursor.calls[-1][0]
    assert "receipt.recorded_by_principal" in cursor.calls[-1][0]
    assert "pg_has_role(attestor.oid,capability.oid,'MEMBER')" in cursor.calls[-1][0]
    assert "CORRECTION_REVIEW" in cursor.calls[-1][0]


def test_rejects_invalid_hash_without_database_call(monkeypatch):
    monkeypatch.setattr(store, "normalize_dsn", lambda _: "postgresql://trusted")
    monkeypatch.setattr(
        store.psycopg,
        "connect",
        lambda *_, **__: (_ for _ in ()).throw(AssertionError("database must not be called")),
    )
    resolver = store.DatabaseCorrectionReceiptResolver("configured")
    assert resolver("not-a-hash") is None
