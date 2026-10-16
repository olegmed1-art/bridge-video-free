"""Read-only validation of a proposed registry credential; no writer cutover."""
import json
import os

from ops import native_maintenance_registry_scope as registry
from ops.native_maintenance_store_runner import source_check
from ops.native_maintenance_workflow_pause import require

PHASE = 'context'


def main():
    global PHASE
    import psycopg
    require(os.environ.get('GITHUB_REPOSITORY') == 'olegmed1-art/bridge-video-free'
            and os.environ.get('GITHUB_REF') == 'refs/heads/main'
            and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
            and os.environ.get('GITHUB_ACTOR') == 'olegmed1-art'
            and os.environ.get('GITHUB_TRIGGERING_ACTOR') == 'olegmed1-art',
            'REGISTRY_PROBE_CONTEXT')
    source = os.environ.get('EXPECTED_MAIN')
    PHASE = 'source_before'
    source_check(source)
    PHASE = 'candidate_observation'
    report = registry.observe(psycopg.connect, os.environ.pop('REGISTRY_CANDIDATE_DATABASE_URL', ''))
    PHASE = 'source_after'
    source_check(source)
    print(json.dumps({**report, 'audit': 'NATIVE_REGISTRY_CREDENTIAL_CANDIDATE_PASS',
                      'source_sha': source, 'credential_route': 'LIGHT_MAINTENANCE_DATABASE_URL',
                      'writer_route_changed': False}, sort_keys=True))


def entrypoint():
    try:
        main()
    except BaseException as exc:
        print(json.dumps(dict(audit='NATIVE_REGISTRY_CREDENTIAL_CANDIDATE_REFUSED',
                              phase=registry.PHASE if PHASE == 'candidate_observation' else PHASE,
                              reason=registry.failure_reason(exc),
                              writer_route_changed=False, production_mutations=False)))
        raise SystemExit(2) from None


if __name__ == '__main__': entrypoint()
