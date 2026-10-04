#!/usr/bin/env python3
"""Read storage metadata for one stopped instance; never submit VPC actions."""
from __future__ import annotations
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
try:
    from .ibm_vpc_power import BoundedClientError, IAM_URL, parse_instance, _request_headers, _instance_url
    from .ibm_vpc_oracle_probe import verify_identity, INSTANCE_ID, INSTANCE_NAME
except ImportError:
    from ibm_vpc_power import BoundedClientError, IAM_URL, parse_instance, _request_headers, _instance_url
    from ibm_vpc_oracle_probe import verify_identity, INSTANCE_ID, INSTANCE_NAME

SAFE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,255}\Z')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BoundedClientError('storage_redirect_refused')

def transport_json(request):
    vpc = request.method == 'GET' and request.data is None and request.full_url == _instance_url('eu-de', INSTANCE_ID)
    iam = request.method == 'POST' and request.full_url == IAM_URL and isinstance(request.data, bytes)
    if not (vpc or iam):
        raise BoundedClientError('storage_endpoint_refused')
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            raw = response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        raise BoundedClientError('storage_http_' + str(exc.code)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BoundedClientError('storage_request_failed') from exc
    if len(raw) > 1024 * 1024:
        raise BoundedClientError('storage_response_too_large')
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BoundedClientError('storage_response_invalid') from exc
    if not isinstance(value, dict):
        raise BoundedClientError('storage_response_invalid')
    return value

def read_json(request):
    if request.method != 'GET' or request.data is not None or request.full_url != _instance_url('eu-de', INSTANCE_ID):
        raise BoundedClientError('storage_endpoint_refused')
    return transport_json(request)

def obtain_token(api_key):
    if not api_key or any(c.isspace() for c in api_key):
        raise BoundedClientError('api_key_invalid')
    request = urllib.request.Request(IAM_URL,
        data=urllib.parse.urlencode({'grant_type':'urn:ibm:params:oauth:grant-type:apikey','apikey':api_key}).encode(),
        headers={**_request_headers(),'Content-Type':'application/x-www-form-urlencoded'},method='POST')
    value = transport_json(request)
    token = value.get('access_token')
    if not isinstance(token,str) or not 20 <= len(token) <= 16384 or any(c.isspace() for c in token):
        raise BoundedClientError('iam_token_invalid')
    return token

def atom(value):
    if not isinstance(value, str) or not SAFE.fullmatch(value):
        raise BoundedClientError('storage_scalar_schema')
    return value

def reference(value, *, volume=False):
    if not isinstance(value, dict):
        raise BoundedClientError('storage_reference_schema')
    result = {'id': atom(value.get('id')), 'name': atom(value.get('name'))}
    if volume:
        result['volume'] = reference(value.get('volume')) if 'volume' in value else {'state': 'UNKNOWN', 'reason': 'REFERENCE_ONLY'}
    return result

def storage(value):
    instance = parse_instance(value, expected_id=INSTANCE_ID, expected_name=INSTANCE_NAME)
    if instance.status != 'stopped':
        raise BoundedClientError('storage_instance_not_stopped')
    zone = value.get('zone')
    if not isinstance(zone, dict) or zone.get('name') != 'eu-de-2':
        raise BoundedClientError('storage_zone_mismatch')
    profile = value.get('profile')
    if not isinstance(profile, dict):
        raise BoundedClientError('storage_profile_missing')
    result = {'id': INSTANCE_ID, 'name': INSTANCE_NAME, 'status': 'stopped', 'zone': 'eu-de-2',
              'profile': {'name': atom(profile.get('name'))}, 'scope': 'INSTANCE_EMBEDDED_METADATA_ONLY',
              'guest_device_correlation': 'UNKNOWN', 'full_profile_and_volume_details_read': False}
    disks = value.get('disks')
    if not isinstance(disks, list) or len(disks) > 32:
        raise BoundedClientError('storage_disks_schema')
    clean = []
    for disk in disks:
        if not isinstance(disk, dict) or disk.get('resource_type') != 'instance_disk':
            raise BoundedClientError('storage_disk_type_schema')
        size = disk.get('size')
        if type(size) is not int or not 0 < size <= 1000000:
            raise BoundedClientError('storage_disk_size_schema')
        clean.append({**reference(disk), 'size': size, 'resource_type': 'instance_disk',
                      'interface_type': atom(disk.get('interface_type'))})
    if len({d['id'] for d in clean}) != len(clean):
        raise BoundedClientError('storage_duplicate_disk')
    result['disks'] = sorted(clean, key=lambda d: d['id'])
    attachments = value.get('volume_attachments')
    if not isinstance(attachments, list) or len(attachments) > 64:
        raise BoundedClientError('storage_attachments_schema')
    clean = [reference(a, volume=True) for a in attachments]
    if len({a['id'] for a in clean}) != len(clean):
        raise BoundedClientError('storage_duplicate_attachment')
    result['volume_attachments'] = sorted(clean, key=lambda a: a['id'])
    result['boot_volume_attachment'] = reference(value.get('boot_volume_attachment'), volume=True)
    if result['boot_volume_attachment']['id'] not in {a['id'] for a in clean}:
        raise BoundedClientError('storage_boot_attachment_missing_from_list')
    return result

def collect(token):
    # Endpoint is constructed from fixed constants, never provider hrefs or CLI input.
    verify_identity(token)
    values = []
    for _ in range(2):
        request = urllib.request.Request(_instance_url('eu-de', INSTANCE_ID),
            headers={**_request_headers(), 'Authorization': 'Bearer ' + token}, method='GET')
        values.append({'observed_at': datetime.now(timezone.utc).isoformat(),
                       'instance': storage(read_json(request))})
    if values[0]['instance'] != values[1]['instance']:
        raise BoundedClientError('storage_metadata_drift')
    return {'state': 'READONLY_STORAGE_METADATA', 'observations': values, 'same_metadata': True,
            'atomic_snapshot': False, 'vpc_gets': 2, 'vpc_actions': 0}

def main():
    try:
        result = collect(obtain_token(os.environ.get('IBM_CLOUD_API_KEY', '')))
    except BoundedClientError as exc:
        # Existing client returns only bounded machine reason, never response body/token.
        print('IBM_STORAGE_EVIDENCE_RESULT=REFUSED reason=' + str(exc), file=sys.stderr)
        return 3
    except Exception:
        print('IBM_STORAGE_EVIDENCE_RESULT=UNKNOWN reason=collector_internal_error', file=sys.stderr)
        return 4
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))
    return 0

if __name__ == '__main__':
    sys.exit(main())
