"""Dedicated local process; never imported by the production entrypoint."""
import argparse
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn

from bridge_school_api.local_single_user_lesson import create_local_app, read_context


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--private-dir', required=True, type=Path)
    args = parser.parse_args()
    context = read_context(args.private_dir)
    uvicorn.run(create_local_app(args.private_dir), host='127.0.0.1',
        port=urlsplit(context['backend_origin']).port, proxy_headers=False,
        access_log=False, log_level='warning')


if __name__ == '__main__':
    main()
