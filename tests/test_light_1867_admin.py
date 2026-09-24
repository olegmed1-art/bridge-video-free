import base64
import json
import pytest
from ops import light_1867_admin as admin


def test_sql_evidence_has_no_raw_interpolation():
    evidence = {'text': "'); DROP TABLE important; --\n\\"}
    query = admin.call_sql('APPLY', evidence)
    encoded = query.split("decode('")[1].split("'",1)[0]
    assert json.loads(base64.b64decode(encoded)) == evidence
    assert 'DROP TABLE' not in query
    with pytest.raises(ValueError):
        admin.call_sql("APPLY'); --", {})


def test_candidate_drift_refused(monkeypatch):
    monkeypatch.setattr(admin, 'CANDIDATE_SHA', '0'*64)
    with pytest.raises(ValueError, match='SOURCE_DRIFT'):
        admin.installer()


def test_independent_namespace_and_runtime_denials():
    sql = admin.installer()
    assert 'light_dispatch_1867_recover' not in sql
    assert 'CREATE OR REPLACE' not in sql
    assert 'SECURITY DEFINER' not in sql
    assert 'GRANT ' not in sql
    assert admin.PRODUCTION in sql and admin.CHILD in sql
    assert "THEN 'PRODUCTION' ELSE 'REHEARSAL' END" in sql
    assert 'LIGHT_1867_ADMIN_SNAPSHOT_DRIFT' in sql
    assert 'LIGHT_1867_ADMIN_UNEXPECTED_ACTIVE_TASK' in sql


def test_rehearsal_uses_exact_installer():
    query = admin.rehearsal()
    assert admin.installer().replace("'", "''") in query
    assert "current_setting('neon.branch_id',true) IS DISTINCT FROM '"+admin.CHILD+"'" in query
    assert 'LIGHT_1867_ADMIN_REHEARSAL_PASS' in query
    assert "EXCEPTION WHEN SQLSTATE 'Z1867'" in query
