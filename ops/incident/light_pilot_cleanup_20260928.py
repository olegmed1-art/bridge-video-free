"""Fixed owned-request cleanup; normalize systemd repeated EnvironmentFiles only."""
import hashlib
import json
from ops import light_native_service_controller as controller
from ops import light_native_service_switch as switch

REQUEST = 'c12ef4667a3a620d6db2f876393545333fb772af36196970193fcb7d4a94c314'
PAYLOAD = 'ccd6d6e9723548cf5e8430361b8c4a0a2e455dd1994365098c72343e1d4e5695'


def parse(output, fields):
    result = {}
    for line in output.splitlines():
        key, sep, value = line.partition('=')
        switch.require(sep and key in fields, 'PILOT_HOST_UNIT_FIELDS')
        if key == 'EnvironmentFiles':
            result.setdefault(key, []).append(value)
        else:
            switch.require(key not in result, 'PILOT_HOST_UNIT_FIELDS')
            result[key] = value
    switch.require(set(result) == set(fields), 'PILOT_HOST_UNIT_FIELDS')
    if 'EnvironmentFiles' in result:
        switch.require(len(result['EnvironmentFiles']) == 2, 'PILOT_HOST_ENVIRONMENT_FILES_COUNT')
        result['EnvironmentFiles'] = ' '.join(result['EnvironmentFiles'])
    return result


def reconcile(package, payload, accepted, wheels, credential, token, guard):
    switch.require(accepted == PAYLOAD and hashlib.sha256(payload).hexdigest() == PAYLOAD,
                   'PILOT_CLEANUP_PAYLOAD')
    switch.require(json.loads(payload) == {'request_sha256': REQUEST}, 'PILOT_CLEANUP_REQUEST')
    guard.assert_running()
    request, prior, protected, protected_digest, directory = controller.ledger(REQUEST)
    switch.require(request.value['source'] == '8bbc1d61010ef70c3fca02b5151ac86fce02a144',
                   'PILOT_CLEANUP_SOURCE')
    original = switch.show
    def normalized(unit, fields):
        if unit != controller.plan.PILOT_UNIT or fields != ['ExecStart','Environment','EnvironmentFiles','RuntimeMaxUSec']:
            return original(unit, fields)
        output = switch.command('/usr/bin/systemctl', 'show', unit,
                                *['--property='+key for key in fields])
        return parse(output, fields)
    switch.show = normalized
    try:
        # Validate complete exact command, file paths and hardening before cleanup.
        switch.attest_pilot_execution(request.value['source'], prior,
                                     request.value['duration_seconds'], REQUEST)
        switch.attest_hardening(controller.plan.PILOT_UNIT, pilot=True)
        guard.assert_running()
        result = controller.restore(REQUEST)
        guard.assert_running()
        controller.restored_receipt(request, prior, protected, protected_digest, directory)
        return dict(result, incident='SYSTEMD_REPEATED_ENVIRONMENT_FILES', pilot_resubmitted=False)
    finally:
        switch.show = original
