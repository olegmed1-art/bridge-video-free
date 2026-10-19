"""Separate, unconfigured anonymous adapter candidate. Never starts a server."""
import argparse
import json
from pathlib import Path
import shutil

from .build_application_candidate import build as build_application
from .build_candidate import HERE


def build(root: Path):
    root = Path(root).resolve()
    if root.exists():
        raise FileExistsError('anonymous candidate root must be new')
    root.mkdir(parents=True)
    result = build_application(root/'site')
    server = root/'server-config'
    server.mkdir()
    shutil.copyfile(HERE/'anonymous_config.example.py', server/'anonymous_config.example.py')
    manifest = {'schema': 'anonymous-practice-adapter-candidate-v1',
        'status': 'UNCONFIGURED_FAIL_CLOSED', 'mode': 'ANONYMOUS_PRACTICE',
        'known_session_contract_installed': False, 'default_http_status': 503,
        'accounts_created': 0, 'keys_generated': 0, 'journals_created': 0,
        'servers_started': 0, 'site_candidate': result,
        'backend_factory': 'bridge_school_api.anonymous_practice_adapter:create_anonymous_practice_app',
        'requires_existing_repository': True, 'production_activated': False,
        'unresolved': 'Existing native PHP session/CSRF bridge and actual CMS bootstrap/view paths'}
    (root/'ANONYMOUS_ADAPTER.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps(build(parser.parse_args().output)))
