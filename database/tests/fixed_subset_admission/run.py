"""Real PostgreSQL 16 integration tests; collection is strictly offline.

No DB import, environment lookup, network, migration or service is performed by
import, --list-cases or --collect-only. Execution requires both --execute and an
explicit credential-bearing loopback URL for the disposable fixedsubset_test DB.
This runner never provisions a server or invokes subprocesses. SQL remains NOT_RUN
until a future authorized invocation records actual case evidence.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import socket
import struct
import sys
import threading
import time
from urllib.parse import unquote, urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
PLAN_PATH = ROOT / 'database/tests/plans/fixed_subset_admission_v1.json'
FIXTURE_PATH = ROOT / 'tests/fixtures/universal_video_terminal_sql_v2.json'
HERE = Path(__file__).resolve().parent
PROFILE, REVISION, WORKER = 'bridge_3_1_free', '3.1-free-r25.16', 'synthetic-worker'
SOURCE, OUTPUT, WORK = 'sourceFolder000001', 'outputFolder000001', 'workFolder00000001'
BASE_MIGRATIONS = (
    '0056_universal_video_queue.sql', '0057_universal_video_canary_review_gate.sql',
    '0058_universal_video_terminal_v2_gate.sql', '0059_universal_video_terminal_size_guard.sql',
)
CASE_IDS = (
    'C01_CONCURRENT_IDENTICAL', 'C02_OVERLAPPING_DIFFERENT_KEYS',
    'C03_DISJOINT_PROGRESS', 'C04_REVERSED_MULTI_SOURCE_ORDER',
    'C05_SAME_KEY_DIFFERENT_ANCHOR', 'F01_PRECOMMIT_FAILURES',
    'F04_COMMIT_RESPONSE_LOST', 'F05_PARTIAL_STATE_INVISIBLE',
    'L01_LEGACY_BOTH_ORDERS', 'L02_PREEXISTING_LEGACY_WRITER_CUTOVER',
    'L03_HISTORICAL_DUPLICATES_ALL_STATUSES', 'L04_LEGACY_REQUEST_HOLD',
    'G01_DIRECT_DML_GUARD', 'G02_IMMUTABLE_IDENTITY_MUTATIONS',
    'G03_PRIVILEGES_AND_BYPASS', 'G04_GUC_FORGERY_NOT_AUTHORITY',
    'G05_APPROVAL_FULL_CONTENT_VALIDATION', 'R01_RECOVERY_MUTABLE_STATUS',
    'R03_SOURCE_CHANGED_AFTER_COMMIT', 'R04_ABSENT_AND_CORRUPT_BINDING',
    'S01_SOURCE_CHANGE_EVERY_ATTEMPT', 'S07_UNCHANGED_NULL_CHECKSUM',
    'S08_OBSERVATION_TRUST_BINDING', 'S09_STALE_AFTER_ADMISSION_LOCK_WAIT',
    'S10_STALE_AFTER_WORKER_LOCK_WAIT', 'W01_LEGACY_WORKER_AND_ELIGIBILITY',
    'W02_LEASE_RETRY_BUDGET_AND_LIVENESS', 'A01_REVOCATION_RACES',
    'T01_COMPLETE_SQL0058_TERMINAL_AND_MUTATIONS',
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def collect():
    plan = json.loads(PLAN_PATH.read_text(encoding='utf-8'))
    require(tuple(c['id'] for c in plan['cases']) == CASE_IDS, 'matrix ID/order drift')
    require(len(CASE_IDS) == 29 and len(set(CASE_IDS)) == 29, 'case mapping incomplete')
    require(set(CASES) == set(CASE_IDS), 'missing executable case implementation')
    return [{'id': case_id, 'status': 'NOT_RUN', 'sql_executed': False,
             'implementation': CASES[case_id].__name__} for case_id in CASE_IDS]


def explicit_dsn(value):
    """Reject services, query overrides, non-loopback targets and libpq defaults."""
    parsed = urlsplit(value)
    require(parsed.scheme in ('postgres', 'postgresql'), 'DSN must be an explicit PostgreSQL URL')
    require(parsed.hostname == '127.0.0.1', 'only literal 127.0.0.1 permitted')
    require(parsed.path == '/fixedsubset_test', 'database name must be fixedsubset_test')
    require(not parsed.query and not parsed.fragment, 'DSN overrides/fragments prohibited')
    require(parsed.username == 'postgres' and parsed.password == 'fixedsubset-local-only',
            'only the fixed disposable workflow credentials permitted')
    port = parsed.port or 5432
    require(port == 5432, 'only disposable service port 5432 permitted')
    return dict(host=parsed.hostname, hostaddr=parsed.hostname, port=port,
                dbname='fixedsubset_test', user=unquote(parsed.username),
                password=unquote(parsed.password), sslmode='disable', gssencmode='disable', connect_timeout=3,
                options='-c statement_timeout=65000 -c lock_timeout=60000',
                application_name='fixedsubset-disposable-tests', target_session_attrs='read-write')


def migration_text(path, *, rollback=False):
    """Expand only repository-relative psql includes; reject other metacommands."""
    path = path.resolve()
    base = (ROOT / ('database/rollback' if rollback else 'database/migrations')).resolve()
    require(path.is_relative_to(base), 'migration include escapes approved tree')
    chunks = []
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.startswith('\\set ON_ERROR_STOP'):
            continue
        if line.startswith('\\ir '):
            chunks.append(migration_text(path.parent / line[4:].strip(),rollback=rollback))
        elif line.lstrip().startswith('\\'):
            raise AssertionError('unsupported migration metacommand')
        else:
            chunks.append(line)
    return '\n'.join(chunks)


class CommitAckDropRelay:
    """Execution-only loopback transport fault; forwards real PostgreSQL frames.

    It neither generates SQL nor simulates a database. After being armed, it
    closes the client socket on the server's CommandComplete(COMMIT) frame,
    before forwarding that frame or ReadyForQuery. Thus the client receives a
    transport error after the actual server commit, with no acknowledgement.
    """
    def __init__(self):
        self.armed,self.dropped,self.stopped=threading.Event(),threading.Event(),threading.Event()
        self.errors,self.sockets,self.threads=[],[],[]
        self.listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        self.listener.bind(('127.0.0.1',0)); self.listener.listen(1); self.listener.settimeout(5)
        self.port=self.listener.getsockname()[1]
        self.thread=threading.Thread(target=self.serve,name='postgres-commit-ack-drop',daemon=True)
        self.thread.start()

    @staticmethod
    def exact(sock,count):
        chunks=[]
        while count:
            part=sock.recv(count)
            if not part: raise EOFError('relay peer closed')
            chunks.append(part); count-=len(part)
        return b''.join(chunks)

    def copy_client(self,client,server):
        try:
            while not self.stopped.is_set():
                chunk=client.recv(65536)
                if not chunk: return
                server.sendall(chunk)
        except (OSError,EOFError):
            if not self.stopped.is_set(): self.errors.append('client forwarding failed')

    def serve(self):
        try:
            client,_=self.listener.accept(); self.sockets.append(client)
            server=socket.create_connection(('127.0.0.1',5432),timeout=3)
            server.settimeout(65); client.settimeout(65); self.sockets.append(server)
            forward=threading.Thread(target=self.copy_client,args=(client,server),daemon=True)
            self.threads.append(forward); forward.start()
            while not self.stopped.is_set():
                header=self.exact(server,5); size=struct.unpack('!I',header[1:])[0]
                require(4<=size<=16*1024*1024,'invalid PostgreSQL frame length')
                body=self.exact(server,size-4)
                if self.armed.is_set() and header[:1]==b'C' and body==b'COMMIT\x00':
                    self.stopped.set(); self.dropped.set()
                    for peer in self.sockets:
                        try: peer.shutdown(socket.SHUT_RDWR)
                        except OSError: pass
                        peer.close()
                    return
                client.sendall(header+body)
        except (OSError,EOFError,AssertionError) as exc:
            if not self.stopped.is_set(): self.errors.append(type(exc).__name__+': '+str(exc))

    def close(self):
        self.stopped.set(); self.listener.close()
        for peer in self.sockets:
            try: peer.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            peer.close()
        self.thread.join(3)
        for thread in self.threads: thread.join(3)
        require(not self.thread.is_alive() and not any(t.is_alive() for t in self.threads),
                'relay thread did not stop')


class Harness:
    """At most eight genuine LOGIN sessions; two concurrent statements max."""
    def __init__(self, config, evidence):
        # Imports deliberately below the explicit --execute gate.
        import psycopg
        from psycopg.rows import dict_row
        from psycopg.types.json import Jsonb
        from psycopg import sql
        self.pg, self.row, self.jsonb, self.sql = psycopg, dict_row, Jsonb, sql
        self.config, self.evidence = config, evidence
        self.connections, self.log, self.pool = {}, [], ThreadPoolExecutor(max_workers=2)
        self.case_id = None
        self.credentials = {}
        self.migration = self.find_migration()

    def start(self):
        """Begin I/O after the caller has assigned this object for cleanup/logs."""
        self.connect('admin')
        info = self.one('admin', "SELECT current_database() db,current_setting('server_version_num')::int version,clock_timestamp() clock,inet_server_addr()::text addr,(SELECT rolsuper FROM pg_roles WHERE rolname=session_user) admin")
        require(info['db'] == 'fixedsubset_test' and 160000 <= info['version'] < 170000,
                'requires exact disposable database on PostgreSQL 16')
        # Docker published ports terminate at a container address. The client
        # endpoint is pinned to loopback; server inet_server_addr is evidence.
        require(info['admin'], 'disposable superuser required')
        require(self.scalar('admin', "SELECT to_regnamespace('video_queue') IS NULL"),
                'initial database must be clean; existing video_queue is never overwritten')
        self.server = {key: str(value) for key, value in info.items()}
        self.roles()

    @staticmethod
    def find_migration():
        paths = list((ROOT / 'database/migrations').glob('0060*.sql'))
        require(len(paths) == 1, 'exactly one 0060 migration required; implementation absent/ambiguous')
        return paths[0]

    def connect(self, name):
        require(name not in self.connections and len(self.connections) < 8, 'connection cap exceeded')
        options = dict(self.config)
        if name != 'admin':
            options.update(self.credentials[name])
        self.connections[name] = self.pg.connect(**options, autocommit=True, row_factory=self.row)
        return self.connections[name]

    def q(self, name, statement, params=()):
        params = tuple(self.jsonb(p) if isinstance(p, (dict, list)) else p for p in params)
        started = time.monotonic()
        logged = str(statement)
        if 'PASSWORD' in logged.upper(): logged = '[synthetic LOGIN role provisioning; credentials redacted]'
        record = {'session': name, 'sql': logged, 'parameter_count': len(params)}
        try:
            cursor = self.connections[name].execute(statement, params if params else None, prepare=False)
            rows = cursor.fetchall() if cursor.description else []
            record.update(status='OK', row_count=len(rows))
            return rows
        except self.pg.Error as exc:
            record.update(status='ERROR', sqlstate=exc.sqlstate,
                          message=exc.diag.message_primary, constraint=exc.diag.constraint_name)
            raise
        finally:
            record['elapsed_seconds'] = round(time.monotonic() - started, 6)
            self.log.append(record)

    def one(self, name, statement, params=()):
        rows = self.q(name, statement, params)
        require(len(rows) == 1, 'expected exactly one result row')
        return rows[0]

    def scalar(self, name, statement, params=()):
        return next(iter(self.one(name, statement, params).values()))

    def expect_error(self, call, *, codes=(), states=(), constraint=None):
        try:
            call()
        except self.pg.Error as exc:
            msg = exc.diag.message_primary or ''
            require(not codes or any(code in msg for code in codes), 'unexpected SQL error: '+msg)
            require(not states or exc.sqlstate in states, 'unexpected SQLSTATE: '+str(exc.sqlstate))
            require(constraint is None or exc.diag.constraint_name == constraint, 'wrong constraint')
            return {'sqlstate': exc.sqlstate, 'message': msg, 'constraint': exc.diag.constraint_name}
        raise AssertionError('SQL unexpectedly accepted forbidden operation')

    def roles(self):
        for role in ('bridge_school_reader','bridge_school_app','bridge_school_worker'):
            self.q('admin', self.sql.SQL('CREATE ROLE {} NOLOGIN').format(self.sql.Identifier(role)))
        for name in ('approver','collector','admit1','admit2','worker','legacy','attacker'):
            role, password = 'fixedsubset_'+name, secrets.token_urlsafe(24)
            self.q('admin', self.sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS').format(self.sql.Identifier(role), self.sql.Literal(password)))
            self.credentials[name] = {'user': role, 'password': password}
        self.q('admin', 'CREATE EXTENSION pgcrypto')
        self.q('admin', 'CREATE TABLE schema_migration(migration_key text PRIMARY KEY)')
        self.q('admin', 'GRANT bridge_school_app,bridge_school_worker TO fixedsubset_legacy')

    def reset(self, *, managed=True):
        for name, connection in self.connections.items():
            connection.execute('ROLLBACK')
        # Only subsequent successful cases use reset; first case started clean.
        self.q('admin', 'DROP SCHEMA IF EXISTS admission_test CASCADE')
        self.q('admin', 'DROP SCHEMA IF EXISTS video_queue CASCADE')
        for role in ('video_subset_approver','video_subset_collector','video_subset_admitter',
                     'video_subset_worker_v2','video_subset_owner'):
            self.q('admin',self.sql.SQL('DROP ROLE IF EXISTS {}').format(self.sql.Identifier(role)))
        self.q('admin', 'TRUNCATE schema_migration')
        for filename in BASE_MIGRATIONS:
            self.q('admin', migration_text(ROOT / 'database/migrations' / filename))
        if managed:
            self.apply_managed()
        if 'legacy' not in self.connections:
            self.connect('legacy')

    def apply_managed(self, migrate=True):
        if migrate:
            self.q('admin', migration_text(self.migration))
        for name, role in (('approver','video_subset_approver'),('collector','video_subset_collector'),
                           ('admit1','video_subset_admitter'),('admit2','video_subset_admitter'),
                           ('worker','video_subset_worker_v2')):
            self.q('admin', self.sql.SQL('GRANT {} TO {}').format(self.sql.Identifier(role), self.sql.Identifier(self.credentials[name]['user'])))
        for name in self.credentials:
            if name not in self.connections:
                self.connect(name)
        self.q('admin', (HERE / 'helpers.sql').read_text(encoding='utf-8'))

    def pid(self, name):
        return self.connections[name].info.backend_pid

    def submit(self, call):
        return self.pool.submit(call)

    def blocked(self, waiter, blocker, timeout=8):
        stop = time.monotonic()+timeout
        while time.monotonic() < stop:
            row = self.one('admin', 'SELECT pg_blocking_pids(%s) blockers', (self.pid(waiter),))
            if self.pid(blocker) in row['blockers']:
                self.log.append({'barrier':'BLOCK_CONFIRMED','waiter':waiter,'blocker':blocker,
                                 'waiter_pid':self.pid(waiter),'blocker_pid':self.pid(blocker)})
                return
            time.sleep(.05)
        raise AssertionError('intended PostgreSQL blocking relation never observed')

    def arm(self, session, relation, match, action='PAUSE', operation='INSERT'):
        if action == 'PAUSE':
            self.q('admin','SELECT pg_advisory_lock(60060,%s)',(self.pid(session),))
        self.q('admin', 'INSERT INTO admission_test.probe_control VALUES (%s,%s,%s,%s,%s)',
               (self.pid(session),relation,operation,match,action))

    def release(self, session):
        self.q('admin','SELECT pg_advisory_unlock(60060,%s)',(self.pid(session),))

    def clear_probe(self, session):
        self.q('admin','DELETE FROM admission_test.probe_control WHERE backend_pid=%s',(self.pid(session),))

    def snapshot(self):
        result = {}
        for table in ('batch','job','job_event','subset_approval','source_observation','subset_admission','source_reservation'):
            if self.scalar('admin','SELECT to_regclass(%s) IS NOT NULL',('video_queue.'+table,)):
                rows = self.q('admin',self.sql.SQL('SELECT to_jsonb(t) row FROM video_queue.{} t').format(self.sql.Identifier(table)))
                result[table] = sorted((r['row'] for r in rows),key=canonical)
        return result

    def immutable(self, binding):
        batch = self.scalar('admin','SELECT to_jsonb(b) FROM video_queue.batch b WHERE batch_id=%s',(binding['batch_id'],))
        jobs = [r['row'] for r in self.q('admin','SELECT to_jsonb(j) row FROM video_queue.job j WHERE batch_id=%s ORDER BY sequence',(binding['batch_id'],))]
        progress = {'status','attempt_count','lease_owner','lease_token','lease_expires_at','next_attempt_at',
                    'error_code','output','updated_at','completed_at'}
        return canonical({'binding':binding,'batch':{k:str(v) if isinstance(v,(datetime,)) else v for k,v in batch.items() if k not in progress},
                          'jobs':[{k:str(v) if isinstance(v,(datetime,)) else v for k,v in row.items() if k not in progress} for row in jobs]})

    def fixture(self, numbers=(1,2), *, reverse=False, age=0, runtime=False):
        from universal_video import batch_plan, subset_contract
        raw = [{'id':f'privateVideo{n:08d}','name':f'Lesson {n}.mp4','parents':[SOURCE],
                'mimeType':'video/mp4','size':'2000000','version':'17',
                'modifiedTime':'2026-10-10T10:00:00Z','trashed':False,'md5Checksum':None} for n in numbers]
        if reverse:
            for n,row in enumerate(raw): row['name']=f'Lesson {len(raw)-n}.mp4'
        plan = batch_plan.build_batch_plan(raw,source_folder_id=SOURCE,output_folder_id=OUTPUT,
                                         work_folder_id=WORK,max_jobs_per_batch=len(raw))
        value = subset_contract.build_subset_contract(plan,expected_plan_sha256=plan['plan_sha256'],
            subset_sequence=1,processing_profile=PROFILE,algorithm_revision=REVISION,
            canary_source_file_id=raw[-1 if reverse else 0]['id'])
        f = {'approval_id':str(uuid4()),'contract':value,'raw':raw,'request_key':value['enqueue_manifest']['request_key']}
        self.scalar('approver','SELECT video_queue.approve_subset(%s::uuid,%s::text,%s::jsonb,clock_timestamp()+interval \'1 hour\')',
                    (f['approval_id'],canonical(value),{'approved_by':'synthetic-fixture','approval_reference':'local-pg16-test','plan_sha256':value['plan_sha256']}))
        if runtime: self.runtime(f,True)
        f['observation_id']=self.observe(f,age=age)
        return f

    def runtime(self,f,enabled):
        return self.scalar('approver','SELECT video_queue.set_subset_runtime(%s::uuid,%s::boolean)',(f['approval_id'],enabled))

    def observe(self,f,*,claim=None,raw=None,age=0,owner=WORKER,session='collector'):
        oid=str(uuid4())
        sources=deepcopy(f['raw'] if raw is None else raw)
        if claim: sources=[r for r in sources if r['id']==claim['source_file_id']]
        self.scalar(session,'SELECT video_queue.observe_subset(%s::uuid,%s::uuid,%s::text,%s::jsonb,clock_timestamp()-(%s*interval \'1 second\'),%s::uuid,%s::uuid,%s::uuid,%s::integer,%s::text)',
                    (oid,f['approval_id'],'ATTEMPT' if claim else 'ADMISSION',sources,age,
                     None if claim else str(uuid4()),claim['job_id'] if claim else None,
                     claim['lease_token'] if claim else None,claim['attempt_count'] if claim else None,owner if claim else None))
        return oid

    def admit(self,f,session='admit1',**override):
        args={'approval_id':f['approval_id'],'request_key':f['request_key'],
              'contract_sha256':f['contract']['contract_sha256'],'queue_manifest_sha256':f['contract']['queue_manifest_sha256'],
              'observation_id':f['observation_id'],**override}
        result=self.scalar(session,'SELECT video_queue.admit_approved_subset(%s::uuid,%s::text,%s::text,%s::text,%s::uuid)',tuple(args[k] for k in ('approval_id','request_key','contract_sha256','queue_manifest_sha256','observation_id')))
        require(result['status'] in ('ADMITTED','RECOVERED_IMMUTABLE_BINDING'),'admission did not return binding')
        return result['binding']

    def recover(self,f,session='admit1',**override):
        args={'request_key':f['request_key'],'contract_sha256':f['contract']['contract_sha256'],
              'queue_manifest_sha256':f['contract']['queue_manifest_sha256'],**override}
        return self.scalar(session,'SELECT video_queue.recover_subset_admission(%s::text,%s::text,%s::text)',tuple(args.values()))

    def claim(self,session='worker'):
        return self.q(session,'SELECT * FROM video_queue.claim_job(%s,900,%s,%s)',(WORKER,PROFILE,REVISION))

    def attempt(self,f,c,*,obs=None,token=None,worker=WORKER):
        obs=obs or self.observe(f,claim=c)
        return self.scalar('worker','SELECT video_queue.authorize_subset_attempt(%s::uuid,%s::uuid,%s,%s::uuid)',(c['job_id'],token or c['lease_token'],worker,obs))

    def retry(self,c,session='worker'):
        return self.one(session,'SELECT * FROM video_queue.retry_subset_job(%s::uuid,%s::uuid,%s,%s,1)',(c['job_id'],c['lease_token'],WORKER,'UV_SYNTHETIC_RETRY'))

    def output(self,c):
        f=json.loads(FIXTURE_PATH.read_text(encoding='utf-8'))
        out=f['output']; manifest=out['artifact_manifest']; receipt=out['terminal_receipt']
        for target in (out,manifest,receipt): target['source_file_id']=c['source_file_id']
        out['stable_job_key']=manifest['job_id']=receipt['job_id']=c['stable_job_key']
        out['algorithm_revision']=manifest['algorithm_revision']=c['algorithm_revision']
        manifest['source_identity']={'file_id':c['source_file_id'],'name':c['source_name'],
            'mime_type':c['source_mime_type'],'size_bytes':c['source_size_bytes'],
            'parent_folder_id':c['source_folder_id'],'checksum':c['source_checksum']}
        for artifact in manifest['artifacts']: artifact['parent_id']=c['output_folder_id']
        return self.rehash(out)

    @staticmethod
    def rehash(out):
        out=deepcopy(out)
        out['artifact_manifest_sha256']=digest(out['artifact_manifest'])
        out['terminal_receipt']['artifact_manifest_sha256']=out['artifact_manifest_sha256']
        core={k:v for k,v in out['terminal_receipt'].items() if k!='evidence_sha256'}
        out['terminal_evidence_sha256']=out['terminal_receipt']['evidence_sha256']=digest(core)
        return out

    def finish(self,f,c,out=None,session='worker'):
        self.attempt(f,c)  # Committed pre-work proof, distinct from post-work evidence.
        obs=self.observe(f,claim=c)
        return self.one(session,'SELECT * FROM video_queue.finish_subset_job(%s::uuid,%s::uuid,%s,%s,%s::jsonb,%s::uuid,NULL)',
                        (c['job_id'],c['lease_token'],WORKER,'REVIEW_READY',out or self.output(c),obs))

    def verify_binding(self,f,binding):
        from universal_video import admission_binding
        b=self.scalar('admin','SELECT to_jsonb(b) FROM video_queue.batch b WHERE batch_id=%s',(binding['batch_id'],))
        # JSONB keeps PostgreSQL -infinity timestamps as strings and persisted
        # UUIDs canonical, without installing a custom psycopg datetime loader.
        jobs=[r['row'] for r in self.q('admin','SELECT to_jsonb(j) row FROM video_queue.job j WHERE batch_id=%s ORDER BY sequence',(binding['batch_id'],))]
        rebuilt=admission_binding.build_binding(f['contract'],expected_contract_sha256=f['contract']['contract_sha256'],
            approval_id=f['approval_id'],batch_row=b,job_rows=jobs)
        require(rebuilt==binding,'actual immutable queue rows do not rebuild exact rich binding')
        reservations=self.q('admin','SELECT * FROM video_queue.source_reservation WHERE admission_request_key=%s ORDER BY source_identity',(f['request_key'],))
        require(len(reservations)==len(jobs),'reservation count mismatch')
        planned={r['source_identity']:r for r in f['contract']['files']}
        for r in reservations:
            require(r['source_fingerprint']==planned[r['source_identity']]['source_fingerprint'],'reservation fingerprint mismatch')
            require(str(r['batch_id'])==binding['batch_id'] and str(r['job_id']) in {j['job_id'] for j in jobs},'reservation actual UUID mismatch')
        return binding

    def wait_expired(self,oid):
        stop=time.monotonic()+35
        while time.monotonic()<stop:
            if self.scalar('admin','SELECT clock_timestamp()>=expires_at FROM video_queue.source_observation WHERE observation_id=%s',(oid,)):
                return
            time.sleep(.05)
        raise AssertionError('server observation did not expire')

    def close(self):
        for connection in self.connections.values():
            try: connection.cancel()
            except Exception: pass
        self.pool.shutdown(wait=True,cancel_futures=True)
        for connection in self.connections.values(): connection.close()

    def fixture_change(self, table, statement, params=()):
        """Deliberate impossible/history fixture state, never an API acceptance."""
        require(table in {'job','batch','subset_admission','source_reservation','source_observation','subset_approval'}, 'fixture table not allowed')
        self.q('admin','BEGIN')
        try:
            self.q('admin',self.sql.SQL('ALTER TABLE video_queue.{} DISABLE TRIGGER ALL').format(self.sql.Identifier(table)))
            self.q('admin',statement,params)
            self.q('admin',self.sql.SQL('ALTER TABLE video_queue.{} ENABLE TRIGGER ALL').format(self.sql.Identifier(table)))
            self.q('admin','COMMIT')
        except Exception:
            self.q('admin','ROLLBACK')
            raise
        self.log.append({'fixture_only':'privileged synthetic state mutation','table':table})

    def lease_expire(self,c):
        self.fixture_change('job','UPDATE video_queue.job SET lease_expires_at=clock_timestamp()-interval \'1 second\' WHERE job_id=%s',(c['job_id'],))

    def ready_retry(self,c):
        self.fixture_change('job','UPDATE video_queue.job SET next_attempt_at=clock_timestamp()-interval \'1 second\' WHERE job_id=%s',(c['job_id'],))

    def legacy(self, *, numbers=(1,), key='legacy-synthetic', session='legacy'):
        files=[{'sequence':i,'file_id':f'privateVideo{n:08d}','name':f'Lesson {n}.mp4',
                'mime_type':'video/mp4','size_bytes':2000000,'checksum':None}
               for i,n in enumerate(numbers,1)]
        return self.one(session,'SELECT * FROM video_queue.enqueue_drive_batch(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)',
                        (key,SOURCE,OUTPUT,WORK,PROFILE,REVISION,files[0]['file_id'],digest(files),files))


def ordinary(h):
    h.reset()
    f=h.fixture(runtime=True)
    b=h.verify_binding(f,h.admit(f))
    c=h.claim()
    require(len(c)==1,'canary not claimed')
    return f,b,c[0]


def unchanged(h, before, message):
    require(h.snapshot()==before,message)


def c01(h):
    h.reset(); f=h.fixture()
    h.q('admit1','BEGIN')
    first=h.admit(f)
    h.q('admit2','BEGIN')
    pending=h.submit(lambda:h.admit(f,'admit2'))
    h.blocked('admit2','admit1')
    h.q('admit1','COMMIT')
    second=pending.result(10); h.q('admit2','COMMIT')
    require(first==second,'identical requests produced different bindings')
    h.verify_binding(f,first)
    require(h.scalar('admin',"SELECT count(*) FROM video_queue.job_event WHERE event_type='ENQUEUED'")==2,'duplicate enqueue events')


def c02(h):
    for reverse in (False,True):
        h.reset(); a=h.fixture(); b=h.fixture((1,3))
        if reverse:a,b=b,a
        h.q('admit1','BEGIN'); first=h.admit(a)
        h.q('admit2','BEGIN'); pending=h.submit(lambda:h.admit(b,'admit2'))
        h.blocked('admit2','admit1'); h.q('admit1','COMMIT')
        h.expect_error(lambda:pending.result(10),codes=('SOURCE_RESERVED','SOURCE_ALREADY','RESERVATION_CONFLICT'))
        h.q('admit2','ROLLBACK'); h.verify_binding(a,first)
        require(h.recover(b)['status']=='NOT_FOUND','loser committed a header')
        require(h.scalar('admin','SELECT count(*) FROM video_queue.source_reservation')==2,'partial loser reservation')
    # Waiter obtains initially-absent PK after reserving writer rolls back, but
    # source evidence expires during the actual unique-index wait.
    h.reset(); a=h.fixture(); b=h.fixture((1,3),age=25)
    before=h.snapshot()
    h.arm('admit1','source_reservation',{'source_identity':'google_drive:privateVideo00000002'})
    h.q('admit1','BEGIN'); first=h.submit(lambda:h.admit(a))
    h.blocked('admit1','admin')
    h.q('admit2','BEGIN'); second=h.submit(lambda:h.admit(b,'admit2'))
    h.blocked('admit2','admit1'); h.wait_expired(b['observation_id'])
    h.connections['admit1'].cancel()
    h.expect_error(lambda:first.result(10),states=('57014',))
    h.q('admit1','ROLLBACK'); h.release('admit1')
    h.expect_error(lambda:second.result(10),codes=('SUBSET_OBSERVATION_EXPIRED',))
    h.q('admit2','ROLLBACK'); unchanged(h,before,'waiter expiry left partial or consumed state')


def c03(h):
    h.reset(); a=h.fixture(); b=h.fixture((3,4))
    h.arm('admit1','source_reservation',{'source_identity':'google_drive:privateVideo00000002'})
    h.q('admit1','BEGIN'); pending=h.submit(lambda:h.admit(a))
    h.blocked('admit1','admin')
    independent=h.submit(lambda:h.admit(b,'admit2'))
    bound=independent.result(8)  # Must finish while A is still paused.
    h.verify_binding(b,bound)
    require(not pending.done(),'pause was not held')
    h.release('admit1'); first=pending.result(10); h.q('admit1','COMMIT')
    h.verify_binding(a,first)


def c04(h):
    for reverse in (False,True):
        h.reset(); a=h.fixture(reverse=False); b=h.fixture(reverse=True)
        require([r['file_id'] for r in a['contract']['enqueue_manifest']['files']] !=
                [r['file_id'] for r in b['contract']['enqueue_manifest']['files']],'fixture order not reversed')
        if reverse:a,b=b,a
        h.arm('admit1','source_reservation',{'source_identity':'google_drive:privateVideo00000001'})
        h.q('admit1','BEGIN'); first=h.submit(lambda:h.admit(a))
        h.blocked('admit1','admin'); h.q('admit2','BEGIN')
        second=h.submit(lambda:h.admit(b,'admit2')); h.blocked('admit2','admit1')
        h.release('admit1'); bound=first.result(10); h.q('admit1','COMMIT')
        h.expect_error(lambda:second.result(10),codes=('SOURCE_RESERVED','SOURCE_ALREADY','RESERVATION_CONFLICT'))
        h.q('admit2','ROLLBACK'); h.verify_binding(a,bound)


def c05(h):
    h.reset(); f=h.fixture(); h.admit(f); before=h.snapshot()
    for override in ({'contract_sha256':'0'*64},{'queue_manifest_sha256':'0'*64},
                     {'approval_id':str(uuid4())},{'contract_sha256':None}):
        h.expect_error(lambda:h.admit(f,**override),codes=('SUBSET_',))
        unchanged(h,before,'anchor conflict mutated state')
    h.expect_error(lambda:h.recover(f,contract_sha256='0'*64),codes=('SUBSET_',))
    unchanged(h,before,'mismatched recovery changed state')


def f01(h):
    for relation,op,match in (
        ('source_reservation','INSERT',{'source_identity':'google_drive:privateVideo00000002'}),
        ('job_event','INSERT',{'event_type':'ENQUEUED'}),
        ('subset_admission','UPDATE',{'state':'FINALIZED'}),
    ):
        h.reset(); f=h.fixture(); before=h.snapshot()
        h.arm('admit1',relation,match,action='FAULT',operation=op)
        h.expect_error(lambda:h.admit(f),codes=('ADMISSION_TEST_INJECTED_FAULT',))
        unchanged(h,before,'precommit fault did not roll back all stores/observation consumption')
        require(h.recover(f)['status']=='NOT_FOUND','fault left recoverable partial state')
        require(h.scalar('admin','SELECT consumed_at IS NULL FROM video_queue.source_observation WHERE observation_id=%s',(f['observation_id'],)), 'original observation consumed on rollback')
        h.clear_probe('admit1')
        # Reuse the same still-fresh observation: refreshing cannot conceal consumption bugs.
        h.verify_binding(f,h.admit(f))


def f04(h):
    h.reset(); f=h.fixture()
    h.connections.pop('admit1').close()
    relay=CommitAckDropRelay()
    try:
        options={**h.config,**h.credentials['admit1'],'port':relay.port}
        h.connections['admit1']=h.pg.connect(**options,autocommit=True,row_factory=h.row)
        h.q('admit1','BEGIN'); tentative=h.admit(f)
        relay.armed.set()
        h.expect_error(lambda:h.q('admit1','COMMIT'))
        require(relay.dropped.wait(3),'COMMIT acknowledgement was not intercepted')
        require(not relay.errors,'wire relay failed: '+repr(relay.errors))
    finally:
        connection=h.connections.pop('admit1',None)
        if connection is not None: connection.close()
        relay.close()
    h.connect('admit1')
    before=h.snapshot()
    one=h.recover(f)['binding']; two=h.recover(f,'admit2')['binding']
    require(one==two==tentative,'lost response recovery changed binding')
    unchanged(h,before,'recovery wrote rows or consumed observations')
    h.log.append({'fault':'server CommandComplete(COMMIT) intercepted before client delivery',
                  'wire_ack_drop_proven':True,'recovered_binding_sha256':one['binding_sha256']})


def f05(h):
    for relation,op,match in (
        ('source_reservation','INSERT',{'source_identity':'google_drive:privateVideo00000002'}),
        ('job_event','INSERT',{'event_type':'ENQUEUED'}),
        ('subset_admission','UPDATE',{'state':'FINALIZED'}),
    ):
        h.reset(); f=h.fixture(); before=h.snapshot()
        h.q('admin','LISTEN video_queue_ready')
        h.arm('admit1',relation,match,operation=op)
        h.q('admit1','BEGIN'); pending=h.submit(lambda:h.admit(f))
        h.blocked('admit1','admin'); unchanged(h,before,'partial rows visible before commit')
        h.connections['admit1'].cancel(); h.expect_error(lambda:pending.result(10),states=('57014',))
        h.q('admit1','ROLLBACK'); h.release('admit1')
        unchanged(h,before,'rollback retained partial writes')
        require(not list(h.connections['admin'].notifies(timeout=.1,stop_after=1)),'rolled back queue notification escaped')
        h.q('admin','UNLISTEN video_queue_ready')


def l01(h):
    for first in ('legacy','new'):
        h.reset(); f=h.fixture(); before=h.snapshot()
        if first=='legacy':
            h.expect_error(lambda:h.legacy(),states=('42501',)); unchanged(h,before,'legacy denied insert changed state')
            h.verify_binding(f,h.admit(f))
        else:
            h.q('admit1','BEGIN'); binding=h.admit(f)
            h.expect_error(lambda:h.legacy(),states=('42501',))
            h.q('admit1','COMMIT'); h.verify_binding(f,binding)


def l02(h):
    for commit_old in (True,False):
        h.reset(managed=False)
        h.q('admin','CREATE SCHEMA admission_test')
        h.q('admin',"CREATE FUNCTION admission_test.legacy_pause() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_advisory_xact_lock(60060,pg_backend_pid()); RETURN NEW; END $$")
        h.q('admin','CREATE TRIGGER legacy_pause BEFORE INSERT ON video_queue.job FOR EACH ROW EXECUTE FUNCTION admission_test.legacy_pause()')
        h.q('admin','SELECT pg_advisory_lock(60060,%s)',(h.pid('legacy'),))
        h.q('legacy','BEGIN'); writer=h.submit(lambda:h.legacy(key='legacy-inflight'))
        h.blocked('legacy','admin')
        # Temporarily reuse the otherwise idle admit2 slot with the explicit admin
        # credentials solely for migration, preserving the eight-connection cap.
        h.connections.pop('admit2').close()
        h.connections['cutover']=h.pg.connect(**h.config,autocommit=True,row_factory=h.row)
        migration=h.submit(lambda:h.q('cutover',migration_text(h.migration)))
        h.blocked('cutover','legacy')
        h.q('admin','SELECT pg_advisory_unlock(60060,%s)',(h.pid('legacy'),))
        writer.result(10); h.q('legacy','COMMIT' if commit_old else 'ROLLBACK')
        migration.result(15); h.connections.pop('cutover').close()
        h.q('admin','DROP SCHEMA admission_test CASCADE'); h.apply_managed(migrate=False)
        f=h.fixture()
        if commit_old:
            h.expect_error(lambda:h.admit(f),codes=('SOURCE_RESERVED','SOURCE_ALREADY','RESERVATION_CONFLICT'))
            require(h.scalar('admin','SELECT cardinality(historical_job_ids) FROM video_queue.source_reservation WHERE source_identity=%s',('google_drive:privateVideo00000001',))==1,'cutover omitted committed writer')
        else:h.verify_binding(f,h.admit(f))


def l03(h):
    h.reset(managed=False)
    statuses=('PENDING_CANARY','QUEUED','LEASED','REVIEW_READY','AMBIGUOUS','FAILED')
    expected=[]
    for i,status in enumerate(statuses):
        batch=h.legacy(key='historical-'+str(i))
        c=h.scalar('admin',"SELECT to_jsonb(j)||jsonb_build_object('source_folder_id',b.source_folder_id,'output_folder_id',b.output_folder_id,'work_folder_id',b.work_folder_id,'algorithm_revision',b.algorithm_revision,'processing_profile',b.processing_profile) FROM video_queue.job j JOIN video_queue.batch b USING(batch_id) WHERE j.batch_id=%s",(batch['batch_id'],))
        expected.append(str(c['job_id']))
        leased=status=='LEASED'; terminal=status in ('REVIEW_READY','AMBIGUOUS','FAILED')
        h.q('admin','UPDATE video_queue.job SET status=%s,attempt_count=5,lease_owner=%s,lease_token=%s,lease_expires_at=CASE WHEN %s THEN clock_timestamp()+interval \'1 hour\' END,completed_at=CASE WHEN %s THEN clock_timestamp() END,output=%s::jsonb WHERE job_id=%s',
            (status,WORKER if leased else None,str(uuid4()) if leased else None,leased,terminal,h.output(c) if status=='REVIEW_READY' else {},c['job_id']))
    before=h.snapshot(); h.apply_managed()
    row=h.one('admin','SELECT * FROM video_queue.source_reservation WHERE source_identity=%s',('google_drive:privateVideo00000001',))
    require(sorted(map(str,row['historical_job_ids']))==sorted(expected),'historical duplicate provenance incomplete')
    require(row['admission_request_key'] is None,'historical duplicate arbitrarily adopted')
    require(h.snapshot()['job']==before['job'],'cutover rewrote historical jobs')
    f=h.fixture(); h.expect_error(lambda:h.admit(f),codes=('SOURCE_RESERVED','SOURCE_ALREADY','RESERVATION_CONFLICT'))
    require(h.claim()==[],'managed claim reached historical >3 jobs')


def l04(h):
    # A syntactically valid subset key reaches the legacy HOLD branch rather
    # than stopping at the generic request-key argument validator.
    key='subset-v1:'+'a'*64
    h.reset(managed=False); h.legacy(key=key); h.apply_managed()
    f=h.fixture(); f['request_key']=key; before=h.snapshot()
    h.expect_error(lambda:h.recover(f),codes=('SUBSET_LEGACY_BINDING_HOLD',))
    h.expect_error(lambda:h.admit(f),codes=('SUBSET_LEGACY_BINDING_HOLD',))
    unchanged(h,before,'legacy recovery/admission adopted unbound request')


def grant_probe(h, tables):
    h.q('admin','GRANT USAGE ON SCHEMA video_queue TO fixedsubset_attacker')
    for table in tables:
        h.q('admin',h.sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON video_queue.{} TO fixedsubset_attacker').format(h.sql.Identifier(table)))


def g01(h):
    h.reset(); f=h.fixture(); binding=h.admit(f)
    grant_probe(h,('job',))
    before=h.snapshot()
    statement="INSERT INTO video_queue.job(batch_id,sequence,source_file_id,source_name,source_mime_type,source_size_bytes,source_checksum,stable_job_key,is_canary,status) VALUES (%s,3,'privateVideo00000003','Lesson 3.mp4','video/mp4',2000000,NULL,substr(encode(public.digest(convert_to('bridge-video|drive|privateVideo00000003','UTF8'),'sha256'),'hex'),1,32),false,'PENDING_CANARY')"
    h.expect_error(lambda:h.q('attacker',statement,(binding['batch_id'],)),codes=('SUBSET_',))
    unchanged(h,before,'direct INSERT with DML grant bypassed persisted reservation')


def g02(h):
    f,b,c=ordinary(h)
    h.q('admin','GRANT SELECT,UPDATE,DELETE ON video_queue.job,video_queue.batch TO fixedsubset_worker')
    h.q('admin','GRANT SELECT,UPDATE,DELETE ON video_queue.subset_admission,video_queue.source_reservation TO fixedsubset_admit1')
    before=h.snapshot()
    edits=[
      ('UPDATE video_queue.job SET source_file_id=%s WHERE job_id=%s',('privateVideo00000003',c['job_id'])),
      ('UPDATE video_queue.job SET stable_job_key=%s WHERE job_id=%s',('0'*32,c['job_id'])),
      ('UPDATE video_queue.job SET source_name=%s WHERE job_id=%s',('Renamed.mp4',c['job_id'])),
      ('UPDATE video_queue.job SET source_size_bytes=2000001 WHERE job_id=%s',(c['job_id'],)),
      ('UPDATE video_queue.job SET source_checksum=%s WHERE job_id=%s',('md5:'+'a'*32,c['job_id'])),
      ('UPDATE video_queue.job SET is_canary=false WHERE job_id=%s',(c['job_id'],)),
      ('UPDATE video_queue.batch SET algorithm_revision=%s WHERE batch_id=%s',('other_revision',b['batch_id'])),
      ('UPDATE video_queue.batch SET output_folder_id=%s WHERE batch_id=%s',('otherFolder000001',b['batch_id'])),
      ('UPDATE video_queue.subset_admission SET binding=%s::jsonb WHERE request_key=%s',({},f['request_key'])),
      ('DELETE FROM video_queue.source_reservation WHERE source_identity=%s',('google_drive:'+c['source_file_id'],)),
      ('UPDATE video_queue.source_reservation SET admission_request_key=%s WHERE source_identity=%s',('other-key','google_drive:'+c['source_file_id'])),
      ('DELETE FROM video_queue.job WHERE job_id=%s',(c['job_id'],)),
    ]
    for sql,args in edits:
        session='admit1' if 'video_queue.subset_' in sql or 'video_queue.source_reservation' in sql else 'worker'
        h.expect_error(lambda:h.q(session,sql,args),codes=('SUBSET_',))
        unchanged(h,before,'identity mutation changed persisted rows')
    h.q('worker','SELECT video_queue.heartbeat_subset_job(%s,%s,%s,900)',(c['job_id'],c['lease_token'],WORKER))
    require(h.recover(f)['binding']==b,'legitimate heartbeat changed immutable receipt')


def g03(h):
    h.reset(); f=h.fixture(); before=h.snapshot()
    for name in ('approver','collector','admit1','admit2','worker','legacy','attacker'):
        role=h.one('admin','SELECT rolsuper,rolbypassrls,rolcreaterole,rolcreatedb FROM pg_roles WHERE rolname=%s',(h.credentials[name]['user'],))
        require(not any(role.values()),'test principal has privileged bypass')
        require(h.scalar(name,'SELECT session_user')==h.credentials[name]['user'],'test uses impersonation instead of real login')
    h.q('admin','GRANT USAGE ON SCHEMA video_queue TO fixedsubset_attacker')
    for sql in (
        'SET ROLE video_subset_worker_v2', 'SET ROLE video_subset_approver',
        'SET session_replication_role=replica',
        'ALTER TABLE video_queue.job DISABLE TRIGGER ALL',
        'TRUNCATE video_queue.source_reservation',
        'UPDATE video_queue.source_observation SET observed_at=clock_timestamp()',
    ):h.expect_error(lambda:h.q('attacker',sql),states=('42501',))
    h.expect_error(lambda:h.admit(f,'legacy'),states=('42501',))
    h.expect_error(lambda:h.observe(f,session='attacker'),states=('42501',))
    defs=h.q('admin',"SELECT p.oid::regprocedure::text signature,p.prosecdef,p.proconfig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='video_queue' AND p.prosecdef")
    require(defs and all(any('search_path=' in x and 'pg_catalog' in x for x in (r['proconfig'] or [])) for r in defs),'unsafe SECURITY DEFINER search_path')
    # Catalog reads only: no source fixture changes permitted by any denial.
    unchanged(h,before,'permission attack mutated data')
    rollback_path=ROOT/'database/rollback/0060_fixed_subset_admission.sql'
    h.expect_error(lambda:h.q('admin',migration_text(rollback_path,rollback=True)),codes=('SUBSET_NONEMPTY_ROLLBACK_REFUSED',))
    h.q('admin','ROLLBACK'); unchanged(h,before,'nonempty rollback changed state')
    h.reset()
    h.q('admin',migration_text(rollback_path,rollback=True))
    require(h.scalar('admin',"SELECT to_regclass('video_queue.source_reservation') IS NOT NULL AND EXISTS (SELECT FROM pg_trigger WHERE tgname='subset_guard') AND EXISTS(SELECT FROM schema_migration WHERE migration_key='0060_fixed_subset_admission')"),
            'empty deactivation destroyed permanent guards/schema/history')
    for principal,signature in (
        ('fixedsubset_approver','video_queue.approve_subset(uuid,text,jsonb,timestamptz)'),
        ('fixedsubset_collector','video_queue.observe_subset(uuid,uuid,text,jsonb,timestamptz,uuid,uuid,uuid,integer,text)'),
        ('fixedsubset_admit1','video_queue.admit_approved_subset(uuid,text,text,text,uuid)'),
        ('fixedsubset_worker','video_queue.claim_job(text,integer,text,text)'),
        ('fixedsubset_legacy','video_queue.enqueue_drive_batch(text,text,text,text,text,text,text,text,jsonb)'),
        ('fixedsubset_legacy','video_queue.claim_job(text,integer,text,text)')):
        require(not h.scalar('admin','SELECT has_function_privilege(%s,%s,\'EXECUTE\')',(principal,signature)),
                'empty deactivation enabled a runtime/legacy entrypoint')
    h.log.append({'rollback_file_sha256':hashlib.sha256(rollback_path.read_bytes()).hexdigest(),
                  'empty_deactivation':'PASS','nonempty_refusal':'PASS'})


def g04(h):
    h.reset(); f=h.fixture(); b=h.admit(f); grant_probe(h,('job',))
    h.q('attacker',"SELECT set_config('video_queue.admission_authorized','true',false),set_config('video_queue.request_key',%s,false),set_config('video_queue.worker_version','v2',false)",(f['request_key'],))
    before=h.snapshot()
    h.expect_error(lambda:h.q('attacker',"INSERT INTO video_queue.job(batch_id,sequence,source_file_id,source_name,source_mime_type,source_size_bytes,stable_job_key,is_canary,status) VALUES (%s,3,'privateVideo00000003','Lesson 3.mp4','video/mp4',2000000,%s,false,'PENDING_CANARY')",(b['batch_id'],hashlib.sha256(b'bridge-video|drive|privateVideo00000003').hexdigest()[:32])),codes=('SUBSET_',))
    h.expect_error(lambda:h.claim('attacker'),states=('42501',))
    unchanged(h,before,'caller settings authorized work')


def g05(h):
    h.reset(); f=h.fixture(); source=f['contract']
    vectors=[None,False,0,-9223372036854775808,18446744073709551615,
             {'z':[None,False,True,10**80], 'a':'quote=" slash=\\\n\t\r\b\f control=\u0001'},
             {'\U0001f600':'non-BMP \U0001d11e','\u0431':'\u041c\u043e\u0441\u043a\u0432\u0430','\u00e9':'e\u0301','\u4e2d':'\u6587'}]
    for number,vector in enumerate(vectors):
        encoded=canonical(vector)
        row=h.one('admin','SELECT video_queue.subset_json(%s::jsonb) canonical,video_queue.subset_sha(%s::jsonb) sha256',(encoded,encoded))
        require(row['canonical']==encoded and row['sha256']==digest(vector),'SQL/Python canonical vector mismatch')
        h.log.append({'canonical_vector':number,'status':'PASS','sha256':row['sha256']})
    for raw in ('1.5','1.0','1e-1'):
        h.expect_error(lambda:h.q('admin','SELECT video_queue.subset_json(%s::jsonb)',(raw,)),codes=('SUBSET_CANONICAL_INTEGER_DOMAIN',))
    variants=[]
    for key in ('files','enqueue_manifest','plan_sha256'):
        value=deepcopy(source); value.pop(key); variants.append(value)
    for change in ('flag','manifest_name','source_fingerprint','source_extra','manifest_sequence','wrong_canary'):
        value=deepcopy(source)
        if change=='flag':value['execution_allowed']=True
        elif change=='manifest_name':value['enqueue_manifest']['files'][0]['name']='Not approved.mp4'
        elif change=='source_fingerprint':value['files'][0]['source_fingerprint']='0'*64
        elif change=='source_extra':value['files'].append(deepcopy(value['files'][0]))
        elif change=='manifest_sequence':value['enqueue_manifest']['files'][0]['sequence']=9
        else:value['canary_source_file_id']='privateVideo00000003'
        value['subset_sha256']=digest(value['files'])
        value['enqueue_manifest']['inventory_sha256']=digest(value['enqueue_manifest']['files'])
        request_core={k:value[k] for k in ('plan_sha256','subset_sha256','subset_sequence','processing_profile','algorithm_revision','canary_source_file_id')}
        value['enqueue_manifest']['request_key']='subset-v1:'+digest(request_core)
        value['queue_manifest_sha256']=digest(value['enqueue_manifest'])
        value['contract_sha256']=digest({k:v for k,v in value.items() if k!='contract_sha256'})
        variants.append(value)
    before=h.snapshot()
    for value in variants:
        h.expect_error(lambda:h.scalar('approver','SELECT video_queue.approve_subset(%s::uuid,%s,%s::jsonb,clock_timestamp()+interval \'1 hour\')',
                       (str(uuid4()),canonical(value),{'approved_by':'synthetic','approval_reference':'invalid-contract','plan_sha256':source['plan_sha256']})),codes=('SUBSET_',))
        unchanged(h,before,'invalid full approval accepted or partially persisted')
    # Recompute every affected digest/request anchor so wrong JSON scalar types
    # reach their semantic clause instead of merely failing stale hashes.
    for path,replacement,error in (
        (('plan_sha256',),int('1'*64),'SUBSET_CONTRACT_INVALID'),
        (('enqueue_manifest','output_folder_id'),123456789012,'SUBSET_MANIFEST_INVALID'),
        (('enqueue_manifest','work_folder_id'),123456789013,'SUBSET_MANIFEST_INVALID'),
        (('files',0,'parent'),None,'SUBSET_SOURCE_INVALID'),
        (('files',0,'version'),17,'SUBSET_SOURCE_INVALID'),
        (('files',0,'modifiedTime'),20261010,'SUBSET_SOURCE_INVALID'),
        (('files',0,'name'),None,'SUBSET_SOURCE_INVALID'),
        (('files',0,'mime_type'),123,'SUBSET_SOURCE_INVALID'),
    ):
        value=deepcopy(source); target=value
        for part in path[:-1]: target=target[part]
        target[path[-1]]=replacement
        for row in value['files']:
            row['source_fingerprint']=digest({k:v for k,v in row.items() if k not in ('name','trashed','source_fingerprint','duration_ms','estimated_cost_usd')})
        value['subset_sha256']=digest(value['files'])
        core={k:value[k] for k in ('plan_sha256','subset_sha256','subset_sequence','processing_profile','algorithm_revision','canary_source_file_id')}
        value['enqueue_manifest']['request_key']='subset-v1:'+digest(core)
        value['queue_manifest_sha256']=digest(value['enqueue_manifest'])
        value['contract_sha256']=digest({k:v for k,v in value.items() if k!='contract_sha256'})
        h.expect_error(lambda:h.scalar('approver','SELECT video_queue.approve_subset(%s::uuid,%s,%s::jsonb,clock_timestamp()+interval \'1 hour\')',
            (str(uuid4()),canonical(value),{'approved_by':'synthetic','approval_reference':'wrong-scalar','plan_sha256':value['plan_sha256']})),codes=(error,))
        unchanged(h,before,'self-rehashed wrong scalar approval accepted')
        h.log.append({'approval_scalar_variant':'.'.join(map(str,path)),'expected_rejection':error})
    for cost,error in ((1.5,'SUBSET_CANONICAL_INTEGER_DOMAIN'),(1.0,'SUBSET_CANONICAL_INTEGER_DOMAIN'),(1e30,'SUBSET_CANONICAL_BYTES_MISMATCH')):
        value=deepcopy(source); value['files'][0]['estimated_cost_usd']=cost
        value['subset_sha256']=digest(value['files'])
        core={k:value[k] for k in ('plan_sha256','subset_sha256','subset_sequence','processing_profile','algorithm_revision','canary_source_file_id')}
        value['enqueue_manifest']['request_key']='subset-v1:'+digest(core)
        value['queue_manifest_sha256']=digest(value['enqueue_manifest'])
        value['contract_sha256']=digest({k:v for k,v in value.items() if k!='contract_sha256'})
        h.expect_error(lambda:h.scalar('approver','SELECT video_queue.approve_subset(%s::uuid,%s,%s::jsonb,clock_timestamp()+interval \'1 hour\')',
            (str(uuid4()),canonical(value),{'approved_by':'synthetic','approval_reference':'noninteger-cost','plan_sha256':value['plan_sha256']})),codes=(error,))
        unchanged(h,before,'restricted canonical cost domain was bypassed')


def r01(h):
    f,b,c=ordinary(h); immutable=h.immutable(b); before=h.snapshot()
    require(h.recover(f)['binding']==b,'RUNNING recovery differs'); unchanged(h,before,'RUNNING recovery writes')
    result=h.finish(f,c)
    require(result['batch_status']=='CANARY_REVIEW' and result['released_jobs']==0,'canary review gate opened')
    before=h.snapshot(); require(h.recover(f)['binding']==b,'CANARY_REVIEW recovery differs')
    unchanged(h,before,'CANARY_REVIEW recovery consumed or wrote rows')
    require(h.immutable(b)==immutable,'worker transition mutated immutable state')


def r03(h):
    f,b,c=ordinary(h)
    raw=deepcopy(f['raw']); raw[0].update(version='18',modifiedTime='2026-10-11T10:00:00Z',name='Renamed.mp4')
    before=h.snapshot(); require(h.recover(f)['binding']==b,'recovery depends on remote source revision')
    unchanged(h,before,'recovery renewed/consumed observations')
    h.expect_error(lambda:h.observe(f,claim=c,raw=raw),codes=('SUBSET_SOURCE_',))
    # Revocation does not erase the admitted fact.
    h.scalar('approver','SELECT video_queue.revoke_subset_approval(%s::uuid)',(f['approval_id'],))
    before=h.snapshot(); require(h.recover(f)['binding']==b,'revocation erased binding')
    unchanged(h,before,'revoked recovery mutated state')


def r04(h):
    for damage in ('none','missing_header','binding_hash','job_mapping','reservation'):
        h.reset(); f=h.fixture()
        if damage=='none':
            before=h.snapshot(); require(h.recover(f)['status']=='NOT_FOUND','absent key not NOT_FOUND')
        else:
            b=h.admit(f)
            if damage=='missing_header':h.fixture_change('subset_admission','DELETE FROM video_queue.subset_admission WHERE request_key=%s',(f['request_key'],))
            elif damage=='binding_hash':h.fixture_change('subset_admission','UPDATE video_queue.subset_admission SET binding_sha256=%s WHERE request_key=%s',('0'*64,f['request_key']))
            elif damage=='job_mapping':h.fixture_change('subset_admission',"UPDATE video_queue.subset_admission SET binding=jsonb_set(binding,'{jobs,0,job_id}',to_jsonb(%s::text)) WHERE request_key=%s",(str(uuid4()),f['request_key']))
            else:h.fixture_change('source_reservation','UPDATE video_queue.source_reservation SET source_fingerprint=%s WHERE admission_request_key=%s',('0'*64,f['request_key']))
            before=h.snapshot(); h.expect_error(lambda:h.recover(f),codes=('SUBSET_',))
        unchanged(h,before,'recovery repaired/reenqueued corrupted history')


def s01(h):
    for between in (False,True):
        for change in ({'version':'18'},{'version':'18','modifiedTime':'2026-10-11T10:00:00Z'},
                       {'modifiedTime':'2026-10-11T10:00:00Z','md5Checksum':None}):
            f,b,c=ordinary(h)
            if between:
                h.attempt(f,c); h.retry(c); h.ready_retry(c); c=h.claim()[0]
                require(c['attempt_count']==2,'between-attempt test did not reach attempt2')
            raw=deepcopy(f['raw']); raw[0].update(change)
            before=h.snapshot()
            h.expect_error(lambda:h.observe(f,claim=c,raw=raw),codes=('SUBSET_SOURCE_',))
            unchanged(h,before,'changed native source produced trusted attempt evidence')
            # Also exercise the actual attempt verifier with a trusted-row fault;
            # ingestion rejection alone cannot stand in for this defense.
            obs=h.observe(f,claim=c)
            h.fixture_change('source_observation',"UPDATE video_queue.source_observation SET sources=jsonb_set(sources,'{0,version}','\"18\"'::jsonb) WHERE observation_id=%s",(obs,))
            before=h.snapshot(); h.expect_error(lambda:h.attempt(f,c,obs=obs),codes=('SUBSET_',))
            unchanged(h,before,'attempt accepted mutated source record')
            require(h.recover(f)['binding']==b,'source change altered immutable binding')


def s07(h):
    f,b,c=ordinary(h)
    require(c['source_checksum'] is None,'null-checksum fixture drift')
    obs=h.observe(f,claim=c); result=h.attempt(f,c,obs=obs)
    require(result is not None,'unchanged null-checksum source did not authorize consistency')
    require(h.recover(f)['binding']==b,'attempt changed binding')


def s08(h):
    h.reset(); f=h.fixture(); before=h.snapshot()
    for oid in (str(uuid4()),None):
        h.expect_error(lambda:h.admit(f,observation_id=oid),codes=('SUBSET_',))
        unchanged(h,before,'invalid observation changed admission')
    other=h.fixture((3,4)); before=h.snapshot()
    h.expect_error(lambda:h.admit(f,observation_id=other['observation_id']),codes=('SUBSET_',))
    unchanged(h,before,'cross-contract observation accepted')
    b=h.admit(f); h.runtime(f,True); c=h.claim()[0]
    for field,value in (('lease_token',str(uuid4())),('attempt_count',2),('job_id',str(uuid4())),('worker_key','other-worker')):
        obs=h.observe(f,claim=c)
        h.fixture_change('source_observation',h.sql.SQL('UPDATE video_queue.source_observation SET {}=%s WHERE observation_id=%s').format(h.sql.Identifier(field)),(value,obs))
        before=h.snapshot(); h.expect_error(lambda:h.attempt(f,c,obs=obs),codes=('SUBSET_',))
        unchanged(h,before,'attempt scope mismatch accepted')
    # Server timestamp checks at collector boundary, including future timestamps.
    h.expect_error(lambda:h.observe(f,claim=c,age=-5),codes=('SUBSET_',))
    h.expect_error(lambda:h.observe(f,claim=c,age=31),codes=('SUBSET_',))


def s09(h):
    h.reset(); f=h.fixture(age=25); before=h.snapshot()
    h.q('admin','BEGIN'); h.q('admin','SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(f['request_key'],))
    pending=h.submit(lambda:h.admit(f)); h.blocked('admit1','admin')
    h.wait_expired(f['observation_id']); h.q('admin','COMMIT')
    h.expect_error(lambda:pending.result(10),codes=('SUBSET_OBSERVATION_EXPIRED',))
    unchanged(h,before,'stale post-lock admission left writes')
    # Document the deferred-trigger timing limitation instead of promising
    # guaranteed COMMIT-time freshness after constraints are forced immediate.
    h.reset(); f=h.fixture(age=25)
    h.q('admit1','BEGIN'); binding=h.admit(f)
    h.q('admit1','SET CONSTRAINTS ALL IMMEDIATE'); h.wait_expired(f['observation_id'])
    h.q('admit1','COMMIT'); h.verify_binding(f,binding)
    require(h.recover(f)['binding']==binding,'delayed commit lost structural binding')
    h.runtime(f,True); c=h.claim()[0]
    h.expect_error(lambda:h.attempt(f,c,obs=f['observation_id']),codes=('SUBSET_',))
    h.attempt(f,c)  # Fresh separately scoped evidence required.
    h.log.append({'limitation':'fresh at final locked authorization, not guaranteed fresh at every COMMIT'})


def s10(h):
    f,b,c=ordinary(h); obs=h.observe(f,claim=c,age=25)
    before=h.snapshot(); h.q('admin','BEGIN')
    h.q('admin','SELECT job_id FROM video_queue.job WHERE job_id=%s FOR UPDATE',(c['job_id'],))
    pending=h.submit(lambda:h.attempt(f,c,obs=obs)); h.blocked('worker','admin')
    h.wait_expired(obs); h.q('admin','COMMIT')
    h.expect_error(lambda:pending.result(10),codes=('SUBSET_OBSERVATION_EXPIRED',))
    unchanged(h,before,'stale worker gate authorized processing')

    # Approval can expire while the worker holds its approval share lock but
    # waits for the observation row. A post-wait check must cover both clocks.
    f,b,c=ordinary(h); obs=h.observe(f,claim=c)
    h.fixture_change('subset_approval',"UPDATE video_queue.subset_approval SET expires_at=clock_timestamp()+interval '3 seconds' WHERE approval_id=%s",(f['approval_id'],))
    before=h.snapshot(); h.q('admin','BEGIN')
    h.q('admin','SELECT observation_id FROM video_queue.source_observation WHERE observation_id=%s FOR UPDATE',(obs,))
    pending=h.submit(lambda:h.attempt(f,c,obs=obs)); h.blocked('worker','admin')
    deadline=time.monotonic()+8
    while not h.scalar('admin','SELECT clock_timestamp()>=expires_at FROM video_queue.subset_approval WHERE approval_id=%s',(f['approval_id'],)):
        require(time.monotonic()<deadline,'approval expiry barrier timed out'); time.sleep(.05)
    h.q('admin','COMMIT')
    h.expect_error(lambda:pending.result(10),codes=('SUBSET_APPROVAL_NOT_ACTIVE',))
    unchanged(h,before,'approval expiry during observation wait was ignored')


def w01(h):
    h.reset(managed=False); legacy=h.legacy(numbers=(90,),key='oldest-unbound')
    old=h.scalar('admin','SELECT to_jsonb(j) FROM video_queue.job j WHERE batch_id=%s',(legacy['batch_id'],))
    h.apply_managed()
    held=h.fixture((10,11)); h.admit(held)
    revoked=h.fixture((20,21),runtime=True); h.admit(revoked)
    h.q('approver','SELECT video_queue.revoke_subset_approval(%s)',(revoked['approval_id'],))
    draft=h.fixture((30,31),runtime=True)
    h.arm('admit1','subset_admission',{'state':'FINALIZED'},operation='UPDATE')
    h.q('admit1','BEGIN'); pending=h.submit(lambda:h.admit(draft)); h.blocked('admit1','admin')
    eligible=h.fixture((40,41),runtime=True); binding=h.admit(eligible,'admit2')
    for phase in ('while_draft_uncommitted','after_commit'):
        before=h.snapshot()
        h.expect_error(lambda:h.claim('legacy'),states=('42501',))
        h.expect_error(lambda:h.q('legacy','SET ROLE video_subset_worker_v2'),states=('42501',))
        h.q('legacy',"SELECT set_config('video_queue.worker_version','v2',false),set_config('video_queue.admission_authorized','true',false)")
        h.expect_error(lambda:h.claim('legacy'),states=('42501',))
        unchanged(h,before,'legacy worker claimed or modified a managed job')
        if phase=='while_draft_uncommitted':
            claim=h.claim(); require(len(claim)==1 and str(claim[0]['batch_id'])==binding['batch_id'],
                                     'selector starved behind older ineligible jobs')
            h.release('admit1'); pending.result(10); h.q('admit1','COMMIT')
    require(h.scalar('admin','SELECT to_jsonb(j) FROM video_queue.job j WHERE job_id=%s',(old['job_id'],))==old,
            'claim mutated historical ineligible job')
    for f in (held,revoked):
        require(h.scalar('admin','SELECT bool_and(attempt_count=0 AND lease_token IS NULL) FROM video_queue.job j JOIN video_queue.batch b USING(batch_id) WHERE b.request_key=%s',(f['request_key'],)),
                'held/revoked batch was claimed')
    c=claim[0]
    for statement,params in (
        ('SELECT video_queue.heartbeat_job(%s,%s,%s,900)',(c['job_id'],c['lease_token'],WORKER)),
        ('SELECT * FROM video_queue.retry_job(%s,%s,%s,%s,4,1)',(c['job_id'],c['lease_token'],WORKER,'UV_RETRY')),
        ('SELECT * FROM video_queue.finish_job(%s,%s,%s,%s,%s::jsonb,NULL)',(c['job_id'],c['lease_token'],WORKER,'REVIEW_READY',h.output(c))),
        ('SELECT video_queue.heartbeat_subset_job(%s,%s,%s,900)',(c['job_id'],c['lease_token'],WORKER))):
        h.expect_error(lambda statement=statement,params=params:h.q('legacy',statement,params),states=('42501',))
    definition=h.scalar('admin',"SELECT pg_get_functiondef('video_queue.claim_job(text,integer,text,text)'::regprocedure)")
    require(re.search(r'SELECT j\.\* INTO v_job[\s\S]+WHERE video_queue\.subset_worker_eligible[\s\S]+FOR UPDATE OF j SKIP LOCKED',definition),
            'eligibility missing from real SKIP LOCKED selector')
    h.log.append({'selector_function_sha256':hashlib.sha256(definition.encode()).hexdigest()})


def w02(h):
    f,b,c1=ordinary(h); old_obs=h.observe(f,claim=c1); h.attempt(f,c1,obs=old_obs)
    h.lease_expire(c1); c2=h.claim()[0]
    require(c2['job_id']==c1['job_id'] and c2['attempt_count']==2 and c2['lease_token']!=c1['lease_token'],
            'reclaim did not rotate token/increment existing job')
    post=h.observe(f,claim=c2)
    invalid_calls=[
        lambda:h.q('worker','SELECT video_queue.heartbeat_subset_job(%s,%s,%s,900)',(c1['job_id'],c1['lease_token'],WORKER)),
        lambda:h.attempt(f,c1,obs=old_obs),
        lambda:h.q('worker','SELECT video_queue.authorize_subset_attempt(%s,NULL,%s,%s)',(c2['job_id'],WORKER,post)),
        lambda:h.q('worker','SELECT video_queue.heartbeat_subset_job(%s,NULL,%s,900)',(c2['job_id'],WORKER)),
        lambda:h.q('worker','SELECT * FROM video_queue.retry_subset_job(%s,NULL,%s,%s,1)',(c2['job_id'],WORKER,'UV_RETRY')),
        lambda:h.q('worker','SELECT * FROM video_queue.finish_subset_job(%s,NULL,%s,%s,%s::jsonb,%s,NULL)',(c2['job_id'],WORKER,'REVIEW_READY',h.output(c2),post)),
    ]
    for call in invalid_calls:
        before=h.snapshot(); h.expect_error(call,codes=('SUBSET_LEASE_LOST',))
        unchanged(h,before,'stale/null lease modified persisted state')
    h.attempt(f,c2,obs=post); h.retry(c2); h.ready_retry(c2); c3=h.claim()[0]
    require(c3['job_id']==c1['job_id'] and c3['attempt_count']==3,'third attempt mapping invalid')
    h.attempt(f,c3)
    before=h.snapshot()
    h.expect_error(lambda:h.q('worker','SELECT * FROM video_queue.retry_job(%s,%s,%s,%s,4,1)',(c3['job_id'],c3['lease_token'],WORKER,'UV_RETRY')),states=('42501',))
    unchanged(h,before,'revoked raw retry path remained accessible')
    # Deliberately grant the historical path to the scoped test login: the
    # permanent guard still must reject max=4, independently of EXECUTE ACLs.
    h.q('admin','GRANT EXECUTE ON FUNCTION video_queue.retry_job(uuid,uuid,text,text,integer,integer) TO fixedsubset_worker')
    h.expect_error(lambda:h.q('worker','SELECT * FROM video_queue.retry_job(%s,%s,%s,%s,4,1)',(c3['job_id'],c3['lease_token'],WORKER,'UV_RETRY')),codes=('SUBSET_',))
    unchanged(h,before,'raw max=4 requeued an exhausted managed job')
    h.q('admin','REVOKE EXECUTE ON FUNCTION video_queue.retry_job(uuid,uuid,text,text,integer,integer) FROM fixedsubset_worker')
    h.q('admin','GRANT SELECT,UPDATE ON video_queue.job TO fixedsubset_worker')
    h.expect_error(lambda:h.q('worker','UPDATE video_queue.job SET attempt_count=0 WHERE job_id=%s',(c3['job_id'],)),codes=('SUBSET_',))
    unchanged(h,before,'attempt counter reset accepted')
    h.q('admin','REVOKE SELECT,UPDATE ON video_queue.job FROM fixedsubset_worker')
    ended=h.retry(c3); require(ended['job_status']=='FAILED','fixed retry wrapper did not terminate attempt3')
    require(h.claim()==[],'fourth attempt claimed')
    require(h.recover(f)['binding']==b,'retry modified immutable admission')
    require(h.scalar('admin','SELECT count(*) FROM video_queue.source_reservation')==2,'retry released reservations')
    # Both historical impossible QUEUED-at-3 and crashed third lease must reach
    # FAILED; neither may starve a later eligible independent admission.
    for state in ('QUEUED','LEASED'):
        f,b,c=ordinary(h)
        h.fixture_change('job',"UPDATE video_queue.job SET attempt_count=3,status=%s,lease_owner=CASE WHEN %s='LEASED' THEN lease_owner END,lease_token=CASE WHEN %s='LEASED' THEN lease_token END,lease_expires_at=CASE WHEN %s='LEASED' THEN clock_timestamp()-interval '1 second' END WHERE job_id=%s",(state,state,state,state,c['job_id']))
        good=h.fixture((5,6),runtime=True); good_binding=h.admit(good)
        claimed=h.claim(); require(len(claimed)==1 and str(claimed[0]['batch_id'])==good_binding['batch_id'],
                                   'exhausted row starved eligible work')
        row=h.one('admin','SELECT status,attempt_count,error_code FROM video_queue.job WHERE job_id=%s',(c['job_id'],))
        require(row['status']=='FAILED' and row['attempt_count']==3 and row['error_code']=='UV_WORKER_CRASH_RETRY_EXHAUSTED',
                'exhausted job not terminalized at three')
        require(h.recover(f)['binding']==b,'exhaustion changed binding')


def a01(h):
    # Same approval row lock determines ordering; actual revoke calls hold it.
    for revoke_first in (True,False):
        h.reset(); f=h.fixture()
        if revoke_first:
            h.q('approver','BEGIN'); h.q('approver','SELECT video_queue.revoke_subset_approval(%s)',(f['approval_id'],))
            before=h.snapshot(); pending=h.submit(lambda:h.admit(f)); h.blocked('admit1','approver')
            h.q('approver','COMMIT')
            h.expect_error(lambda:pending.result(10),codes=('SUBSET_APPROVAL_NOT_ACTIVE',))
            after=h.snapshot()
            for table in ('batch','job','job_event','source_observation','subset_admission','source_reservation'):
                require(after[table]==before[table],'revocation-first admission left partial state')
        else:
            h.q('admit1','BEGIN'); b=h.admit(f)
            pending=h.submit(lambda:h.q('approver','SELECT video_queue.revoke_subset_approval(%s)',(f['approval_id'],)))
            h.blocked('approver','admit1'); h.q('admit1','COMMIT'); pending.result(10)
            require(h.recover(f)['binding']==b and h.claim()==[],'post-admission revocation lost binding or allowed work')
    for revoke_first in (True,False):
        f,b,c=ordinary(h); obs=h.observe(f,claim=c)
        if revoke_first:
            h.q('approver','BEGIN'); h.q('approver','SELECT video_queue.revoke_subset_approval(%s)',(f['approval_id'],))
            pending=h.submit(lambda:h.attempt(f,c,obs=obs)); h.blocked('worker','approver')
            h.q('approver','COMMIT'); h.expect_error(lambda:pending.result(10),codes=('SUBSET_RUNTIME_HOLD','SUBSET_APPROVAL_NOT_ACTIVE'))
            require(h.scalar('admin','SELECT consumed_at IS NULL FROM video_queue.source_observation WHERE observation_id=%s',(obs,)),
                    'revoked attempt consumed observation')
        else:
            h.q('worker','BEGIN'); h.attempt(f,c,obs=obs)
            pending=h.submit(lambda:h.q('approver','SELECT video_queue.revoke_subset_approval(%s)',(f['approval_id'],)))
            h.blocked('approver','worker'); h.q('worker','COMMIT'); pending.result(10)
            h.expect_error(lambda:h.q('worker','SELECT video_queue.heartbeat_subset_job(%s,%s,%s,900)',(c['job_id'],c['lease_token'],WORKER)),codes=('SUBSET_RUNTIME_HOLD','SUBSET_APPROVAL_NOT_ACTIVE'))
        require(h.recover(f)['binding']==b,'revocation mutated immutable binding')


def t01(h):
    f,b,c=ordinary(h); good=h.output(c)
    no_pre=h.observe(f,claim=c); before=h.snapshot()
    h.expect_error(lambda:h.q('worker','SELECT * FROM video_queue.finish_subset_job(%s,%s,%s,%s,%s::jsonb,%s,NULL)',
        (c['job_id'],c['lease_token'],WORKER,'REVIEW_READY',good,no_pre)),codes=('SUBSET_PREWORK_OBSERVATION_REQUIRED',))
    unchanged(h,before,'finish without pre-work evidence mutated state')
    h.attempt(f,c)  # Distinct, committed pre-work evidence.
    plan=json.loads(PLAN_PATH.read_text(encoding='utf-8'))['cases'][-1]
    variants=plan['malformed_variants']+plan['optional_0059_variants']
    require(len(variants)==87,'terminal variant coverage drift')
    hashes="SELECT encode(public.digest(convert_to(video_queue.canonical_json_text(%s::jsonb->'artifact_manifest'),'UTF8'),'sha256'),'hex')=%s AND encode(public.digest(convert_to(video_queue.canonical_json_text((%s::jsonb->'terminal_receipt')-'evidence_sha256'),'UTF8'),'sha256'),'hex')=%s"
    require(h.scalar('admin',hashes,(good,good['artifact_manifest_sha256'],good,good['terminal_evidence_sha256'])),
            'fixture hashes disagree with actual SQL canonicalizer')
    for number,v in enumerate(variants):
        vid=v.get('id','0059_size_'+str(number))
        # The frozen plan is the reviewed local specification; evaluate its
        # mutation with PostgreSQL JSONB, then recompute with SQL canonicalizer.
        expression=v['mutation_sql']
        require(set(re.findall(r'\$\d+',expression))=={'$4'} and ';' not in expression,
                'unexpected mutation statement shape')
        count=expression.count('$4'); statement='SELECT '+expression.replace('$4','%s')
        bad=h.scalar('admin',statement,(good,)*count)
        if v['rehash']:
            manifest_sha=h.scalar('admin',"SELECT encode(public.digest(convert_to(video_queue.canonical_json_text(%s::jsonb),'UTF8'),'sha256'),'hex')",(bad['artifact_manifest'],))
            bad['artifact_manifest_sha256']=manifest_sha
            bad['terminal_receipt']['artifact_manifest_sha256']=manifest_sha
            receipt_core={k:x for k,x in bad['terminal_receipt'].items() if k!='evidence_sha256'}
            receipt_sha=h.scalar('admin',"SELECT encode(public.digest(convert_to(video_queue.canonical_json_text(%s::jsonb),'UTF8'),'sha256'),'hex')",(receipt_core,))
            bad['terminal_evidence_sha256']=bad['terminal_receipt']['evidence_sha256']=receipt_sha
            require(h.scalar('admin',hashes,(bad,manifest_sha,bad,receipt_sha)),vid+': rehash failed')
        obs=h.observe(f,claim=c); before=h.snapshot()
        h.q('worker','BEGIN'); h.q('worker','SAVEPOINT bad_terminal')
        expected=h.expect_error(lambda:h.q('worker','SELECT * FROM video_queue.finish_subset_job(%s,%s,%s,%s,%s::jsonb,%s,NULL)',
            (c['job_id'],c['lease_token'],WORKER,'REVIEW_READY',bad,obs)),
            codes=(v['expected_error'],) if 'expected_error' in v else (),
            states=(v['expected_sqlstate'],) if 'expected_sqlstate' in v else (),constraint=v.get('expected_constraint'))
        h.q('worker','ROLLBACK TO SAVEPOINT bad_terminal'); h.q('worker','COMMIT')
        unchanged(h,before,vid+': rejected finish changed state or consumed post-work observation')
        h.log.append({'terminal_variant':vid,'status':'PASS','expected_rejection':expected,'semantic_rehash':v['rehash']})
    result=h.finish(f,c,good)
    require(result=={'job_status':'REVIEW_READY','batch_status':'CANARY_REVIEW','released_jobs':0},'canary did not stop at review gate')
    require(h.claim()==[] and h.recover(f)['binding']==b,'canary released remainder or changed binding')
    require(h.scalar('admin',"SELECT count(*) FROM video_queue.job WHERE status='PENDING_CANARY'")==1,'remainder not held')
    # One-source approval must still retain the human review hold.
    h.reset(); f=h.fixture((1,),runtime=True); b=h.admit(f); c=h.claim()[0]
    require(h.finish(f,c)=={'job_status':'REVIEW_READY','batch_status':'CANARY_REVIEW','released_jobs':0},'one-source canary bypassed hold')
    require(h.claim()==[] and h.recover(f)['binding']==b,'one-source recovery/release failed')


CASES=dict(zip(CASE_IDS,(c01,c02,c03,c04,c05,f01,f04,f05,l01,l02,l03,l04,
                       g01,g02,g03,g04,g05,r01,r03,r04,s01,s07,s08,s09,s10,
                       w01,w02,a01,t01),strict=True))


def save_json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--list-cases',action='store_true',help='offline case IDs; no environment/driver/connection')
    mode.add_argument('--collect-only',action='store_true',help='offline JSON collection, every SQL status NOT_RUN')
    mode.add_argument('--execute',action='store_true',help='apply migrations and run in explicit disposable PG16 only')
    parser.add_argument('--dsn',help='required only for --execute; exact documented synthetic loopback URL')
    parser.add_argument('--evidence-dir',type=Path,help='required for --execute; writes per-case logs and final results')
    args=parser.parse_args(argv)
    cases=collect()
    if not args.execute:
        if args.dsn or args.evidence_dir: parser.error('offline modes accept neither DSN nor evidence destination')
        if args.list_cases:
            for case in cases: print(case['id'])
        else:
            print(json.dumps({'schema':'fixed-subset-postgres-collection/v1','status':'NOT_RUN',
                              'sql_executed':False,'case_count':len(cases),'cases':cases},indent=2))
        return 0
    if not args.dsn or not args.evidence_dir: parser.error('--execute requires --dsn and --evidence-dir')
    try:
        config=explicit_dsn(args.dsn)
        # This lookup is deliberately execution-only. No libpq environment
        # fallback may redirect credentials, service options or authentication.
        import os
        forbidden=[key for key in os.environ if key.startswith('PG') or key=='DATABASE_URL']
        require(not forbidden,'remove database environment variables before execution: '+','.join(sorted(forbidden)))
    except (AssertionError,ValueError) as exc:
        parser.error(str(exc))
    evidence=args.evidence_dir.resolve()
    evidence.mkdir(parents=True,exist_ok=True)
    require(not (evidence/'results.json').exists(),'evidence directory already contains a run; use a new directory')
    report={'schema':'fixed-subset-postgres-execution/v1','status':'NOT_RUN','sql_executed':False,
            'case_count':29,'cases':cases,'started_at':datetime.now(timezone.utc).isoformat(),
            'endpoint':{'host':'127.0.0.1','port':5432,'database':'fixedsubset_test'},
            'limits':{'max_connections':8,'parallel_test_cases':1},
            'source_hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (PLAN_PATH,FIXTURE_PATH,HERE/'run.py',HERE/'helpers.sql')},
            'limitations':['Freshness is checked at the final locked authorization point, not guaranteed at every COMMIT.',
                           'This disposable run makes no statement about any production database or remote source.']}
    save_json(evidence/'results.json',report)
    harness=None
    try:
        sys.path.insert(0,str(ROOT))
        harness=Harness(config,evidence)
        report['sql_execution_attempted']=True
        harness.start()
        report.update(status='RUNNING',sql_executed=True,server=harness.server)
        paths=[ROOT/'database/migrations'/name for name in BASE_MIGRATIONS]+[harness.migration]
        report['migration_expanded_hashes']={str(p.relative_to(ROOT)):hashlib.sha256(migration_text(p).encode()).hexdigest() for p in paths}
        save_json(evidence/'setup.json',harness.log); harness.log=[]
        for record in cases:
            harness.case_id=record['id']; began=time.monotonic(); harness.log=[]
            record.update(status='RUNNING',sql_executed=True)
            try:
                CASES[record['id']](harness)
                record.update(status='PASS',finished_server_clock=str(harness.scalar('admin','SELECT clock_timestamp()')))
                save_json(evidence/(record['id']+'.state.json'),harness.snapshot())
            except Exception as exc:
                record.update(status='FAIL',error_type=type(exc).__name__,error=str(exc))
                raise
            finally:
                record['elapsed_seconds']=round(time.monotonic()-began,3)
                save_json(evidence/(record['id']+'.sql.json'),harness.log)
                save_json(evidence/'results.json',report)
                print(record['id']+': '+record['status'],flush=True)
        require(len(cases)==29 and all(c['status']=='PASS' for c in cases),'incomplete execution cannot pass')
        report['status']='PASS'
    except Exception as exc:
        report.update(status='FAIL',error_type=type(exc).__name__,error=str(exc))
        if harness is not None:
            report['sql_executed']=report['sql_executed'] or bool(harness.log)
        for record in cases:
            if record['status']=='NOT_RUN': record['reason']='run stopped after setup/case failure; no reset conceals failing state'
        if harness is not None and not any(c['status']=='FAIL' for c in cases):
            save_json(evidence/'setup-failure.sql.json',harness.log)
    finally:
        if harness is not None:
            try: harness.close()
            except Exception as exc:
                report.update(status='FAIL',cleanup_error=type(exc).__name__+': '+str(exc))
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        save_json(evidence/'results.json',report)
    print(json.dumps({'status':report['status'],'passed':sum(c['status']=='PASS' for c in cases),
                      'failed':sum(c['status']=='FAIL' for c in cases),'not_run':sum(c['status']=='NOT_RUN' for c in cases),
                      'results':str(evidence/'results.json')}))
    return 0 if report['status']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
