"""Stage an empty TLS PostgreSQL on the verified data disk, loopback access only."""
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import struct
import subprocess
import sys
import time

IMAGE = 'sha256:4ef4dbc939d61acea57712655ddb4b4ab27419c913f94cca0cd57cb3ea3c2280'
NAME = 'bridge-autopilot-postgres'
ROOT = Path('/srv/autopilot-data')
CONF = Path('/etc/bridge-autopilot-postgres')
UNIT = Path('/etc/systemd/system/bridge-autopilot-postgres.service')
LABEL = 'bridge-autopilot-pg-stage-v1'


def verified_tls_context(cafile):
    """Keep CA/hostname verification and require TLS 1.2 or newer.

    Kept local because administrators also execute this script over SSH stdin.
    """
    context = ssl.create_default_context(cafile=str(cafile))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def run(*args, timeout=60):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout).stdout.strip()


def write_new(path, content, mode=0o600, owner=0):
    assert not path.is_symlink()
    with path.open('x') as file:
        file.write(content)
    path.chmod(mode)
    os.chown(path, owner, owner)


def config():
    return """listen_addresses = '127.0.0.1'
port = 55432
ssl = on
ssl_cert_file = '/run/bridge-tls/server.crt'
ssl_key_file = '/run/bridge-tls/server.key'
ssl_min_protocol_version = 'TLSv1.2'
password_encryption = 'scram-sha-256'
hba_file = '/run/bridge-config/pg_hba.conf'
max_connections = 50
shared_buffers = '256MB'
work_mem = '4MB'
maintenance_work_mem = '64MB'
max_wal_size = '1GB'
min_wal_size = '80MB'
wal_level = replica
log_statement = 'none'
log_min_error_statement = 'panic'
"""


HBA = """local all postgres peer
hostnossl all all 0.0.0.0/0 reject
hostnossl all all ::/0 reject
hostssl all all 0.0.0.0/0 scram-sha-256
hostssl all all ::/0 scram-sha-256
"""

SERVICE = """[Unit]
Description=Bridge autopilot PostgreSQL (local staging)
Requires=docker.service
After=docker.service
RequiresMountsFor=/srv/autopilot-data
ConditionPathIsMountPoint=/srv/autopilot-data
[Service]
Type=simple
ExecStart=/usr/bin/docker start -a bridge-autopilot-postgres
ExecStop=/usr/bin/docker stop -t 30 bridge-autopilot-postgres
Restart=on-failure
RestartSec=10
TimeoutStopSec=45
[Install]
WantedBy=multi-user.target
"""


def verify_container(item):
    assert item['Image'] == IMAGE
    assert item['Config']['Labels'].get('managed_by') == LABEL
    host = item['HostConfig']
    assert host['NetworkMode'] == 'host' and not host['PortBindings']
    assert host['NanoCpus'] == 1000000000 and host['PidsLimit'] == 128
    assert host['LogConfig'] == {'Type':'json-file','Config':{'max-size':'10m','max-file':'3'}}
    assert item['Config']['Cmd'] == ['postgres','-c','config_file=/run/bridge-config/postgresql.conf']
    env = dict(entry.split('=',1) for entry in item['Config']['Env'])
    assert env['POSTGRES_PASSWORD_FILE'] == '/run/secrets/admin-password'
    assert env['POSTGRES_INITDB_ARGS'] == '--data-checksums --locale-provider=builtin --locale=C.UTF-8'
    assert env.get('POSTGRES_USER','postgres') == 'postgres'
    assert env.get('POSTGRES_DB','postgres') == 'postgres'
    assert 'POSTGRES_PASSWORD' not in env and 'POSTGRES_HOST_AUTH_METHOD' not in env
    assert host['Memory'] == 2 * 1024**3 and not host['Privileged']
    assert host['RestartPolicy']['Name'] == 'no'
    mounts = {m['Destination']: (m['Source'], m['RW']) for m in item['Mounts']}
    assert mounts == {
        '/var/lib/postgresql': (str(ROOT/'postgresql'), True),
        '/run/bridge-config': (str(CONF/'config'), False),
        '/run/bridge-tls': (str(CONF/'tls'), False),
        '/run/secrets/admin-password': (str(CONF/'admin-password'), False),
    }


def main():
    assert os.geteuid() == 0 and run('hostname') == 'autopilot-lite-vnic'
    os.umask(0o077)
    for path in (ROOT, CONF, UNIT):
        assert not any(p.is_symlink() for p in [path, *path.parents])
    assert os.path.ismount(ROOT)
    state = json.loads(Path('/var/lib/bridge-light-data-mount/provision.json').read_text())
    mounted = json.loads(run('findmnt','-J','-M',str(ROOT),'-o','UUID,FSTYPE'))['filesystems']
    assert len(mounted)==1 and mounted[0]['uuid']==state['uuid'] and mounted[0]['fstype']=='ext4'
    assert os.statvfs(ROOT).f_bavail * os.statvfs(ROOT).f_frsize > 30*1024**3
    image = json.loads(run('docker','image','inspect',IMAGE))[0]
    assert image['Id']==IMAGE and image['Architecture']=='arm64'
    marker = CONF/'prepared.json'
    if not marker.exists():
        assert not CONF.exists() and not UNIT.exists() and not (ROOT/'postgresql').exists()
        assert not run('docker','ps','-aq','--filter','name=^/'+NAME+'$')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',55432))
        CONF.mkdir(mode=0o700)
        for name in ('config','tls'):
            (CONF/name).mkdir(mode=0o755)
            (CONF/name).chmod(0o755)
        password = secrets.token_urlsafe(48)
        write_new(CONF/'admin-password',password+'\n',0o400,999)
        write_new(CONF/'config/postgresql.conf',config(),0o644)
        write_new(CONF/'config/pg_hba.conf',HBA,0o644)
        run('openssl','req','-x509','-newkey','rsa:3072','-nodes','-days','3650',
            '-subj','/CN=Bridge Autopilot Local CA', '-keyout',str(CONF/'ca.key'),'-out',str(CONF/'ca.crt'))
        (CONF/'ca.key').chmod(0o600)
        run('openssl','req','-new','-newkey','rsa:3072','-nodes','-subj','/CN=localhost',
            '-keyout',str(CONF/'tls/server.key'),'-out',str(CONF/'server.csr'))
        write_new(CONF/'server.ext','subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n')
        run('openssl','x509','-req','-in',str(CONF/'server.csr'),'-CA',str(CONF/'ca.crt'),
            '-CAkey',str(CONF/'ca.key'),'-CAcreateserial','-days','365',
            '-extfile',str(CONF/'server.ext'),'-out',str(CONF/'tls/server.crt'))
        for name in ('server.key','server.crt'):
            path=CONF/'tls'/name
            path.chmod(0o600 if name.endswith('key') else 0o644)
            os.chown(path,999,999)
        write_new(CONF/'tls/ca.crt',(CONF/'ca.crt').read_text(),0o644)
        (ROOT/'postgresql').mkdir(mode=0o700)
        os.chown(ROOT/'postgresql',999,999)
        write_new(marker,json.dumps({'version':1,'image':IMAGE,'disk_uuid':state['uuid']})+'\n')
    assert json.loads(marker.read_text()) == {'version':1,'image':IMAGE,'disk_uuid':state['uuid']}
    assert (CONF/'config/postgresql.conf').read_text()==config()
    assert (CONF/'config/pg_hba.conf').read_text()==HBA
    if not run('docker','ps','-aq','--filter','name=^/'+NAME+'$'):
        run('docker','create','--name',NAME,'--label','managed_by='+LABEL,
            '--restart','no','--memory','2g','--cpus','1','--pids-limit','128',
            '--log-driver','json-file','--log-opt','max-size=10m','--log-opt','max-file=3',
            '--network','host',
            '--mount','type=bind,src='+str(ROOT/'postgresql')+',dst=/var/lib/postgresql',
            '--mount','type=bind,src='+str(CONF/'config')+',dst=/run/bridge-config,readonly',
            '--mount','type=bind,src='+str(CONF/'tls')+',dst=/run/bridge-tls,readonly',
            '--mount','type=bind,src='+str(CONF/'admin-password')+',dst=/run/secrets/admin-password,readonly',
            '--env','POSTGRES_PASSWORD_FILE=/run/secrets/admin-password',
            '--env','POSTGRES_INITDB_ARGS=--data-checksums --locale-provider=builtin --locale=C.UTF-8',
            IMAGE,'postgres','-c','config_file=/run/bridge-config/postgresql.conf')
    verify_container(json.loads(run('docker','inspect',NAME))[0])
    if UNIT.exists():
        assert UNIT.read_text()==SERVICE
    else:
        write_new(UNIT,SERVICE,0o644)
    run('systemd-analyze','verify',str(UNIT))
    run('systemctl','daemon-reload')
    run('systemctl','enable','--now',UNIT.name)
    for attempt in range(30):
        ready=subprocess.run(['docker','exec',NAME,'pg_isready','-p','55432','-U','postgres'],capture_output=True,timeout=10)
        if ready.returncode==0:
            break
        time.sleep(2)
    else:
        raise TimeoutError('PostgreSQL readiness')
    check=run('docker','exec','--user','postgres',NAME,'psql','-XAt','-p','55432','-U','postgres','-d','postgres',
              '-c',"SELECT current_setting('ssl'),current_setting('server_version_num'),current_setting('data_checksums');")
    assert check.startswith('on|18') and check.endswith('|on')
    context=verified_tls_context(CONF/'ca.crt')
    with socket.create_connection(('127.0.0.1',55432),timeout=10) as connection:
        connection.sendall(struct.pack('!II',8,80877103))
        assert connection.recv(1)==b'S'
        with context.wrap_socket(connection,server_hostname='localhost') as secure:
            assert secure.getpeercert()
    assert run('systemctl','is-active',UNIT.name)=='active'
    print(json.dumps({'postgresql_stage':'READY_EMPTY','tls_enabled':True,'listen':'127.0.0.1:55432',
                      'memory_limit_gib':2,'writers_switched':False,'backup_ready':False}))


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'postgresql_stage':'NOT_CONFIRMED','error_type':type(exc).__name__}))
        sys.exit(2)
