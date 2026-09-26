"""Exact-repository GitHub workflow adapter; defaults to read-only.

This supplies transport, not writer exclusion or authority to change production.
No generic HTTP method, arbitrary URL, token printing, retry or redirect API.
"""
import json
import os
import re
import urllib.request

from ops.native_maintenance_workflow_pause import Refused, digest, require, unique, validate_plan

REPOSITORY = 'olegmed1-art/bridge-video-free'
BASE = 'https://api.github.com/repos/' + REPOSITORY
MAX_RESPONSE = 2 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_):
        raise Refused('WORKFLOW_API_REDIRECT')


class Transport:
    def __init__(self, token):
        require(type(token) is str and 0 < len(token) <= 4096
                and not any(c.isspace() for c in token), 'WORKFLOW_API_TOKEN_REQUIRED')
        self._token = token
        self._opener = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))

    def request(self, method, suffix):
        get = (suffix == '/git/ref/heads/main'
               or re.fullmatch(r'/actions/workflows/[1-9][0-9]{0,19}', suffix)
               or re.fullmatch(r'/actions/workflows\?per_page=100&page=[1-9][0-9]?', suffix))
        put = re.fullmatch(r'/actions/workflows/[1-9][0-9]{0,19}/(?:enable|disable)', suffix)
        require((method == 'GET' and get) or (method == 'PUT' and put), 'WORKFLOW_API_PATH_REFUSED')
        url = BASE + suffix
        request = urllib.request.Request(url, method=method, headers={
            'Authorization': 'Bearer ' + self._token,
            'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'native-maintenance-workflow-api', 'Cache-Control': 'no-cache',
        })
        try:
            # Timeout is per socket operation, not a total controller deadline.
            # The production caller must retain the independent lifetime bound.
            with self._opener.open(request, timeout=4) as response:
                require(response.url == url and response.status == (200 if method == 'GET' else 204),
                        'WORKFLOW_API_RESPONSE_REFUSED')
                require(not response.headers.get('Link') or suffix.startswith('/actions/workflows?'),
                        'WORKFLOW_API_UNEXPECTED_PAGINATION')
                raw = response.read(MAX_RESPONSE + 1)
                require(len(raw) <= MAX_RESPONSE, 'WORKFLOW_API_RESPONSE_SIZE')
            if method == 'PUT':
                require(not raw, 'WORKFLOW_API_NONEMPTY_WRITE_RESPONSE')
                return None
            return json.loads(raw, object_pairs_hook=unique)
        except Exception:
            # HTTP error bodies/URLs may contain private context. Preserve no raw
            # exception chain, response text or token in this public boundary.
            raise Refused('WORKFLOW_API_REQUEST_FAILED') from None


def source_matches(transport, source):
    require(type(source) is str and re.fullmatch('[0-9a-f]{40}', source), 'WORKFLOW_SOURCE_INVALID')
    ref = transport.request('GET', '/git/ref/heads/main')
    require(type(ref) is dict and ref.get('ref') == 'refs/heads/main'
            and ref.get('object', {}).get('type') == 'commit'
            and ref['object'].get('sha') == source, 'WORKFLOW_SOURCE_CHANGED')


def observation(row):
    require(type(row) is dict and type(row.get('id')) is int and row['id'] > 0,
            'WORKFLOW_IDENTITY_INVALID')
    require(row.get('url') == BASE + '/actions/workflows/' + str(row['id']), 'WORKFLOW_REPOSITORY_MISMATCH')
    result = {key: row.get(key) for key in ('id', 'path', 'state', 'updated_at')}
    validate_plan({'version': 1, 'repository': REPOSITORY, 'source': '0' * 40, 'workflows': [result]})
    return result


class WorkflowAPI:
    """Adapter accepted by WorkflowPause, with no mutation permission by default.

The caller's mutation_guard.assert_dispatch(digest, action, id) must enforce
the independently approved live operation, not an environment flag. This class
does not implement that guard. A successful HTTP write is still followed by the
pause library's observed-state journal; lost responses are never retried here.
"""
    def __init__(self, token, plan, approved_digest, *, mutation_guard=None):
        validate_plan(plan)
        require(digest(plan) == approved_digest, 'WORKFLOW_PLAN_DIGEST_MISMATCH')
        self.plan = json.loads(json.dumps(plan))
        self.plan_digest = approved_digest
        self.workflows = {r['id']: r for r in self.plan['workflows']}
        self.transport = Transport(token)
        self.mutation_guard = mutation_guard
        self.failed = False

    def _row(self, workflow_id):
        require(not self.failed, 'WORKFLOW_API_ALREADY_FAILED')
        require(type(workflow_id) is int and workflow_id in self.workflows, 'WORKFLOW_OUTSIDE_PLAN')
        return self.workflows[workflow_id]

    def get_workflow(self, workflow_id):
        try:
            row = self._row(workflow_id)
            actual = observation(self.transport.request('GET', '/actions/workflows/' + str(workflow_id)))
            require(actual['id'] == workflow_id and actual['path'] == row['path'], 'WORKFLOW_IDENTITY_CHANGED')
            return actual
        except BaseException:
            self.failed = True
            raise

    def _change(self, workflow_id, action):
        try:
            self._row(workflow_id)
            require(callable(getattr(self.mutation_guard, 'assert_dispatch', None)), 'WORKFLOW_API_READ_ONLY')
            self.mutation_guard.assert_dispatch(self.plan_digest, action, workflow_id)
            source_matches(self.transport, self.plan['source'])
            self.mutation_guard.assert_dispatch(self.plan_digest, action, workflow_id)
            self.transport.request('PUT', '/actions/workflows/' + str(workflow_id) + '/' + action)
        except BaseException:
            self.failed = True
            raise

    def disable_workflow(self, workflow_id):
        self._change(workflow_id, 'disable')

    def enable_workflow(self, workflow_id):
        self._change(workflow_id, 'enable')


def inventory(transport, source):
    """Complete bounded registry observation, never a writer allowlist.

Construct pagination URLs ourselves; never follow a supplied Link to another
host. Stable total and unique IDs detect partial/overlapping pages, but do not
claim an atomic server snapshot. Unknown/deleted workflow states are preserved
as observations and are not accepted by WorkflowPause's stricter plan schema.
"""
    source_matches(transport, source)
    rows, total = [], None
    for page in range(1, 11):
        result = transport.request('GET', f'/actions/workflows?per_page=100&page={page}')
        require(type(result) is dict and type(result.get('total_count')) is int
                and 0 < result['total_count'] <= 1000 and type(result.get('workflows')) is list,
                'WORKFLOW_INVENTORY_SHAPE')
        require(total is None or total == result['total_count'], 'WORKFLOW_INVENTORY_CHANGED')
        total = result['total_count']
        require(len(result['workflows']) == min(100, total - len(rows)), 'WORKFLOW_INVENTORY_PARTIAL')
        for item in result['workflows']:
            require(type(item) is dict and type(item.get('id')) is int and item['id'] > 0
                    and item.get('url') == BASE + '/actions/workflows/' + str(item['id'])
                    and all(type(item.get(key)) is str and 0 < len(item[key]) <= 512
                            for key in ('path', 'state', 'updated_at')), 'WORKFLOW_INVENTORY_IDENTITY')
            rows.append({key: item[key] for key in ('id', 'path', 'state', 'updated_at')})
        require(len({r['id'] for r in rows}) == len(rows), 'WORKFLOW_INVENTORY_DUPLICATE')
        if len(rows) == total:
            break
    require(len(rows) == total, 'WORKFLOW_INVENTORY_INCOMPLETE')
    source_matches(transport, source)
    return {'audit': 'WORKFLOW_REGISTRY_OBSERVED', 'source': source, 'workflow_count': total,
            'workflows': sorted(rows, key=lambda r: r['id']),
            'writer_exclusion': 'NOT_ESTABLISHED', 'production_mutations': False}


def main():
    require(os.environ.get('GITHUB_REPOSITORY') == REPOSITORY
            and os.environ.get('GITHUB_REF') == 'refs/heads/main', 'WORKFLOW_INVENTORY_CONTEXT')
    report = inventory(Transport(os.environ.get('GH_WORKFLOW_READ_TOKEN')), os.environ.get('GITHUB_SHA'))
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        print(json.dumps({'audit': 'WORKFLOW_REGISTRY_REFUSED', 'production_mutations': False}))
        raise SystemExit(2)
