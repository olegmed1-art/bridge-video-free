"""Read-only workflow inventory, never a maintenance lock or permission grant.

Every workflow remains unreviewed. Credential-name matches prioritize manual
review; absence of a match never proves absence of write capability.
"""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

import yaml

SHA = re.compile(r'[0-9a-f]{40}')
SECRET = re.compile(r"\bsecrets(?:\.([A-Za-z_][A-Za-z_0-9]*)|\[\s*['\"]([A-Za-z_][A-Za-z_0-9]*)['\"]\s*\])")
PRIORITY = re.compile(r'NEON|DATABASE|POSTGRES|ORACLE|OCI|SSH', re.I)


class InventoryError(ValueError):
    pass


class UniqueLoader(yaml.BaseLoader):
    """Preserve GitHub's `on` key and literal conditions; refuse duplicate keys."""
    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise InventoryError('DUPLICATE_OR_COMPLEX_YAML_KEY')
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def concurrency(value):
    if value is None:
        return None
    if isinstance(value, str):
        return {'group': value, 'cancel_in_progress': 'false (default)'}
    if not isinstance(value, dict):
        raise InventoryError('INVALID_CONCURRENCY')
    return {'group': value.get('group'),
            'cancel_in_progress': value.get('cancel-in-progress', 'false (default)')}


def workflow_record(path, source):
    if len(source.encode()) > 2 * 1024 * 1024:
        raise InventoryError('WORKFLOW_TOO_LARGE')
    data = yaml.load(source, Loader=UniqueLoader)
    if not isinstance(data, dict) or not isinstance(data.get('jobs'), dict):
        raise InventoryError('WORKFLOW_MAPPING_REQUIRED')
    names = sorted({a or b for a, b in SECRET.findall(source)})
    jobs = []
    for name, job in sorted(data['jobs'].items()):
        if not isinstance(job, dict):
            raise InventoryError('JOB_MAPPING_REQUIRED')
        jobs.append({'id': name, 'if': job.get('if'),
                     'environment': job.get('environment'),
                     'concurrency': concurrency(job.get('concurrency')),
                     'reusable_workflow': job.get('uses'),
                     'inherits_secrets': job.get('secrets') == 'inherit'})
    # Scan lexical references, including comments: conservative hints only.
    # Unresolved/dynamic references remain visible without pretending to parse
    # the GitHub expression language or a downstream action's capabilities.
    residual = SECRET.sub('', source)
    return {'path': path, 'sha256': hashlib.sha256(source.encode()).hexdigest(),
            'review': 'UNREVIEWED', 'secret_names': names,
            'priority_secret_names': [n for n in names if PRIORITY.search(n)],
            'opaque_secret_reference': bool(re.search(r'\bsecrets\b', residual)),
            'events': data.get('on'), 'concurrency': concurrency(data.get('concurrency')),
            'jobs': jobs}


def inventory(sources, revision, tree):
    if not SHA.fullmatch(revision) or not SHA.fullmatch(tree):
        raise InventoryError('IMMUTABLE_SOURCE_REQUIRED')
    if not sources:
        raise InventoryError('EMPTY_WORKFLOW_INVENTORY')
    records = [workflow_record(path, source) for path, source in sorted(sources.items())]
    return {'schema': 'NATIVE_PERMISSION_WRITER_INVENTORY_V1',
            'revision': revision, 'source_tree': tree,
            'scope': 'committed workflow files only; capability closure unresolved',
            'maintenance_exclusion': 'NOT_ESTABLISHED',
            'external_channels': ['Neon console and connected owner tools',
                                  'local owner scripts and other operators',
                                  'server processes and credential stores',
                                  'queued/running runs at older or other refs',
                                  'managed-provider administrative access'],
            'workflow_count': len(records),
            'priority_workflow_count': sum(bool(r['priority_secret_names']) for r in records),
            'workflows': records}


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True,
                          capture_output=True, timeout=30).stdout


def from_repository(repo, revision):
    if not SHA.fullmatch(revision):
        raise InventoryError('IMMUTABLE_SOURCE_REQUIRED')
    commit = git(repo, 'rev-parse', revision + '^{commit}').decode().strip()
    if commit != revision:
        raise InventoryError('EXACT_COMMIT_REQUIRED')
    tree = git(repo, 'rev-parse', revision + '^{tree}').decode().strip()
    entries = git(repo, 'ls-tree', '-rz', '--full-tree', revision, '--', '.github/workflows')
    sources = {}
    for entry in entries.split(b'\0'):
        if not entry:
            continue
        metadata, path_bytes = entry.split(b'\t', 1)
        mode, kind, blob = metadata.decode().split()
        path = path_bytes.decode()
        if not path.endswith(('.yml', '.yaml')):
            continue
        if mode not in ('100644', '100755') or kind != 'blob':
            raise InventoryError('NON_REGULAR_WORKFLOW')
        sources[path] = git(repo, 'cat-file', 'blob', blob).decode()
    return inventory(sources, revision, tree)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path('.'))
    parser.add_argument('--revision', required=True, help='Exact 40-character commit SHA')
    args = parser.parse_args()
    print(json.dumps(from_repository(args.repo, args.revision), sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
