import pytest

from oracle_autopilot.database_target import backend, expected_database, validate_pinned_dsn
from oracle_autopilot.worker import validate_neon_direct_dsn
from oracle_autopilot.github_role_callback import validate_callback_dsn, CallbackContractError
from oracle_autopilot.reconcile_db import normalize_dsn

BASE = "postgresql://autopilot_light_worker_login:test-only@db.example.invalid:5432/autopilot_prod?sslmode=verify-full&channel_binding=require"

@pytest.fixture
def target(monkeypatch):
    monkeypatch.setenv("AUTOPILOT_DB_BACKEND", "postgresql")
    monkeypatch.setenv("AUTOPILOT_PG_HOST", "db.example.invalid")
    monkeypatch.setenv("AUTOPILOT_PG_DATABASE", "autopilot_prod")
    monkeypatch.setenv("AUTOPILOT_PG_PORT", "5432")
    monkeypatch.setenv("AUTOPILOT_EXPECTED_DB_USER", "autopilot_light_worker_login")


def test_default_remains_neon(monkeypatch):
    monkeypatch.delenv("AUTOPILOT_DB_BACKEND", raising=False)
    assert backend() == "neon"
    assert expected_database() == "neondb"
    with pytest.raises(RuntimeError):
        validate_neon_direct_dsn(BASE)


def test_all_autopilot_entrypoints_accept_pinned_target(target):
    assert validate_neon_direct_dsn(BASE) == BASE
    callback = BASE.replace("autopilot_light_worker_login", "autopilot_callback_login")
    assert validate_callback_dsn(callback) == callback
    reconcile = BASE.replace("autopilot_light_worker_login", "bridge_school_worker_principal")
    assert normalize_dsn(reconcile) == reconcile


@pytest.mark.parametrize("old,new", [
    ("db.example.invalid", "other.example.invalid"),
    ("autopilot_prod", "school"),
    (":5432/", ":5433/"),
    (":5432/", ":0/"),
    ("verify-full", "require"),
    ("verify-full", "disable"),
    ("channel_binding=require", "channel_binding=prefer"),
    ("autopilot_light_worker_login", "postgres"),
    (":test-only@", "@"),
])
def test_rejects_wrong_destination_or_weakened_authentication(target, old, new):
    with pytest.raises(RuntimeError):
        validate_neon_direct_dsn(BASE.replace(old, new))


@pytest.mark.parametrize("suffix", [
    "&host=other.example.invalid", "&hostaddr=127.0.0.1", "&port=9999",
    "&dbname=school", "&user=postgres", "&service=other", "&options=-csearch_path=public",
    "&sslmode=disable", "&channel_binding=prefer", "#fragment", "&bad",
])
def test_rejects_libpq_overrides_and_ambiguous_query(target, suffix):
    with pytest.raises(ValueError):
        validate_pinned_dsn(BASE + suffix, expected_user="autopilot_light_worker_login")


def test_missing_explicit_identity_fails(target, monkeypatch):
    monkeypatch.delenv("AUTOPILOT_EXPECTED_DB_USER")
    with pytest.raises(RuntimeError):
        validate_neon_direct_dsn(BASE)


def test_unknown_backend_fails_closed(target, monkeypatch):
    monkeypatch.setenv("AUTOPILOT_DB_BACKEND", "typo")
    with pytest.raises(ValueError):
        normalize_dsn(BASE)
    with pytest.raises(CallbackContractError):
        validate_callback_dsn(BASE)


def test_reconcile_does_not_rewrite_oracle_to_neon(target):
    dsn = BASE.replace("autopilot_light_worker_login", "bridge_school_worker_principal")
    assert "db.example.invalid" in normalize_dsn(dsn)
    with pytest.raises(ValueError):
        normalize_dsn(dsn.replace("db.example.invalid", "unapproved.neon.tech"))
