"""Disabled integration draft. No network/subprocess calls on import.

Uses only PRECONFIGURED gh, OCI and pinned SSH identity. No credential
discovery, installation, retries or alternate targets. Run only after separate
authorization and review. Runtime execution guard remains unconditionally off.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import time
import datetime
import base64
import hashlib

from evidence_contract import (ACTIVE, INSTANCE, actions_summary, draft_report,
                               oci_summary, self_run_identity, resident_summary)

REPO = 'olegmed1-art/bridge-video-free'
HOST = '158.180.47.161'
FINGERPRINT = 'SHA256:NXmGcng3fzof9b6Hs5Xgh4yYnzxGyVwa/EcfOxu0WPk'
EXECUTION_ENABLED = False


def invoke(args, body=None):
    return subprocess.run(args, input=body, capture_output=True, text=True,
                          timeout=105 if args[0] == 'timeout' else 35, check=True).stdout


def github_snapshot(call, run_id=None, exact_sha=None):
    identity = None
    if run_id is not None:
        detail = json.loads(call(['gh', 'api', 'repos/' + REPO + '/actions/runs/' + str(run_id)]))
        identity = self_run_identity(detail, run_id, exact_sha)
    result = {}
    for status in ACTIVE:
        result[status] = json.loads(call([
            'gh', 'api', 'repos/' + REPO + '/actions/runs?status=' + status + '&per_page=100']))
    return actions_summary(result, identity)


def execute(exact_sha, key, known_hosts, call=invoke, run_id=None):
    """Injection seam for mocks, not a public execution authorization."""
    receipt = draft_report()
    receipt['exact_sha'] = exact_sha if re.fullmatch('[0-9a-f]{40}', exact_sha) else 'UNKNOWN'
    started = time.monotonic()
    started_at = datetime.datetime.now(datetime.timezone.utc)
    stage = 'input_validation'
    try:
        if receipt['exact_sha'] == 'UNKNOWN':
            raise ValueError()
        # Explicit, caller-configured files only. Never scan credential stores.
        for path in (key, known_hosts):
            if not Path(path).is_absolute() or not Path(path).is_file():
                raise ValueError()
        if os.stat(key).st_mode & 0o077:
            raise ValueError()
        stage = 'host_identity'
        fingerprint = call(['ssh-keygen', '-lf', known_hosts, '-E', 'sha256'])
        lines = fingerprint.splitlines()
        if len(lines) != 1 or len(lines[0].split()) < 2 or lines[0].split()[1] != FINGERPRINT:
            raise ValueError()
        stage = 'exact_main'
        def main():
            return json.loads(call(['gh', 'api', 'repos/' + REPO + '/git/ref/heads/main']))['object']['sha']
        if main() != exact_sha:
            raise ValueError()
        stage = 'actions_before'
        receipt['actions_before'] = github_snapshot(call, run_id, exact_sha)
        if receipt['actions_before'] != 'NO_ACTIVE_OBSERVED':
            raise ValueError()
        stage = 'oci_before'
        before = oci_summary(json.loads(call(['oci', 'compute', 'instance', 'get', '--instance-id', INSTANCE])))
        receipt['oci_before'] = before
        if before['vm_state'] != 'RUNNING':
            raise ValueError()
        if time.monotonic() - started > 180:
            raise TimeoutError()
        stage = 'resident_probe'
        # Obtain observer bytes from the immutable attested commit, never
        # execute the mutable local file with a different claimed SHA.
        source_record = json.loads(call(['gh', 'api', 'repos/' + REPO +
            '/contents/witness-draft/witness.py?ref=' + exact_sha]))
        if source_record.get('encoding') != 'base64' or source_record.get('type') != 'file':
            raise ValueError()
        if source_record.get('path') != 'witness-draft/witness.py':
            raise ValueError()
        encoded = source_record['content']
        if type(encoded) is not str or len(encoded) > 100000:
            raise ValueError()
        data = base64.b64decode(encoded.replace('\n', ''), validate=True)
        blob_sha = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        if blob_sha != source_record.get('sha') or len(data) != source_record.get('size'):
            raise ValueError()
        if data != Path(__file__).with_name('witness.py').read_bytes():
            raise ValueError()
        source = data.decode('utf-8')
        receipt['observer_blob_sha'] = blob_sha
        # __name__ is not __main__; witness.py's disabled CLI is not invoked.
        # The outer EXECUTION_ENABLED gate must remain false until review/GO.
        body = 'ns={"__name__":"bounded_observer"}\nexec(' + repr(source) + ',ns)\nprint(ns["json"].dumps(ns["collect"]()))\n'
        raw = call(['timeout', '90s', 'ssh', '-i', key, '-o', 'BatchMode=yes',
                    '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
                    '-o', 'UserKnownHostsFile=' + known_hosts, '-o', 'ConnectTimeout=10',
                    '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=2',
                    'ubuntu@' + HOST,
                    'sudo -n /usr/bin/timeout --signal=TERM --kill-after=5s 75s /usr/bin/python3 -B -'], body)
        observed = resident_summary(raw, started_at, datetime.datetime.now(datetime.timezone.utc))
        receipt['resident_response_received'] = True
        stage = 'postcheck'
        after = oci_summary(json.loads(call(['oci', 'compute', 'instance', 'get', '--instance-id', INSTANCE])))
        receipt['oci_after'] = after
        receipt['actions_after'] = github_snapshot(call, run_id, exact_sha)
        receipt['source_stable'] = main() == exact_sha
        snapshots_equal = (before == after and receipt['actions_after'] == 'NO_ACTIVE_OBSERVED'
                                    and receipt['source_stable'] and time.monotonic() - started <= 240)
        receipt['window_stable'] = 'UNKNOWN'
        receipt['sampled_metadata_equal'] = snapshots_equal
        if not snapshots_equal:
            raise ValueError()
        receipt['resident_observation'] = observed
        receipt['inode_match'] = observed['inode_match']
        receipt['BLOCKER'] = 'effective_worker_target_and_conflict_inventory_unproved'
    except Exception:
        # Fixed stage enum only; never exception, argv, raw stdout or stderr.
        receipt['failed_stage'] = stage
    receipt['duration_seconds'] = round(time.monotonic() - started, 3)
    return receipt


def main():
    if not EXECUTION_ENABLED:
        raise SystemExit('DRAFT_DISABLED: no server execution authorized')
    raise SystemExit('Integration requires reviewed entry point and exact GO')


if __name__ == '__main__':
    main()
