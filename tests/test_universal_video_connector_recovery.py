"""Offline checks for the fixed connector envelope; PG execution is separate."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
CODE = REPO/'database/recovery/fixed_subset'
sys.path.insert(0,str(CODE))
import build_recovery as r
import connector_packet as c


@pytest.fixture
def staged(tmp_path,monkeypatch):
    monkeypatch.setattr(r,'ROOT',tmp_path)
    monkeypatch.setattr(c,'ROOT',tmp_path)
    for name in c.PINNED:
        dest=tmp_path/'source/database/migrations'/(name+'.sql')
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(REPO/'database/migrations'/(name+'.sql'),dest)
    dest=tmp_path/'source/database/tests/041_universal_video_queue.sql'
    dest.parent.mkdir(parents=True)
    shutil.copyfile(REPO/'database/tests/041_universal_video_queue.sql',dest)
    # Rendering fixture only, not an asserted PostgreSQL catalog baseline.
    baseline={'schema':dict(owner=r.OWNER,acl=[r.OWNER+'=UC/'+r.OWNER])}
    for name in ('batch','job','job_event','job_event_event_id_seq'):
        baseline[name]=dict(owner=r.OWNER,acl=[r.OWNER+'=arwdDxt/'+r.OWNER])
    signatures=[r.CLAIM,'video_queue.enqueue_drive_batch(text,text,text,text,text,text,text,text,jsonb)',
        'video_queue.heartbeat_job(uuid,uuid,text,integer)','video_queue.retry_job(uuid,uuid,text,text,integer,integer)',
        'video_queue.finish_job(uuid,uuid,text,text,jsonb,text)','video_queue.canonical_json_text(jsonb)',
        'video_queue.precanary_idle_snapshot()']
    for sig in signatures:
        baseline[sig]=dict(owner=r.OWNER,acl=[r.OWNER+'=X/'+r.OWNER],definition_md5='a'*32,security_definer=True,config=['search_path=pg_catalog, video_queue'])
    monkeypatch.setattr(r,'projection',lambda _:baseline)
    (tmp_path/'catalog-baseline.json').write_text(json.dumps([{'registry':[{'migration_key':'0058_universal_video_terminal_v2_gate','checksum':'c'*64}]}]))
    (tmp_path/'semantic-catalog-baseline.json').write_text(json.dumps({'catalog_md5':'a'*32}))
    (tmp_path/'target.json').write_text(json.dumps(dict(project_id='synthetic-project',branch_id='synthetic-branch',endpoint_id='synthetic-endpoint',database_name='neondb')))
    (tmp_path/'recovery').mkdir()
    raw=(CODE/(r.KEY+'.sql')).read_bytes()
    (tmp_path/'recovery'/(r.KEY+'.sql')).write_bytes(raw)
    (tmp_path/'model-result.json').write_text(json.dumps({'recovery_sha256':hashlib.sha256(raw).hexdigest()}))
    return baseline


@pytest.mark.parametrize('sql,count',[
    ("SELECT ';'; SELECT 2;",2),
    ('DO $x$ BEGIN PERFORM 1; END $x$; SELECT 2;',2),
    ('/* outer /* ; */ end */ SELECT 1;-- comment ;\nSELECT 2;',2),
    ("SELECT E'a\\\';b'; SELECT 'a'';b';",2),
    ('SELECT "a;b"; SELECT 2;',2),
])
def test_sql_splitter_preserves_all_bytes(sql,count):
    parts=c.split_sql(sql)
    assert len(parts)==count and ''.join(parts)==sql


@pytest.mark.parametrize('sql',["SELECT '",'/* missing','DO $x$ BEGIN;'])
def test_sql_splitter_rejects_unterminated(sql):
    with pytest.raises(ValueError): c.split_sql(sql)


def test_pinned_bodies_and_atomic_order(staged):
    payload=c.install_payload()
    statements=payload['sql_statements']
    for key in c.PINNED:
        body=c.migration_statements(key)
        start=statements.index(body[0])
        assert statements[start:start+len(body)]==body
    assert all(c.bare(s).upper() not in ('BEGIN','COMMIT','ROLLBACK') for s in statements)
    assert statements[-1]=='SELECT data AS receipt FROM pg_temp.uv_receipt'
    assert statements.index(c.TEMP_REVOKE)>statements.index(c.TEMP_GRANT)
    assert any('INSTALL_EXACT_FUNCTION_ACL' in s for s in statements)
    assert 'endpoint_id' not in payload


def test_source_tamper_blocks(staged):
    key=next(iter(c.PINNED))
    with (c.ROOT/'source/database/migrations'/(key+'.sql')).open('a') as f: f.write('\n')
    with pytest.raises(ValueError,match='checksum'): c.install_payload()


def receipt():
    return dict(before_old_data={},data_digest={},catalog_md5='b'*32,target='synthetic-branch',state='INSTALLED_RUNTIME_HOLD')


def test_verifier_cannot_restore_or_write_ledger(staged):
    statements=c.verify_payload(receipt())['sql_statements']
    assert c.TEMP_GRANT in statements and c.TEMP_REVOKE in statements
    assert not any('DROP TRIGGER' in s or 'INSERT INTO public.schema_migration' in s for s in statements)
    assert any('RECOVERY_FULL_HISTORY_DRIFT' in s for s in statements)


def test_recovery_orders_checks_and_cleanup(staged):
    statements=c.recover_payload(receipt())['sql_statements']
    ledger=next(i for i,s in enumerate(statements) if 'INSERT INTO public.schema_migration' in s)
    for label in ('RECOVERY_LEGACY_CATALOG_MISMATCH','RECOVERY_SUBSET_NOT_DISABLED','RECOVERY_HISTORY_CHANGED'):
        assert next(i for i,s in enumerate(statements) if label in s)<ledger
    assert statements.index(c.TEMP_REVOKE)>ledger
    assert not any('DELETE FROM' in s or 'DROP TABLE' in s or 'DROP ROLE' in s for s in statements)


@pytest.mark.parametrize('change',[{'state':'UNKNOWN'},{'target':'other'},{'catalog_md5':'xyz'},{'extra':1}])
def test_invalid_receipts_rejected(staged,change):
    bad={**receipt(),**change}
    with pytest.raises(ValueError): c.recover_payload(bad)


def test_model_restores_without_mutating_history(staged):
    rows=[{'id':1,'source':'same'},{'id':2,'source':'same'}]
    reservations={'same':[1,2]}
    installed=r.expected_installed_model(staged)
    result,after,held=r.model_recover(installed,staged,rows,reservations)
    assert {k:result[k] for k in staged}==staged
    assert after==rows and held==reservations
    assert after is not rows and held is not reservations
    assert not result['subset_execute'] and result['subset_roles_retained']==[r.SUBSET,*r.CAPS]


def test_complete_function_acl_has_no_grantee_allowlist_filter():
    matrix=c.function_acl_matrix()
    assert len(c.FUNCTION_CAPS)==12 and len(c.OWNER_ONLY)==13
    assert 'a.grantee,a.grantor,a.privilege_type,a.is_grantable' in matrix
    assert matrix.count('EXCEPT')==2 and 'WHERE a.grantee IN' not in matrix
    assert '=24' in c.function_acl_matrix(recovered=True)


def test_041_runtime_acl_tracks_scoped_claim():
    sql=(REPO/'database/tests/041_universal_video_queue.sql').read_text()
    managed=sql.split('DO $managed_acl$',1)[1].split('END $managed_acl$',1)[0]
    assert "('video_subset_worker_v2','video_queue.subset_claim_job(uuid,text,integer,text,text)')" in managed
    assert c.function_acl_matrix() in sql


def test_repair_external_history_matches_and_is_never_a_pending_migration():
    raw=(CODE/(r.KEY+'.sql')).read_bytes()
    rows=[line.split('\t') for line in (REPO/'database/external_migration_history.tsv').read_text().splitlines() if line.startswith(r.KEY+'\t')]
    assert len(rows)==1 and rows[0][1]==hashlib.sha256(raw).hexdigest()
    assert not (REPO/'database/migrations'/(r.KEY+'.sql')).exists()
    include=(REPO/'database/tests/fixed_subset_recovery_state.sql.inc').read_text()
    assert rows[0][1] in include and 'LEGACY_RECOVERY_STATE_INVALID' in include
    for n in ('041','042','043'):
        text=next((REPO/'database/tests').glob(n+'_*.sql')).read_text()
        assert '\\ir fixed_subset_recovery_state.sql.inc' in text
        assert "AND NOT :'legacy_recovered'::boolean AS fixed_subset_installed" in text


def test_no_implicit_target(tmp_path,monkeypatch):
    monkeypatch.setattr(r,'ROOT',tmp_path)
    with pytest.raises(FileNotFoundError): c.prefix()


def test_incomplete_target_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(r,'ROOT',tmp_path)
    (tmp_path/'target.json').write_text('{"database_name":"neondb"}')
    with pytest.raises(ValueError,match='explicit target'): c.prefix()
