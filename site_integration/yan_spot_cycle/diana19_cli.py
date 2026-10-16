"""Private local CLI transport for offline UI verification; not an HTTP bypass.

Only a local operator can select --root. No browser sends this path. Both CLI
and HTTP use the same staging service and durable response repository.
"""
import argparse
import json
from pathlib import Path
import sys

from bridge_school_api.diana19_staging import DianaStagingService
from bridge_school_api.local_single_user_lesson import read_context


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args()
    private = args.root.resolve()/'private'
    context = read_context(private)
    service = DianaStagingService.from_private(private, context['owner']['provider_subject'])
    message = json.load(sys.stdin)
    operation = message.get('operation')
    if operation == 'catalog' and set(message) == {'operation'}:
        result = service.catalog()
    elif operation == 'view' and set(message) == {'operation', 'lesson_id'}:
        result = service.view(message['lesson_id'])
    elif operation == 'answer' and set(message) == {'operation', 'lesson_id', 'value'}:
        result = service.submit(message['lesson_id'], message['value'])
    else:
        raise ValueError('unknown local staging operation')
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
