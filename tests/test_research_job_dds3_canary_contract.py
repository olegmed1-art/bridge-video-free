from pathlib import Path


def test_dds3_research_canary_is_fail_closed_and_idempotent():
    workflow = Path('.github/workflows/research-job-dds3-canary.yml').read_text(encoding='utf-8')
    required = [
        '/research-job canary-dds3-idempotency',
        'environment: database-production',
        'kind="DDS3"',
        'second["research_id"] != first["research_id"]',
        'third["research_id"] != first["research_id"]',
        'validation.get("engine") == "DDS3"',
        'validation.get("fallback_used") is False',
        'compute.get("operation") == "dd_table"',
        'compute.get("input_validated") is True',
        'compute.get("request_sha256")',
        'compute.get("deal_pbn_sha256")',
        'child["attempts"] != 1',
        'provenance.get("execution_path") == "oracle_local_dds3"',
        'canonical_promotion=false',
        'REVOKE bridge_school_app',
    ]
    for marker in required:
        assert marker in workflow
