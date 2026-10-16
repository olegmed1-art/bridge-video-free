"""Fail closed if the manual 0355 scripts drift from the installed patch."""
from pathlib import Path

from pglast import parse_sql


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / 'database/migrations/0355_autopilot_light_v3_compatibility_fence.sql').read_text()
CANARY = 'f05c605f-f664-4ff7-9927-a039f000a929'
HEAD = '2586929313ab40326d64353b513ff86e5ae3350c'


def source_chunk(source, marker):
    assert source.count(marker) == 2
    return source.split(marker)[1]


def test_exact_patch_and_bounded_transaction():
    for name in ('retire', 'refence'):
        source = (ROOT / f'ops/autopilot_light_0355_{name}.sql').read_text()
        parse_sql('\n'.join(line for line in source.splitlines() if not line.startswith('\\')))
        assert source_chunk(source, '$a$') == source_chunk(MIGRATION, '$anchor$')
        assert source_chunk(source, '$p$') == source_chunk(MIGRATION, '$replacement$')
        assert source.count('LIGHT_RUNTIME_MAILBOX_V3_COMPATIBILITY_FENCE_V1') == 1
        assert 'LOCK TABLE autopilot.task, autopilot.role_dispatch_outbox' in source
        assert "hashtext('bridge_school_schema_migrations')" in source
        assert CANARY in source
        assert 'DROP TABLE' not in source
        assert 'DELETE FROM public.schema_migration' not in source
        assert "status='CLAIMED' OR claim_until IS NOT NULL" in source


def test_retirement_pins_existing_task_and_preserves_recovery():
    source = (ROOT / 'ops/autopilot_light_0355_retire.sql').read_text()
    assert HEAD in source
    assert "goal_json->>'mailbox_pr'='1703'" in source
    assert "goal_json->>'target_pr'='1769'" in source
    assert "goal_json->>'expected_head_sha'" in source
    assert "status='READY' AND attempts=0 AND not_before <= now()" in source
    assert 'EXECUTE original;' in source


def test_recovery_requires_unpublished_canary():
    source = (ROOT / 'ops/autopilot_light_0355_refence.sql').read_text()
    assert 'LIGHT_0355_RECOVERY_REQUIRES_UNPUBLISHED_CANARY' in source
    assert "task_id='" + CANARY + "'::uuid" in source
    assert 'current_definition IS DISTINCT FROM original' in source
    assert 'EXECUTE replace(original,anchor,patch);' in source
