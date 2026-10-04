"""Pure fail-closed policy. SSH_READY is not a provider Running assertion."""
import datetime as dt
import math
import re

REPO = 'example-owner/example-repository'
BRANCH = 'review/synthetic-power-contract'
SHA = '1111111111111111111111111111111111111111'
WORKFLOW = 42
PATH = '.github/workflows/synthetic-power-probe.yml'
HOST = 'synthetic-compute'
NAMES = {'contract', 'probe', 'oracle-probe', 'trial-start', 'manual-console-trial', 'trial-stop'}


class Refused(Exception):
    pass


def require(test, reason):
    if not test:
        raise Refused(reason)


def finite(value):
    return type(value) in (float, int) and math.isfinite(value)


def epoch(text):
    require(isinstance(text, str) and text.endswith('Z'), 'INVALID_TIMESTAMP')
    try:
        value = dt.datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp()
    except (ValueError, OverflowError):
        raise Refused('INVALID_TIMESTAMP')
    require(finite(value), 'INVALID_TIMESTAMP')
    return value


def validate_run(run, now, earliest):
    require(isinstance(run, dict), 'RUN_SCHEMA')
    require(type(run.get('id')) is int and run['id'] > 0, 'RUN_ID')
    require(run.get('repository', {}).get('full_name') == REPO, 'REPOSITORY')
    require(run.get('head_repository', {}).get('full_name') == REPO, 'HEAD_REPOSITORY')
    require(run.get('head_branch') == BRANCH and run.get('head_sha') == SHA, 'BRANCH_OR_SHA')
    require(run.get('workflow_id') == WORKFLOW and run.get('path') == PATH, 'WORKFLOW')
    require(run.get('event') == 'workflow_dispatch' and run.get('run_attempt') == 1, 'EVENT_OR_RERUN')
    require(run.get('html_url') == f'https://github.com/{REPO}/actions/runs/{run["id"]}', 'RUN_URL')
    created = epoch(run.get('created_at'))
    require(earliest <= created <= now and now - created < 120, 'RUN_TIME')
    require(run.get('status') in ('queued', 'in_progress', 'requested', 'waiting', 'pending'), 'RUN_TERMINAL')
    require(run.get('conclusion') is None, 'RUN_CONCLUSION')
    return created


def select_run(page, earliest, now, bound=None):
    require(isinstance(page, dict) and isinstance(page.get('workflow_runs'), list), 'RUN_LIST_SCHEMA')
    rows = page['workflow_runs']
    require(type(page.get('total_count')) is int and page['total_count'] == len(rows) and len(rows) < 100, 'RUN_PAGINATION_OR_TRUNCATION')
    fresh = [r for r in rows if epoch(r.get('created_at')) >= earliest]
    require(len({r.get('id') for r in fresh}) == len(fresh), 'DUPLICATE_RUN_ID')
    require(len(fresh) <= 1, 'AMBIGUOUS_RUN')
    if not fresh:
        require(bound is None, 'BOUND_RUN_DISAPPEARED')
        return None
    run = fresh[0]
    t0 = validate_run(run, now, earliest)
    if bound is not None:
        require(run['id'] == bound['id'] and t0 == bound['t0'], 'BOUND_RUN_CHANGED')
    return {'id': run['id'], 't0': t0, 'url': run['html_url']}


def validate_jobs(page, run_id):
    require(isinstance(page, dict) and isinstance(page.get('jobs'), list), 'JOBS_SCHEMA')
    jobs = page['jobs']
    require(page.get('total_count') == len(jobs) and len(jobs) <= 6, 'JOB_PAGINATION_OR_COUNT')
    require(all(isinstance(j, dict) for j in jobs), 'JOB_SCHEMA')
    names = [j.get('name') for j in jobs]
    require(len(set(names)) == len(names) and set(names) <= NAMES, 'UNKNOWN_OR_DUPLICATE_JOB')
    for j in jobs:
        require(j.get('run_id') == run_id and type(j.get('id')) is int and j['id'] > 0, 'JOB_IDENTITY')
    if set(names) != NAMES:
        return None  # GitHub has not materialized all jobs yet; never admit.
    by = {j['name']: j for j in jobs}
    for name in ('probe', 'oracle-probe', 'trial-start', 'trial-stop'):
        j = by[name]
        require(j.get('status') == 'completed' and j.get('conclusion') == 'skipped', 'OTHER_JOB_NOT_SKIPPED')
    contract = by['contract']
    if contract.get('status') != 'completed':
        return None
    require(contract.get('conclusion') == 'success', 'CONTRACT_FAILED')
    job = by['manual-console-trial']
    require(job.get('status') != 'completed' and job.get('conclusion') is None, 'MANUAL_JOB_TERMINAL')
    if job.get('status') != 'in_progress':
        return None
    require(job.get('html_url') == f'https://github.com/{REPO}/actions/runs/{run_id}/job/{job["id"]}', 'JOB_URL')
    return {'id': job['id'], 'url': job['html_url']}


def validate_boot(sample, t0, previous=None):
    require(isinstance(sample, dict), 'SSH_SCHEMA')
    require(set(sample) == {'hostname', 'username', 'uid', 'root_uid', 'boot_id', 'uptime', 'guest_epoch', 'sent', 'received'}, 'SSH_FIELDS')
    require(sample['hostname'] == HOST and sample['username'] == 'ubuntu' and type(sample['uid']) is int and sample['uid'] > 0 and sample['root_uid'] == 0, 'SSH_IDENTITY')
    require(isinstance(sample['boot_id'], str) and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', sample['boot_id']), 'BOOT_ID')
    require(all(finite(sample[k]) for k in ('uptime', 'guest_epoch', 'sent', 'received')), 'SSH_TIME_TYPE')
    lo, hi, up = sample['sent'], sample['received'], sample['uptime']
    require(t0 <= lo <= hi and hi-lo <= 6 and 0 < up < 120, 'SSH_RTT_OR_UPTIME')
    require(lo-2 <= sample['guest_epoch'] <= hi+2, 'GUEST_CLOCK_SKEW')
    # Guest clock cannot make an old boot appear fresh. /proc/uptime is mapped
    # to a conservative local interval, widened 50ms for reported precision.
    boot_lo, boot_hi = lo-up-0.05, hi-up+0.05
    require(boot_lo >= t0 and boot_hi <= hi, 'OLD_OR_UNCERTAIN_BOOT')
    require(hi-t0 < 120, 'ADMISSION_EXPIRED')
    if previous is not None:
        require(previous['boot_id'] == sample['boot_id'], 'BOOT_CHANGED')
        require(lo >= previous['received'] and up > previous['uptime'], 'UPTIME_NOT_INCREASING')
        delta = up-previous['uptime']
        require(max(0,lo-previous['received']-0.1) <= delta <= hi-previous['sent']+0.1, 'UPTIME_INCONSISTENT')
    return sample
