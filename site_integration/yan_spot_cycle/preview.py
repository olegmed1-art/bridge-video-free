"""Explicit local Linux/PHP preview, using a newly created synthetic journal."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys

from site_integration.yan_spot_cycle.build_candidate import build, HERE, REPO
from bridge_school_api.local_spot_session import load_store_class


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--spot', choices=['spot.a7c2', 'spot.f3b8'], default='spot.a7c2')
    parser.add_argument('--port', type=int, default=8090)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('use an unprivileged local port')
    output = args.output.resolve()
    if output.exists():
        parser.error('output must be a new local directory')
    output.mkdir(parents=True)
    candidate = output / 'site'
    build(candidate)
    journal = output / 'private-preview.sqlite3'
    load_store_class().create(journal, 'yan-local-preview', preview_exercise_id=args.spot)
    origin = f'http://127.0.0.1:{args.port}'
    env = os.environ.copy()
    env.update(BRIDGE_YAN_LOCAL_PREVIEW='1', BRIDGE_YAN_FIXTURE_READY='1',
        BRIDGE_YAN_CANDIDATE=str(candidate), BRIDGE_YAN_JOURNAL=str(journal),
        BRIDGE_TEST_PYTHON=sys.executable, BRIDGE_YAN_ORIGIN=origin,
        PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE='1')
    print(origin + '/ru/practice/spot-cycle/', flush=True)
    print('Synthetic local preview; no CMS bootstrap or production identity.', flush=True)
    server = subprocess.Popen(['php', '-S', f'127.0.0.1:{args.port}',
        str(HERE / 'tests/local_router.php')], cwd=REPO, env=env)
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        return server.wait()
    except KeyboardInterrupt:
        return 130
    finally:
        signal.signal(signal.SIGTERM, previous)
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


if __name__ == '__main__':
    raise SystemExit(main())
