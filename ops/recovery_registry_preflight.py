"""Read-only preflight for the existing registry writer's two trigger types."""
import json
import os

from ops import native_maintenance_registry_scope as registry
from ops.native_maintenance_bundle import identifier
from ops.native_maintenance_run_guard import API, REPOSITORY, OWNER
from ops.native_maintenance_workflow_pause import require

WORKFLOW_REF = REPOSITORY + '/.github/workflows/recovery-registry-population.yml@refs/heads/main'
PHASE = 'source_before'


def check_source(source, api):
    require(identifier(source, 40) and os.environ.get('GITHUB_SHA') == source
            and os.environ.get('GITHUB_WORKFLOW_SHA') == source
            and os.environ.get('GITHUB_WORKFLOW_REF') == WORKFLOW_REF
            and os.environ.get('GITHUB_REPOSITORY') == REPOSITORY
            and os.environ.get('GITHUB_REF') == 'refs/heads/main'
            and os.environ.get('GITHUB_ACTOR') == OWNER
            and os.environ.get('GITHUB_TRIGGERING_ACTOR') == OWNER
            and os.environ.get('GITHUB_EVENT_NAME') in ('push', 'workflow_dispatch'),
            'REGISTRY_WRITER_CONTEXT')
    value = api.get('/git/ref/heads/main')
    require(value.get('ref') == 'refs/heads/main'
            and value.get('object', {}).get('type') == 'commit'
            and value['object']['sha'] == source, 'REGISTRY_WRITER_MAIN_CHANGED')


def main():
    global PHASE
    import psycopg
    source = os.environ.get('EXPECTED_MAIN')
    api = API(os.environ.get('GH_TOKEN'))
    PHASE = 'source_before'
    check_source(source, api)
    PHASE = 'catalog'
    report = registry.observe(psycopg.connect, os.environ.pop('NATIVE_REGISTRY_DATABASE_URL', ''))
    PHASE = 'source_after'
    check_source(source, api)
    print(json.dumps({**report, 'audit': 'RECOVERY_REGISTRY_PREFLIGHT_PASS',
                      'source_sha': source}, sort_keys=True))


def entrypoint():
    try:
        main()
    except BaseException as exc:
        print(json.dumps(dict(audit='RECOVERY_REGISTRY_PREFLIGHT_REFUSED',
                              phase=registry.PHASE if PHASE == 'catalog' else PHASE,
                              reason=registry.failure_reason(exc), production_mutations=False)))
        raise SystemExit(2) from None


if __name__ == '__main__': entrypoint()
