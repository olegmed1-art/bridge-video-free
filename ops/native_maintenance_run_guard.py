"""Authenticated run/job observations; NOT a complete DB maintenance guard."""
import base64
import hashlib
import json
import os
import re
import time
import urllib.request

REPOSITORY = 'olegmed1-art/bridge-video-free'
OWNER = 'olegmed1-art'
OWNER_ID = 315099490
WORKFLOW = '.github/workflows/native-maintenance-window.yml'
WORKFLOW_SHA256 = 'a7b90723e717131bfff45c0cff4eacd116db65e563dbe6be884e949a4b5d557b'
JOB = 'native-maintenance-window'
MAX_RESPONSE = 2 * 1024 * 1024


class Refused(RuntimeError):
    pass


def check(value, code):
    if not value:
        raise Refused(code)


def unique(pairs):
    result = {}
    for key, value in pairs:
        check(key not in result, 'DUPLICATE_API_KEY')
        result[key] = value
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Refused('API_REDIRECT')


class API:
    def __init__(self, token):
        check(isinstance(token, str) and token, 'API_TOKEN_REQUIRED')
        self._token = token
        self._opener = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))

    def get(self, suffix):
        check(suffix.startswith('/') and '\n' not in suffix and '\r' not in suffix, 'API_PATH_INVALID')
        url = 'https://api.github.com/repos/' + REPOSITORY + suffix
        request = urllib.request.Request(url, headers={
            'Authorization': 'Bearer ' + self._token,
            'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'native-maintenance-run-binding', 'Cache-Control': 'no-cache',
        })
        with self._opener.open(request, timeout=4) as response:
            check(response.status == 200 and response.url == url and not response.headers.get('Link'),
                  'API_RESPONSE_INCOMPLETE')
            raw = response.read(MAX_RESPONSE + 1)
        check(len(raw) <= MAX_RESPONSE, 'API_RESPONSE_SIZE')
        return json.loads(raw, object_pairs_hook=unique)


class RunBinding:
    def __init__(self, source, run_id, attempt, api, *, seconds=60):
        check(isinstance(source, str) and re.fullmatch('[0-9a-f]{40}', source), 'SOURCE_INVALID')
        check(type(run_id) is int and run_id > 0 and type(attempt) is int and attempt > 0,
              'RUN_ID_INVALID')
        check(type(seconds) is int and 1 <= seconds <= 60, 'DURATION_INVALID')
        self.source, self.run_id, self.attempt = source, run_id, attempt
        self.api = api
        self.deadline = time.monotonic() + seconds
        self.job_id = None
        self.failed = False

    def _run(self):
        run = self.api.get('/actions/runs/' + str(self.run_id))
        check(type(run) is dict and type(run.get('id')) is int and run['id'] == self.run_id
              and type(run.get('run_attempt')) is int and run['run_attempt'] == self.attempt,
              'RUN_ATTEMPT_CHANGED')
        check(run.get('head_sha') == self.source and run.get('path') == WORKFLOW
              and run.get('head_branch') == 'main' and run.get('event') in ('push', 'workflow_dispatch')
              and run.get('repository', {}).get('full_name') == REPOSITORY
              and run.get('head_repository', {}).get('full_name') == REPOSITORY, 'RUN_SOURCE_CHANGED')
        for principal in ('actor', 'triggering_actor'):
            actor = run.get(principal, {})
            check(actor.get('login') == OWNER and type(actor.get('id')) is int and actor['id'] == OWNER_ID,
                  'RUN_PRINCIPAL_CHANGED')
        check(run.get('status') == 'in_progress' and 'conclusion' in run and run['conclusion'] is None,
              'RUN_NOT_RUNNING')

    def assert_running(self):
        check(not self.failed, 'RUN_BINDING_ALREADY_FAILED')
        try:
            check(time.monotonic() < self.deadline, 'RUN_BINDING_EXPIRED')
            self._run()
            file = self.api.get('/contents/' + WORKFLOW + '?ref=' + self.source)
            check(type(file) is dict and file.get('type') == 'file' and file.get('path') == WORKFLOW
                  and file.get('encoding') == 'base64' and type(file.get('size')) is int
                  and 0 < file['size'] <= 32768, 'WORKFLOW_BLOB_INVALID')
            raw = base64.b64decode(file['content'].replace('\n', ''), validate=True)
            check(len(raw) == file['size'] and hashlib.sha256(raw).hexdigest() == WORKFLOW_SHA256,
                  'WORKFLOW_CONTRACT_CHANGED')
            main = self.api.get('/git/ref/heads/main')
            check(main.get('ref') == 'refs/heads/main' and main.get('object', {}).get('type') == 'commit'
                  and main['object']['sha'] == self.source, 'MAIN_CHANGED')
            jobs = self.api.get('/actions/runs/' + str(self.run_id) + '/attempts/' +
                                str(self.attempt) + '/jobs?per_page=100')
            # The pinned workflow has precisely one job, no matrix or pagination.
            check(type(jobs.get('total_count')) is int and jobs['total_count'] == 1
                  and type(jobs.get('jobs')) is list and len(jobs['jobs']) == 1, 'JOB_SET_CHANGED')
            job = jobs['jobs'][0]
            check(type(job.get('id')) is int and job['id'] > 0 and job.get('name') == JOB
                  and type(job.get('run_id')) is int and job['run_id'] == self.run_id
                  and type(job.get('run_attempt')) is int and job['run_attempt'] == self.attempt
                  and job.get('head_sha') == self.source, 'JOB_IDENTITY_CHANGED')
            check(job.get('status') == 'in_progress' and 'conclusion' in job and job['conclusion'] is None,
                  'JOB_NOT_RUNNING')
            check(self.job_id is None or self.job_id == job['id'], 'JOB_ID_CHANGED')
            self._run()
            check(time.monotonic() < self.deadline, 'RUN_BINDING_EXPIRED')
            # First ID comes from the unique job in the authenticated, exact
            # workflow/attempt. It is an identity observation, not DB approval.
            self.job_id = job['id']
        except BaseException:
            self.failed = True
            raise


def main():
    env = os.environ
    source = env.get('GITHUB_SHA')
    check(env.get('GITHUB_REPOSITORY') == REPOSITORY and env.get('GITHUB_REF') == 'refs/heads/main'
          and env.get('GITHUB_WORKFLOW_REF') == REPOSITORY + '/' + WORKFLOW + '@refs/heads/main'
          and env.get('GITHUB_WORKFLOW_SHA') == source and env.get('GITHUB_JOB') == 'maintenance-window',
          'LOCAL_JOB_CONTEXT_INVALID')
    check(re.fullmatch('[1-9][0-9]{0,19}', env.get('GITHUB_RUN_ID', '')) and
          re.fullmatch('[1-9][0-9]{0,5}', env.get('GITHUB_RUN_ATTEMPT', '')), 'LOCAL_RUN_ID_INVALID')
    guard = RunBinding(source, int(env['GITHUB_RUN_ID']), int(env['GITHUB_RUN_ATTEMPT']),
                       API(env.get('GH_RUN_GUARD_TOKEN')))
    guard.assert_running()
    guard.assert_running()
    print(json.dumps({'audit': 'NATIVE_RUN_BINDING_PASS', 'source_sha': source,
                      'run_id': guard.run_id, 'attempt': guard.attempt, 'job_id': guard.job_id,
                      'workflow_group': 'oracle-instance-workload-mutation',
                      'job_group': 'oracle-light-backup-mutation', 'production_mutations': False}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        print(json.dumps({'audit': 'NATIVE_RUN_BINDING_REFUSED'}))
        raise SystemExit(2)
