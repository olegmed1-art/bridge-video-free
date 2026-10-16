"""Run the persisted single-user lesson. Does not create or replace journals."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit

from bridge_school_api.local_single_user_lesson import read_context
from .build_candidate import HERE, REPO


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    context = read_context(root / 'private')
    if os.environ.get('VERCEL_ENV') or not (root / 'site/index.php').is_file():
        raise ValueError('explicit local context required')
    env = os.environ.copy()
    env.update(PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE='1',
        BRIDGE_LOCAL_SINGLE_USER='1', BRIDGE_YAN_CANDIDATE=str(root / 'site'),
        BRIDGE_SINGLE_USER_FRONTEND_ORIGIN=context['frontend_origin'],
        BRIDGE_SINGLE_USER_BACKEND_ORIGIN=context['backend_origin'])
    children = []
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        children.append(subprocess.Popen([sys.executable, '-m',
            'site_integration.yan_spot_cycle.local_backend', '--private-dir', str(root / 'private')],
            cwd=REPO, env=env))
        port = urlsplit(context['frontend_origin']).port
        children.append(subprocess.Popen(['php', '-S', f'127.0.0.1:{port}',
            str(HERE / 'local_router.php')], cwd=REPO, env=env))
        print(context['frontend_origin'] + '/ru/practice/spot-cycle/', flush=True)
        print('LOCAL_SINGLE_USER: existing private assignments, no website login.', flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(0.2)
        raise RuntimeError('a local lesson process stopped')
    except KeyboardInterrupt:
        return 130
    finally:
        signal.signal(signal.SIGTERM, previous)
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)


if __name__ == '__main__':
    raise SystemExit(main())
