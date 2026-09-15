#!/usr/bin/env python3
"""Production guards for the evidence-preserving 3.1 FREE r26 route."""
from pathlib import Path
from types import SimpleNamespace
import json
import os
import bridge_runtime_hardening_r26 as runtime
from bridge_output_scoped_idempotency import existing_same_revision_done
import check_completed_job as preflight
import run_master_3_1_free as base

def test_production_route_is_confirmed_r26_with_inheritance_chain():
    adapter = Path('run_drive_3_1_free_generic.py').read_text(encoding='utf-8')
    workflow = Path('.github/workflows/bridge-video-3.1-free.yml').read_text(encoding='utf-8')
    runtime_source = Path('bridge_runtime_hardening_r26.py').read_text(encoding='utf-8')
    primary = Path('bridge_vision/bridgit_primary_production.py').read_text(encoding='utf-8')
    assert 'bridge_runtime_hardening_r26' in adapter
    assert 'BRIDGE_REQUESTED_ALGORITHM_REVISION: "3.1-free-r26"' in workflow
    assert 'BRIDGE_DIARIZATION_ENABLED: "true"' in workflow
    assert 'WHISPER_MODEL: small' in workflow
    assert 'BRIDGE_PAID_CLOUD: "false"' in workflow
    assert 'BRIDGE_BILLING_FALLBACK: "false"' in workflow
    assert 'import bridge_runtime_hardening_r25_16 as previous' in runtime_source
    assert 'previous.install(token_func)' in runtime_source
    assert 'install_primary_recognizer' in runtime_source
    assert 'VISUAL_PRIMARY_RECOGNIZED' in primary
    assert 'VISUAL_ONLY; NO_DECK_COMPLEMENT' in primary
    assert 'git diff-tree --no-commit-id --name-only -r -m "$GITHUB_SHA"' in workflow
    assert 'Expected exactly one run request in triggering commit' in workflow

def test_runtime_does_not_filter_master_canon_evidence():
    raw = base.course_link_candidates
    previous = os.environ.get('BRIDGE_REQUESTED_ALGORITHM_REVISION')
    os.environ['BRIDGE_REQUESTED_ALGORITHM_REVISION'] = runtime.REVISION
    try: runtime.install(lambda: 'test-token')
    finally:
        if previous is None: os.environ.pop('BRIDGE_REQUESTED_ALGORITHM_REVISION', None)
        else: os.environ['BRIDGE_REQUESTED_ALGORITHM_REVISION'] = previous
    assert base.course_link_candidates is raw

def test_terminal_preflight_matches_revision_receipt_contract():
    source = Path('check_completed_job.py').read_text(encoding='utf-8')
    assert 'receipt_matches_revision' in source
    assert 'knowledge_status_matches_revision' not in source
    assert 'CLEANUP_ACK' in source

def test_terminal_preflight_scopes_same_revision_to_output_generation():
    job = '41daa4ca6e09d13e366c578b7c53ae31'
    assert "'1ProdRepeatOutputFolder' in parents" in preflight.receipt_search_query(job, '1ProdRepeatOutputFolder')
    try: preflight.receipt_search_query(job, 'bad folder/id')
    except RuntimeError as exc: assert 'INVALID_OUTPUT_FOLDER_ID' in str(exc)
    else: raise AssertionError('invalid Drive id must fail closed')

def test_runtime_same_revision_done_is_scoped_to_requested_output_folder():
    job = '41daa4ca6e09d13e366c578b7c53ae31'; revision = runtime.REVISION
    done = {'id':'done','modifiedTime':'2026-08-19T13:00:00Z'}; method = {'id':'method','modifiedTime':'2026-08-19T13:00:01Z'}
    payloads = {'done':{'status':'AI_DONE','job_id':job,'algorithmRevision':revision,'masterPdf':{'driveId':'pdf-new'}}, 'method':{'status':'METHODOLOGY_READY','job_id':job,'algorithmRevision':revision,'masterPdfDriveId':'pdf-new'}}
    def search(_token, query):
        if "'targetOutput' in parents" not in query: return []
        if f'AI_DONE_{job}.json' in query: return [done]
        if f'METHODOLOGY_READY_{job}.json' in query: return [method]
        return []
    fake = SimpleNamespace(base=SimpleNamespace(io=SimpleNamespace(search=search)), _read_json=lambda _token,item:payloads.get(item.get('id')))
    previous = os.environ.get('BRIDGE_OUTPUT_FOLDER_ID'); os.environ['BRIDGE_OUTPUT_FOLDER_ID']='targetOutput'
    try: found = existing_same_revision_done(fake,'token',job,revision)
    finally:
        if previous is None: os.environ.pop('BRIDGE_OUTPUT_FOLDER_ID',None)
        else: os.environ['BRIDGE_OUTPUT_FOLDER_ID']=previous
    assert found == payloads['done']

def test_periodic_auto_discovery_remains_disabled():
    source = Path('.github/workflows/bridge-video-auto-discovery.yml').read_text(encoding='utf-8')
    assert 'schedule:' not in source
    assert 'actions: write' not in source
    assert 'GOOGLE_DRIVE_OAUTH_JSON' not in source
    assert 'discover_next_drive_job.py' not in source
    assert 'workflow_dispatch:' in source
    assert 'AUTO_DISCOVERY_DISABLED' in source
