"""Read-only IBM reconciliation. No application imports or guest file writes."""
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import stat
import subprocess
import time

UNITS = ('synthetic-lab.service', 'synthetic-lab-control-bridge.service',
         'synthetic-lab-control.service', 'synthetic-lab-observer.service')
ROOT = Path('/nonexistent/synthetic-guest-ledger')
CODE = Path('/nonexistent/synthetic-guest-code')
OP = 'synthetic-operation-001'
LIMIT = 131072

class Unknown(Exception): pass

def need(ok, reason):
    if not ok: raise Unknown(reason)

def command(argv):
    # Only callers below construct argv; errors never include stderr or argv.
    p = subprocess.run(argv, capture_output=True, timeout=2.5)
    need(p.returncode == 0, 'READ_COMMAND_FAILED')
    need(len(p.stdout) <= LIMIT and len(p.stderr) <= LIMIT, 'READ_OUTPUT_LIMIT')
    return p.stdout.decode('utf-8', 'strict').strip()

def hooks(unit, call=command):
    need(unit in UNITS, 'UNIT_SCOPE')
    raw = call(['/usr/bin/systemctl', 'show', '--all', '--no-pager',
                '--property=Id,LoadState,ActiveState,UnitFileState,ExecStop,ExecStopPost', unit])
    rows = {}
    for line in raw.splitlines():
        key, sep, value = line.partition('=')
        need(sep and key not in rows, 'SHOW_SCHEMA')
        rows[key] = value
    need(rows.get('Id') == unit and rows.get('LoadState') == 'loaded', 'UNIT_IDENTITY')
    base = ['/usr/bin/busctl', '--system', '--no-pager', '--timeout=2s']
    path = shlex.split(call(base + ['call', 'org.freedesktop.systemd1',
        '/org/freedesktop/systemd1', 'org.freedesktop.systemd1.Manager', 'GetUnit', 's', unit]))
    need(len(path) == 2 and path[0] == 'o' and re.fullmatch(
        r'/org/freedesktop/systemd1/unit/[A-Za-z0-9_]+', path[1]) is not None, 'OBJECT_SCHEMA')
    get = base + ['get-property', 'org.freedesktop.systemd1', path[1]]
    ident = shlex.split(call(get + ['org.freedesktop.systemd1.Unit', 'Id']))
    need(ident == ['s', unit], 'DBUS_IDENTITY')
    evidence = {}
    for prop in ('ExecStop', 'ExecStopPost'):
        raw = call(get + ['org.freedesktop.systemd1.Service', prop])
        tokens = raw.split(maxsplit=2)
        signature = tokens[0] if tokens else ''
        count = int(tokens[1]) if len(tokens) >= 2 and tokens[1].isdigit() else None
        valid = signature == 'a(sasbttttuii)' and count is not None
        # Only scalar facts leave the guest, never command path/argv/error text.
        evidence[prop] = {'show_present': prop in rows,
            'show_nonempty': bool(rows.get(prop, '').strip()),
            'type_matches': signature == 'a(sasbttttuii)', 'count': count,
            'empty_exact': raw == 'a(sasbttttuii) 0'}
        need(valid, 'HOOK_TYPE_UNKNOWN_' + prop)
    need(rows.get('ActiveState') in ('active','inactive','failed','activating','deactivating','reloading'), 'ACTIVE_SCHEMA')
    need(rows.get('UnitFileState') in ('enabled','disabled','static','masked','enabled-runtime','masked-runtime','indirect','alias','generated','transient','linked','linked-runtime'), 'ENABLEMENT_SCHEMA')
    return {'unit': unit, 'active': rows['ActiveState'], 'enabled': rows['UnitFileState'], 'hooks': evidence}

def metadata(path):
    # No symlink following, content reads, hashing of arbitrary guest files or locks.
    try: s = path.lstat()
    except FileNotFoundError: return {'exists': False}
    return {'exists': True, 'regular': stat.S_ISREG(s.st_mode),
            'directory': stat.S_ISDIR(s.st_mode), 'symlink': stat.S_ISLNK(s.st_mode),
            'uid': s.st_uid, 'mode': oct(stat.S_IMODE(s.st_mode)), 'size': s.st_size,
            'nlink': s.st_nlink}

def inventory(root=ROOT, code=CODE):
    result = {}
    for parent in (root.parent, root, root/OP, code.parent, code):
        m = metadata(parent); result[str(parent)] = m
        need(not m.get('exists') or (m['directory'] and not m['symlink']), 'EVIDENCE_PATH_TYPE')
    paths = [root/'lock', root/'mutation-intent.json', root/OP/'baseline.json',
             root/OP/'baseline-recovery.json', root/OP/'stop-intent.json',
             root/OP/'stop-client-returned.json', root/OP/'stop-observed.json', root/OP/'complete.json']
    paths += [code/n for n in ('runner.py','protocol.py','durable.py','guest.py','prepare-'+OP+'.json')]
    for path in paths: result[str(path)] = metadata(path)
    if (root/OP).exists():
        with os.scandir(root/OP) as it:
            entries = [Path(e.path) for e in itertools.islice(it,65)]
        need(len(entries) <= 64, 'LEDGER_COUNT_LIMIT')
        # Names outside the known set become a count, not arbitrary output.
        result['unlisted_operation_entries'] = sum(p not in paths for p in entries)
    return result

def main():
    signal.signal(signal.SIGALRM, signal.SIG_DFL); signal.alarm(45)
    try:
        need(socket.gethostname() == 'synthetic-target' and os.geteuid() == 0, 'HOST_IDENTITY')
        print(json.dumps({'phase':'DIAGNOSTIC_IDENTITY','host_verified':True,
            'systemd':command(['/usr/bin/systemctl','--version']).splitlines()[0]}),flush=True)
        ledger = inventory()
        print(json.dumps({'phase':'LEDGER_METADATA','data':ledger}),flush=True)
        unexpected = ledger[str(ROOT/'mutation-intent.json')]['exists'] or ledger.get('unlisted_operation_entries',0) != 0
        unexpected |= any(ledger[str(ROOT/OP/n)]['exists'] for n in ('baseline.json','baseline-recovery.json','stop-intent.json','stop-client-returned.json','stop-observed.json','complete.json'))
        need(not unexpected, 'LEDGER_REQUIRES_SEPARATE_RECONCILIATION')
        for unit in UNITS:
            row = hooks(unit)
            print(json.dumps({'phase':'HOOK_METADATA','data':row}),flush=True)
            need(all(v['empty_exact'] and not v['show_nonempty'] for v in row['hooks'].values()), 'HOOK_NOT_PROVEN_EMPTY')
        print(json.dumps({'phase':'READONLY_DIAGNOSTIC_COMPLETE','guest_writes':False}),flush=True)
        return 0
    except Exception as exc:
        print(json.dumps({'phase':'READONLY_DIAGNOSTIC_UNKNOWN',
            'reason':str(exc) if isinstance(exc,Unknown) else type(exc).__name__, 'guest_writes':False}),flush=True)
        return 2

if __name__ == '__main__': raise SystemExit(main())
