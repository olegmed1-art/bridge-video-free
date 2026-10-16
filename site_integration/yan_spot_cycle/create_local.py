"""Create two fresh persistent assignments, with no fabricated response history."""
import argparse
import json
from pathlib import Path

from bridge_school_api.local_single_user_lesson import create_assignments
from .build_candidate import build


def create(root: Path, frontend_origin='http://127.0.0.1:8090',
           backend_origin='http://127.0.0.1:8091'):
    root = Path(root).resolve()
    if root.exists():
        raise FileExistsError('local lesson root must be new')
    root.mkdir(parents=True, mode=0o700)
    build(root / 'site')
    result = create_assignments(root / 'private', frontend_origin, backend_origin)
    result['frontend_origin'] = frontend_origin
    result['backend_origin'] = backend_origin
    result['root'] = str(root)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--frontend-origin', default='http://127.0.0.1:8090')
    parser.add_argument('--backend-origin', default='http://127.0.0.1:8091')
    args = parser.parse_args()
    print(json.dumps(create(args.output, args.frontend_origin, args.backend_origin)))
