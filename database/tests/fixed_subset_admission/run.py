"""Real PostgreSQL 16 integration tests; collection is strictly offline.

No DB import, environment lookup, network, migration or service is performed by
import, --list-cases or --collect-only. Execution requires both --execute and an
explicit credential-bearing loopback URL for the disposable fixedsubset_test DB.
This runner never provisions a server or invokes subprocesses. SQL remains NOT_RUN
until a future authorized invocation records actual case evidence.
"""
from __future__ import annotations

import argparse
from concurrent.futures import Future, wait
from contextlib import contextmanager
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
        self.listener,self.thread=None,None

    def start(self):
        # The harness owns this object before any socket/thread is allocated.
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
        self.stopped.set()
        errors=[]
        if self.listener is not None:
            try: self.listener.close()
            except OSError as exc: errors.append(str(exc))
        for peer in self.sockets:
            try: peer.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            try: peer.close()
            except OSError as exc: errors.append(str(exc))
        threads=([self.thread] if self.thread is not None else [])+self.threads
        deadline=time.monotonic()+6
        for thread in threads:
            if thread.ident is not None: thread.join(max(0,deadline-time.monotonic()))
        require(not any(t.is_alive() for t in threads),
                'relay thread did not stop')
        require(not errors,'relay close failed: '+repr(errors))


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
        self.connections,self.log={},[]
        self.futures,self.threads,self.operation_threads,self.relays=[],[],[],[]
        self.connection_history,self.variants=[],[]
        self.peak_connections=0
        self.peak_backend_connections=0
        self.closing=False
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

    def connect(self, name, *, options=None):
        require(not self.closing,'harness is closing')
        require(name not in self.connections and len(self.connections) < 8, 'connection cap exceeded')
        if 'admin' in self.connections:
            deadline=time.monotonic()+3
            while True:
                live=self.scalar('admin','SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()')
                if live<8: break
                require(time.monotonic()<deadline,'backend connection cap exceeded')
                time.sleep(.05)
            self.peak_backend_connections=max(self.peak_backend_connections,live+1)
        else:
            self.peak_backend_connections=max(self.peak_backend_connections,1)
        if options is None:
            options = dict(self.config)
            if name != 'admin': options.update(self.credentials[name])
        self.connections[name] = self.pg.connect(**options, autocommit=True, row_factory=self.row)
        self.connection_history.append({'session':name,'pid':self.pid(name)})
        self.peak_connections=max(self.peak_connections,len(self.connections))
        self.log.append({'connection_opened':name,'pid':self.pid(name),'live_connections':len(self.connections)})
        return self.connections[name]

    def close_connection(self,name):
        connection=self.connections.get(name)
        if connection is not None:
            connection.close()
            require(connection.closed,'connection did not close: '+name)
            del self.connections[name]

    def bounded(self,call,timeout,label):
        """A diagnostic/cleanup timeout cannot strand a non-daemon Python worker."""
        outcome=[]; done=threading.Event()
        def perform():
            try: outcome.append((True,call()))
            except BaseException as exc: outcome.append((False,exc))
            finally: done.set()
        thread=threading.Thread(target=perform,name='fixedsubset-'+label,daemon=True)
        self.operation_threads.append(thread); thread.start()
        require(done.wait(max(0,timeout)),label+' timed out')
        if not outcome[0][0]: raise outcome[0][1]
        return outcome[0][1]

    @contextmanager
    def variant(self,variant_id,*,unchanged=False):
        require(not any(v['id']==variant_id and v['case']==self.case_id for v in self.variants),
                'duplicate variant identifier: '+variant_id)
        row={'id':variant_id,'case':self.case_id,'status':'RUNNING','sql_start':len(self.log)}
        self.variants.append(row)
        try:
            before=self.snapshot() if unchanged else None
            if unchanged: row['before_sha256']=digest(before)
            yield row
            if unchanged:
                after=self.snapshot(); row['after_sha256']=digest(after)
                require(after==before,'variant changed persisted state: '+variant_id)
            row['status']='PASS'
        except BaseException as exc:
            row.update(status='FAIL',error_type=type(exc).__name__,error=str(exc))
            raise
        finally:
            row['sql_end']=len(self.log)
            self.log.append({'variant':dict(row)})

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
        require(all(f.done() for f in self.futures),'reset with active test statements')
        for name in self.connections:
            self.q(name,'ROLLBACK')
            self.q(name,'SELECT pg_advisory_unlock_all()')
            self.q(name,'UNLISTEN *')
            self.q(name,'RESET ALL')
            self.q(name,"SET statement_timeout='65s'")
            self.q(name,"SET lock_timeout='60s'")
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
        require(not self.closing and sum(not f.done() for f in self.futures)<2,
                'concurrent statement cap exceeded or harness closing')
        future=Future(); self.futures.append(future)
        def run():
            if not future.set_running_or_notify_cancel(): return
            try: future.set_result(call())
            except BaseException as exc: future.set_exception(exc)
        thread=threading.Thread(target=run,name='fixedsubset-statement',daemon=True)
        self.threads.append(thread); thread.start()
        return future

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

    def failure_evidence(self):
        """Best effort bounded capture; the caller retains the original exception."""
        def capture():
            return {'state':self.snapshot(),
                    'activity':self.q('admin',"SELECT pid,state,xact_start,wait_event_type,wait_event FROM pg_stat_activity WHERE datname=current_database()"),
                    'locks':self.q('admin',"SELECT pid,locktype,mode,granted FROM pg_locks WHERE pid IN (SELECT value::int FROM jsonb_array_elements_text(%s::jsonb))",
                                   ([r['pid'] for r in self.connection_history],))}
        return self.bounded(capture,5,'failure-evidence')

    def close(self):
        self.closing=True
        deadline=time.monotonic()+45
        result={'status':'RUNNING','errors':[],'connections':[],'peak_connections':self.peak_connections}
        result['peak_backend_connections']=self.peak_backend_connections
        def attempt(label,call,limit=3):
            if time.monotonic()>=deadline:
                result['errors'].append({'stage':label,'error':'cleanup deadline exhausted'})
                return None
            try: return self.bounded(call,min(limit,max(0,deadline-time.monotonic())),label)
            except BaseException as exc:
                result['errors'].append({'stage':label,'error_type':type(exc).__name__,'error':str(exc)})
        for future in self.futures: future.cancel()
        for name,connection in list(self.connections.items()):
            # psycopg>=3.2 provides bounded cancellation; do not fall back to
            # potentially unbounded legacy cancel() during failure cleanup.
            attempt('cancel-'+name,lambda c=connection:c.cancel_safe(timeout=2))
        wait(self.futures,timeout=min(5,max(0,deadline-time.monotonic())))
        for relay in self.relays: attempt('relay-close',relay.close,7)
        for name in list(self.connections):
            if name=='admin': continue
            attempt('close-'+name,lambda n=name:self.close_connection(n))
            result['connections'].append({'session':name,'closed':name not in self.connections})
        admin=self.connections.get('admin')
        if admin is not None:
            def census():
                self.q('admin','ROLLBACK')
                self.q('admin',"SET statement_timeout='1s'")
                self.q('admin',"SET lock_timeout='1s'")
                self.q('admin','SELECT pg_advisory_unlock_all()')
                self.q('admin','UNLISTEN *')
                # Reuse the administrative slot. No ninth observer is opened.
                until=min(deadline,time.monotonic()+3)
                while True:
                    peers=self.q('admin',"SELECT pid,state,xact_start FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()")
                    locks=self.q('admin',"SELECT pid,locktype,mode FROM pg_locks WHERE locktype='advisory' AND pid IN (SELECT value::int FROM jsonb_array_elements_text(%s::jsonb))",([r['pid'] for r in self.connection_history],))
                    prepared=self.q('admin','SELECT gid FROM pg_prepared_xacts WHERE database=current_database()')
                    if not peers and not locks and not prepared: break
                    require(time.monotonic()<until,'remaining backend/lock/prepared transaction')
                    time.sleep(.05)
                return {'other_backends':peers,'advisory_locks':locks,'prepared_transactions':prepared}
            result['database_census']=attempt('cleanup-census',census,5)
            attempt('close-admin',lambda:self.close_connection('admin'))
            result['connections'].append({'session':'admin','closed':'admin' not in self.connections})
        elif self.connection_history:
            result['errors'].append({'stage':'cleanup-census','error':'admin unavailable; backend cleanup unverified'})
        for thread in self.threads+self.operation_threads:
            if thread.ident is not None:
                thread.join(min(.2,max(0,deadline-time.monotonic())))
        result['pending_futures']=sum(not f.done() for f in self.futures)
        result['alive_threads']=[t.name for t in self.threads+self.operation_threads if t.is_alive()]
        result['remaining_connections']=list(self.connections)
        if result['pending_futures'] or result['alive_threads'] or result['remaining_connections']:
            result['errors'].append({'stage':'resources','error':'cleanup incomplete'})
        result['status']='FAIL' if result['errors'] else 'PASS'
        return result

    def fixture_change(self, table, statement, params=()):
        """Deliberate impossible/history fixture state, never an API acceptance."""
        require(table in {'job','batch','subset_admission','source_reservation','source_observation','subset_approval'}, 'fixture table not allowed')
        self.q('admin','BEGIN')
        try:
            self.q('admin',self.sql.SQL('ALTER TABLE video_queue.{} DISABLE TRIGGER ALL').format(self.sql.Identifier(table)))
            self.q('admin',statement,params)
            self.q('admin',self.sql.SQL('ALTER TABLE video_queue.{} ENABLE TRIGGER ALL').format(self.sql.Identifier(table)))
            self.q('admin','COMMIT')
        except BaseException as primary:
            try:
                self.q('admin','ROLLBACK')
            except BaseException as secondary:
                primary.add_note('fixture rollback also failed: '+type(secondary).__name__+': '+str(secondary))
                self.log.append({'fixture_rollback_error':str(secondary),'error_type':type(secondary).__name__})
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
    h.close_connection('admit1')
    relay=CommitAckDropRelay()
    h.relays.append(relay); relay.start()
    options={**h.config,**h.credentials['admit1'],'port':relay.port}
    h.connect('admit1',options=options)
    h.q('admit1','BEGIN'); tentative=h.admit(f)
    relay.armed.set()
    h.expect_error(lambda:h.q('admit1','COMMIT'))
    require(relay.dropped.wait(3),'COMMIT acknowledgement was not intercepted')
    require(not relay.errors,'wire relay failed: '+repr(relay.errors))
    # An unexpected error above goes straight to harness cleanup. A finally
    # exception must never replace the original test/transport failure.
    h.close_connection('admit1'); relay.close()
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
        with h.variant('L02_COMMIT' if commit_old else 'L02_ROLLBACK'):
            h.reset(managed=False)
            h.q('admin','CREATE SCHEMA admission_test')
            h.q('admin',"CREATE FUNCTION admission_test.legacy_pause() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_advisory_xact_lock(60060,pg_backend_pid()); RETURN NEW; END $$")
            h.q('admin','CREATE TRIGGER legacy_pause BEFORE INSERT ON video_queue.job FOR EACH ROW EXECUTE FUNCTION admission_test.legacy_pause()')
            h.q('admin','SELECT pg_advisory_lock(60060,%s)',(h.pid('legacy'),))
            h.q('legacy','BEGIN'); writer=h.submit(lambda:h.legacy(key='legacy-inflight'))
            h.blocked('legacy','admin')
            # A standalone managed=False reset has no admit2. Reuse its slot only
            # when it exists; every connection still passes through the shared cap.
            h.close_connection('admit2')
            h.connect('cutover',options=h.config)
            migration=h.submit(lambda:h.q('cutover',migration_text(h.migration)))
            h.blocked('cutover','legacy')
            h.q('admin','SELECT pg_advisory_unlock(60060,%s)',(h.pid('legacy'),))
            writer.result(10); h.q('legacy','COMMIT' if commit_old else 'ROLLBACK')
            migration.result(15); h.close_connection('cutover')
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


# Explicit acceptance inventory, independent of the loops that execute probes.
# A missing/duplicate/non-PASS variant prevents its case from receiving PASS.
REQUIRED_VARIANTS = {
    'G01_DIRECT_DML_GUARD': (
        'G01_unbound_batch',
        'G01_missing_reservation',
        'G01_wrong_reservation_owner',
        'G01_missing_observation',
        'G01_complete_untrusted_fields',
        'G01_invented_batch_binding',
    ),
    'G02_IMMUTABLE_IDENTITY_MUTATIONS': (
        'G02_job_job_id',
        'G02_job_batch_id',
        'G02_job_sequence',
        'G02_job_source_file_id',
        'G02_job_source_name',
        'G02_job_source_mime_type',
        'G02_job_source_size_bytes',
        'G02_job_source_checksum',
        'G02_job_stable_job_key',
        'G02_job_is_canary',
        'G02_job_created_at',
        'G02_job_delete',
        'G02_batch_batch_id',
        'G02_batch_request_key',
        'G02_batch_source_kind',
        'G02_batch_source_folder_id',
        'G02_batch_output_folder_id',
        'G02_batch_work_folder_id',
        'G02_batch_processing_profile',
        'G02_batch_algorithm_revision',
        'G02_batch_result_mode',
        'G02_batch_inventory_sha256',
        'G02_batch_expected_count',
        'G02_batch_total_size_bytes',
        'G02_batch_canary_source_file_id',
        'G02_batch_canonical_promotion_allowed',
        'G02_batch_database_persistence_allowed',
        'G02_batch_created_at',
        'G02_batch_delete',
        'G02_subset_admission_request_key',
        'G02_subset_admission_approval_id',
        'G02_subset_admission_observation_id',
        'G02_subset_admission_contract_sha256',
        'G02_subset_admission_queue_manifest_sha256',
        'G02_subset_admission_plan_sha256',
        'G02_subset_admission_subset_sha256',
        'G02_subset_admission_state',
        'G02_subset_admission_batch_id',
        'G02_subset_admission_binding',
        'G02_subset_admission_binding_sha256',
        'G02_subset_admission_created_at',
        'G02_subset_admission_finalized_at',
        'G02_subset_admission_delete',
        'G02_source_reservation_source_identity',
        'G02_source_reservation_admission_request_key',
        'G02_source_reservation_source_fingerprint',
        'G02_source_reservation_batch_id',
        'G02_source_reservation_job_id',
        'G02_source_reservation_historical_job_ids',
        'G02_source_reservation_created_at',
        'G02_source_reservation_delete',
        'G02_heartbeat_preserves_binding',
    ),
    'G04_GUC_FORGERY_NOT_AUTHORITY': (
        'G04_extra_source',
        'G04_duplicate_job_identity',
        'G04_foreign_job_identity',
        'G04_foreign_batch_identity',
        'G04_forged_worker_claim',
    ),
    'G05_APPROVAL_FULL_CONTENT_VALIDATION': (
        'G05_canonical_0',
        'G05_canonical_1',
        'G05_canonical_2',
        'G05_canonical_3',
        'G05_canonical_4',
        'G05_canonical_5',
        'G05_canonical_6',
        'G05_numeric_domain_0',
        'G05_numeric_domain_1',
        'G05_numeric_domain_2',
        'G05_intake_missing_files',
        'G05_intake_missing_enqueue_manifest',
        'G05_intake_missing_plan_sha256',
        'G05_intake_flag',
        'G05_intake_manifest_name',
        'G05_intake_manifest_checksum',
        'G05_intake_manifest_order',
        'G05_intake_manifest_sequence',
        'G05_intake_source_fingerprint',
        'G05_intake_source_duplicate',
        'G05_intake_source_removed',
        'G05_intake_source_added',
        'G05_intake_wrong_canary',
        'G05_intake_invalid_plan_anchor',
        'G05_intake_scalar_plan_sha256',
        'G05_intake_scalar_enqueue_manifest_output_folder_id',
        'G05_intake_scalar_enqueue_manifest_work_folder_id',
        'G05_intake_scalar_files_0_parent',
        'G05_intake_scalar_files_0_version',
        'G05_intake_scalar_files_0_modifiedTime',
        'G05_intake_scalar_files_0_name',
        'G05_intake_scalar_files_0_mime_type',
        'G05_intake_cost_fraction',
        'G05_intake_cost_float_integer',
        'G05_intake_cost_exponent',
        'G05_plan_provenance_mismatch',
        'G05_persisted_missing_files',
        'G05_persisted_flag',
        'G05_persisted_manifest_name',
        'G05_persisted_manifest_checksum',
        'G05_persisted_manifest_order',
        'G05_persisted_source_removed',
        'G05_persisted_source_added',
        'G05_persisted_invalid_plan_anchor',
        'G05_persisted_hash_only',
        'G05_persisted_json_bytes_disagree',
        'G05_persisted_plan_pin',
        'G05_persisted_revoked',
    ),
    'S08_OBSERVATION_TRUST_BINDING': (
        'S08_admission_unknown',
        'S08_admission_null',
        'S08_admission_cross_approval',
        'S08_duplicate_nonce_same_approval',
        'S08_collector_missing_source',
        'S08_collector_extra_source',
        'S08_collector_duplicate_source',
        'S08_collector_wrong_parent',
        'S08_collector_trashed',
        'S08_collector_future',
        'S08_collector_expired',
        'S08_untrusted_collector_acl',
        'S08_untrusted_update_acl',
        'S08_untrusted_collector_guard',
        'S08_untrusted_update_guard',
        'S08_admission_scope_contract_sha256',
        'S08_admission_scope_request_key',
        'S08_admission_scope_approval_id',
        'S08_consumed_observation_new_request_replay',
        'S08_attempt_scope_lease_token',
        'S08_attempt_scope_attempt_count',
        'S08_attempt_scope_job_id',
        'S08_attempt_scope_worker_key',
        'S08_attempt_scope_approval_id',
        'S08_attempt_scope_contract_sha256',
        'S08_attempt_scope_request_key',
        'S08_attempt_scope_consumed_at',
        'S08_attempt_missing_source',
        'S08_recovery_observation_independent',
    ),
    'W02_LEASE_RETRY_BUDGET_AND_LIVENESS': (
        'W02_null_token_heartbeat',
        'W02_null_token_retry',
        'W02_null_token_finish',
        'W02_null_token_attempt',
        'W02_null_token_recovery',
        'W02_stale_token_heartbeat',
        'W02_stale_token_retry',
        'W02_stale_token_finish',
        'W02_stale_token_attempt',
        'W02_stale_token_recovery',
        'W02_wrong_owner_heartbeat',
        'W02_wrong_owner_retry',
        'W02_wrong_owner_finish',
        'W02_wrong_owner_attempt',
        'W02_wrong_owner_recovery',
        'W02_expired_token_heartbeat',
        'W02_expired_token_retry',
        'W02_expired_token_finish',
        'W02_expired_token_attempt',
        'W02_expired_token_recovery',
        'W02_wrong_attempt_domain_heartbeat',
        'W02_wrong_attempt_domain_retry',
        'W02_wrong_attempt_domain_finish',
        'W02_wrong_attempt_domain_attempt',
        'W02_wrong_attempt_domain_recovery',
        'W02_wrong_observation_attempt_attempt',
        'W02_wrong_observation_attempt_finish',
        'W02_current_lease_without_observation_heartbeat',
        'W02_current_lease_without_observation_retry',
        'W02_reclaim_second_attempt',
        'W02_claim_third_attempt',
        'W02_raw_max4_acl',
        'W02_raw_max4_guard',
        'W02_attempt_counter_reset',
        'W02_third_retry_terminal',
        'W02_managed_exhausted_QUEUED',
        'W02_managed_exhausted_LEASED',
        'W02_historical_exhausted_QUEUED',
        'W02_historical_exhausted_LEASED',
    ),
}


def grant_guard_probe(h, session, tables):
    """Test DML privilege only; never add a capability or definer membership."""
    require(session in ('admit1','worker','approver'),'unexpected guard probe principal')
    role=h.credentials[session]['user']
    require(h.scalar(session,'SELECT session_user')==role,'guard probe must use genuine LOGIN')
    require(not h.scalar('admin','SELECT rolsuper OR rolbypassrls OR rolcreaterole FROM pg_roles WHERE rolname=%s',(role,)),
            'privileged principal cannot prove runtime guard')
    for table in tables:
        require(table in ('job','batch','subset_admission','source_reservation','subset_approval'),'unexpected guard probe table')
        h.q('admin',h.sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON video_queue.{} TO {}').format(h.sql.Identifier(table),h.sql.Identifier(role)))


def expect_guard(h, call, code):
    error=h.expect_error(call,codes=(code,),states=('P0001',))
    require(error['message']==code,'wrong guard reached: '+error['message'])


def insert_job_probe(h, session, row, **changes):
    candidate={**row,**changes}
    return h.q(session,'INSERT INTO video_queue.job SELECT (jsonb_populate_record(NULL::video_queue.job,%s::jsonb)).*',(candidate,))


def rehash_approval(value, *, fingerprints=False):
    """Rehash affected layers without repairing the semantic mutation itself."""
    value=deepcopy(value)
    if 'files' in value:
        if fingerprints:
            for row in value['files']:
                row['source_fingerprint']=digest({k:v for k,v in row.items() if k not in
                    ('name','trashed','source_fingerprint','duration_ms','estimated_cost_usd')})
        value['subset_sha256']=digest(value['files'])
    if isinstance(value.get('enqueue_manifest'),dict):
        manifest=value['enqueue_manifest']
        if 'files' in manifest: manifest['inventory_sha256']=digest(manifest['files'])
        keys=('plan_sha256','subset_sha256','subset_sequence','processing_profile','algorithm_revision','canary_source_file_id')
        if all(k in value for k in keys): manifest['request_key']='subset-v1:'+digest({k:value[k] for k in keys})
        value['queue_manifest_sha256']=digest(manifest)
    value['contract_sha256']=digest({k:v for k,v in value.items() if k!='contract_sha256'})
    return value


def approval_mutations(source):
    """Pure named inputs; PostgreSQL, not this producer, decides acceptance."""
    variants=[]
    for key in ('files','enqueue_manifest','plan_sha256'):
        value=deepcopy(source); value.pop(key)
        variants.append(('missing_'+key,rehash_approval(value),'SUBSET_CONTRACT_INVALID'))
    for change,code in (
        ('flag','SUBSET_CONTRACT_INVALID'),('manifest_name','SUBSET_QUEUE_PROJECTION_INVALID'),
        ('manifest_checksum','SUBSET_QUEUE_PROJECTION_INVALID'),('manifest_order','SUBSET_QUEUE_PROJECTION_INVALID'),
        ('manifest_sequence','SUBSET_QUEUE_PROJECTION_INVALID'),('source_fingerprint','SUBSET_FINGERPRINT_INVALID'),
        ('source_duplicate','SUBSET_SOURCE_INVALID'),('source_removed','SUBSET_QUEUE_PROJECTION_INVALID'),
        ('source_added','SUBSET_MANIFEST_INVALID'),('wrong_canary','SUBSET_MANIFEST_INVALID'),
        ('invalid_plan_anchor','SUBSET_CONTRACT_INVALID')):
        value=deepcopy(source); files=value['files']; queue=value['enqueue_manifest']['files']
        if change=='flag': value['execution_allowed']=True
        elif change=='manifest_name': queue[0]['name']='Not approved.mp4'
        elif change=='manifest_checksum': queue[0]['checksum']='md5:'+'a'*32
        elif change=='manifest_order': queue.reverse()  # Keep original sequence labels: not another valid exact manifest.
        elif change=='manifest_sequence': queue[0]['sequence']=9
        elif change=='source_fingerprint': files[0]['source_fingerprint']='0'*64
        elif change=='source_duplicate': files.append(deepcopy(files[-1]))
        elif change=='source_removed': files.pop()
        elif change=='source_added':
            extra=deepcopy(files[-1]); extra.update(file_id='privateVideo00000003',source_identity='google_drive:privateVideo00000003')
            files.append(extra)
            value=rehash_approval(value,fingerprints=True)
        elif change=='wrong_canary': value['canary_source_file_id']=value['enqueue_manifest']['canary_source_file_id']='privateVideo00000003'
        else: value['plan_sha256']='invalid-plan-anchor'
        variants.append((change,rehash_approval(value),code))
    for path,replacement,code in (
        (('plan_sha256',),int('1'*64),'SUBSET_CONTRACT_INVALID'),
        (('enqueue_manifest','output_folder_id'),123456789012,'SUBSET_MANIFEST_INVALID'),
        (('enqueue_manifest','work_folder_id'),123456789013,'SUBSET_MANIFEST_INVALID'),
        (('files',0,'parent'),None,'SUBSET_SOURCE_INVALID'),
        (('files',0,'version'),17,'SUBSET_SOURCE_INVALID'),
        (('files',0,'modifiedTime'),20261010,'SUBSET_SOURCE_INVALID'),
        (('files',0,'name'),None,'SUBSET_SOURCE_INVALID'),
        (('files',0,'mime_type'),123,'SUBSET_SOURCE_INVALID')):
        value=deepcopy(source); target=value
        for part in path[:-1]: target=target[part]
        target[path[-1]]=replacement
        variants.append(('scalar_'+'_'.join(map(str,path)),rehash_approval(value,fingerprints=True),code))
    for label,cost,code in (('fraction',1.5,'SUBSET_CANONICAL_INTEGER_DOMAIN'),
                            ('float_integer',1.0,'SUBSET_CANONICAL_INTEGER_DOMAIN'),
                            ('exponent',1e30,'SUBSET_CANONICAL_BYTES_MISMATCH')):
        value=deepcopy(source); value['files'][0]['estimated_cost_usd']=cost
        variants.append(('cost_'+label,rehash_approval(value),code))
    return variants


def worker_probe(h, operation, claim, *, token, owner, observation):
    """No truthy fallback: NULL/stale tokens must reach the real SQL boundary."""
    args=(claim['job_id'],token,owner)
    if operation=='heartbeat': return h.q('worker','SELECT video_queue.heartbeat_subset_job(%s,%s,%s,900)',args)
    if operation=='retry': return h.q('worker','SELECT * FROM video_queue.retry_subset_job(%s,%s,%s,%s,1)',args+('UV_RETRY',))
    if operation=='finish': return h.q('worker','SELECT * FROM video_queue.finish_subset_job(%s,%s,%s,%s,%s::jsonb,%s,NULL)',args+('REVIEW_READY',h.output(claim),observation))
    require(operation=='attempt','unknown worker probe')
    return h.q('worker','SELECT video_queue.authorize_subset_attempt(%s,%s,%s,%s)',args+(observation,))


def g01(h):
    for damage in ('unbound_batch','missing_reservation','wrong_reservation_owner','missing_observation'):
        h.reset(); f=h.fixture(); binding=h.admit(f)
        other=h.fixture((3,4)); other_binding=h.admit(other)
        row=h.scalar('admin','SELECT to_jsonb(j) FROM video_queue.job j WHERE batch_id=%s ORDER BY sequence LIMIT 1',(binding['batch_id'],))
        grant_guard_probe(h,'admit1',('job','subset_admission'))
        if damage=='unbound_batch':
            h.fixture_change('subset_admission','DELETE FROM video_queue.subset_admission WHERE request_key=%s',(f['request_key'],))
        else:
            # A privileged fixture opens the draft state only to reach the
            # intended reservation/observation clause with real runtime guards.
            h.fixture_change('subset_admission',"UPDATE video_queue.subset_admission SET state='DRAFT',batch_id=NULL,binding=NULL,binding_sha256=NULL,finalized_at=NULL WHERE request_key=%s",(f['request_key'],))
            if damage=='missing_reservation':
                h.fixture_change('source_reservation','DELETE FROM video_queue.source_reservation WHERE source_identity=%s',('google_drive:'+row['source_file_id'],))
            elif damage=='wrong_reservation_owner':
                h.fixture_change('source_reservation','UPDATE video_queue.source_reservation SET admission_request_key=%s WHERE source_identity=%s',(other['request_key'],'google_drive:'+row['source_file_id']))
            else:
                h.fixture_change('source_observation','DELETE FROM video_queue.source_observation WHERE observation_id=%s',(f['observation_id'],))
        with h.variant('G01_'+damage,unchanged=True):
            if damage=='missing_observation':
                # Complete job/reservation/binding metadata cannot replace the
                # absent trusted observation; test the deferred final proof.
                expect_guard(h,lambda:h.q('admit1',"UPDATE video_queue.subset_admission SET state='FINALIZED',batch_id=%s,binding=%s::jsonb,binding_sha256=%s,finalized_at=clock_timestamp() WHERE request_key=%s",(binding['batch_id'],binding,binding['binding_sha256'],f['request_key'])),
                    'SUBSET_OBSERVATION_BINDING_HOLD')
            else:
                expect_guard(h,lambda:insert_job_probe(h,'admit1',row,job_id=str(uuid4())), 'SUBSET_LEGACY_INSERT_HOLD')
    h.reset(); f=h.fixture(); binding=h.admit(f); grant_probe(h,('job',))
    row=h.scalar('admin','SELECT to_jsonb(j) FROM video_queue.job j WHERE batch_id=%s ORDER BY sequence LIMIT 1',(binding['batch_id'],))
    with h.variant('G01_complete_untrusted_fields',unchanged=True):
        expect_guard(h,lambda:insert_job_probe(h,'attacker',row,job_id=str(uuid4())), 'SUBSET_CAPABILITY_DENIED')
    grant_guard_probe(h,'admit1',('job',))
    with h.variant('G01_invented_batch_binding',unchanged=True):
        expect_guard(h,lambda:insert_job_probe(h,'admit1',row,job_id=str(uuid4()),batch_id=str(uuid4())), 'SUBSET_LEGACY_INSERT_HOLD')


def g02(h):
    f,b,c=ordinary(h)
    other=h.fixture((3,4)); foreign=h.admit(other)
    grant_guard_probe(h,'worker',('job','batch'))
    grant_guard_probe(h,'admit1',('subset_admission','source_reservation'))
    edits={
        'job':dict(job_id=str(uuid4()),batch_id=foreign['batch_id'],sequence=9,source_file_id='privateVideo00000003',
            source_name='Renamed.mp4',source_mime_type='video/avi',source_size_bytes=2000001,
            source_checksum='md5:'+'a'*32,stable_job_key='0'*32,is_canary=False,created_at='2000-01-01T00:00:00Z'),
        'batch':dict(batch_id=str(uuid4()),request_key='changed-key',source_kind='changed-kind',
            source_folder_id='otherSource000001',output_folder_id='otherOutput000001',work_folder_id='otherWork00000001',
            processing_profile='different_profile',algorithm_revision='other_revision',result_mode='PRODUCTION',
            inventory_sha256='0'*64,expected_count=3,total_size_bytes=6000000,canary_source_file_id='privateVideo00000002',
            canonical_promotion_allowed=True,database_persistence_allowed=True,created_at='2000-01-01T00:00:00Z'),
        'subset_admission':dict(request_key='subset-v1:'+'e'*64,approval_id=other['approval_id'],observation_id=other['observation_id'],
            contract_sha256='0'*64,queue_manifest_sha256='0'*64,plan_sha256='0'*64,subset_sha256='0'*64,
            state='DRAFT',batch_id=foreign['batch_id'],binding={},binding_sha256='0'*64,
            created_at='2000-01-01T00:00:00Z',finalized_at='2000-01-01T00:00:00Z'),
        'source_reservation':dict(source_identity='google_drive:privateVideo00000009',admission_request_key=other['request_key'],
            source_fingerprint='0'*64,batch_id=foreign['batch_id'],job_id=foreign['jobs'][0]['job_id'],
            historical_job_ids=None,created_at='2000-01-01T00:00:00Z'),
    }
    keys={'job':('job_id',c['job_id']),'batch':('batch_id',b['batch_id']),
          'subset_admission':('request_key',f['request_key']),'source_reservation':('source_identity','google_drive:'+c['source_file_id'])}
    for table,fields in edits.items():
        session='worker' if table in ('job','batch') else 'admit1'; key,identity=keys[table]
        for field,value in fields.items():
            # historical_job_ids requires an actual non-null uuid[] change,
            # not the unchanged JSON NULL default and not a JSONB array cast.
            expression='ARRAY[%s::uuid]' if field=='historical_job_ids' else '%s'
            if field=='historical_job_ids': value=c['job_id']
            query=h.sql.SQL('UPDATE video_queue.{} SET {}='+expression+' WHERE {}=%s').format(h.sql.Identifier(table),h.sql.Identifier(field),h.sql.Identifier(key))
            expected='SUBSET_RESERVATION_MISMATCH_HOLD' if table=='source_reservation' else 'SUBSET_IMMUTABLE_HOLD'
            with h.variant('G02_'+table+'_'+field,unchanged=True):
                expect_guard(h,lambda:h.q(session,query,(value,identity)),expected)
        with h.variant('G02_'+table+'_delete',unchanged=True):
            query=h.sql.SQL('DELETE FROM video_queue.{} WHERE {}=%s').format(h.sql.Identifier(table),h.sql.Identifier(key))
            expect_guard(h,lambda:h.q(session,query,(identity,)),'SUBSET_IMMUTABLE_HOLD')
    with h.variant('G02_heartbeat_preserves_binding'):
        worker_probe(h,'heartbeat',c,token=c['lease_token'],owner=WORKER,observation=None)
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
    other=h.fixture((3,4)); foreign=h.admit(other)
    row=h.scalar('admin','SELECT to_jsonb(j) FROM video_queue.job j WHERE batch_id=%s ORDER BY sequence LIMIT 1',(b['batch_id'],))
    h.q('attacker',"SELECT set_config('video_queue.admission_id',%s,false),set_config('video_queue.contract_sha256',%s,false)",(f['approval_id'],f['contract']['contract_sha256']))
    for label,changes in (
        ('extra_source',dict(job_id=str(uuid4()),sequence=3,source_file_id='privateVideo00000009',stable_job_key='0'*32)),
        ('duplicate_job_identity',{}),
        ('foreign_job_identity',dict(job_id=foreign['jobs'][0]['job_id'])),
        ('foreign_batch_identity',dict(job_id=str(uuid4()),batch_id=foreign['batch_id']))):
        with h.variant('G04_'+label,unchanged=True):
            expect_guard(h,lambda:insert_job_probe(h,'attacker',row,**changes),'SUBSET_CAPABILITY_DENIED')
    with h.variant('G04_forged_worker_claim',unchanged=True):
        h.expect_error(lambda:h.claim('attacker'),states=('42501',))


def g05(h):
    h.reset(); f=h.fixture(); source=f['contract']
    vectors=[None,False,0,-9223372036854775808,18446744073709551615,
             {'z':[None,False,True,10**80],'a':'quote=" slash=\\\n\t\r\b\f control=\u0001'},
             {'\U0001f600':'non-BMP \U0001d11e','\u0431':'\u041c\u043e\u0441\u043a\u0432\u0430','\u00e9':'e\u0301','\u4e2d':'\u6587'}]
    for number,vector in enumerate(vectors):
        with h.variant('G05_canonical_'+str(number),unchanged=True):
            encoded=canonical(vector)
            row=h.one('admin','SELECT video_queue.subset_json(%s::jsonb) canonical,video_queue.subset_sha(%s::jsonb) sha256',(encoded,encoded))
            require(row['canonical']==encoded and row['sha256']==digest(vector),'SQL/Python canonical vector mismatch')
    for number,raw in enumerate(('1.5','1.0','1e-1')):
        with h.variant('G05_numeric_domain_'+str(number),unchanged=True):
            expect_guard(h,lambda:h.q('admin','SELECT video_queue.subset_json(%s::jsonb)',(raw,)),'SUBSET_CANONICAL_INTEGER_DOMAIN')
    variants=approval_mutations(source)
    for label,value,code in variants:
        with h.variant('G05_intake_'+label,unchanged=True):
            expect_guard(h,lambda:h.scalar('approver','SELECT video_queue.approve_subset(%s::uuid,%s,%s::jsonb,clock_timestamp()+interval \'1 hour\')',
                (str(uuid4()),canonical(value),{'approved_by':'synthetic','approval_reference':'invalid-full-approval','plan_sha256':value.get('plan_sha256',source['plan_sha256'])})),code)
    changed=deepcopy(source); changed['plan_sha256']='f'*64; changed=rehash_approval(changed)
    with h.variant('G05_plan_provenance_mismatch',unchanged=True):
        # A syntactically valid SHA alone is not an independently pinned plan.
        expect_guard(h,lambda:h.scalar('approver','SELECT video_queue.approve_subset(%s::uuid,%s,%s::jsonb,clock_timestamp()+interval \'1 hour\')',
            (str(uuid4()),canonical(changed),{'approved_by':'synthetic','approval_reference':'wrong-plan-anchor','plan_sha256':source['plan_sha256']})), 'SUBSET_APPROVAL_INVALID')
    persisted={'missing_files','manifest_name','manifest_checksum','manifest_order','source_removed','source_added','invalid_plan_anchor','flag'}
    for label,value,code in variants:
        if label not in persisted: continue
        h.reset(); f=h.fixture()
        request=value['enqueue_manifest']['request_key']
        h.fixture_change('subset_approval','UPDATE video_queue.subset_approval SET contract=%s::jsonb,contract_bytes=%s,contract_sha256=%s,queue_manifest_sha256=%s,subset_sha256=%s,request_key=%s WHERE approval_id=%s',
            (value,canonical(value),value['contract_sha256'],value['queue_manifest_sha256'],value['subset_sha256'],request,f['approval_id']))
        damaged={**f,'contract':value,'request_key':request}
        with h.variant('G05_persisted_'+label,unchanged=True):
            expect_guard(h,lambda:h.admit(damaged),code)
            require(h.recover(damaged)['status']=='NOT_FOUND','malformed persisted approval created a binding')
    for damage in ('hash_only','json_bytes_disagree','plan_pin','revoked'):
        h.reset(); f=h.fixture()
        if damage=='hash_only':
            value={'contract_sha256':f['contract']['contract_sha256'],'queue_manifest_sha256':f['contract']['queue_manifest_sha256']}
            h.fixture_change('subset_approval','UPDATE video_queue.subset_approval SET contract=%s::jsonb,contract_bytes=%s WHERE approval_id=%s',(value,canonical(value),f['approval_id']))
            code='SUBSET_CONTRACT_INVALID'
        elif damage=='json_bytes_disagree':
            value=deepcopy(f['contract']); value['files'][0]['name']='Changed.mp4'; value['enqueue_manifest']['files'][0]['name']='Changed.mp4'
            value=rehash_approval(value)
            h.fixture_change('subset_approval','UPDATE video_queue.subset_approval SET contract=%s::jsonb WHERE approval_id=%s',(value,f['approval_id']))
            code='SUBSET_DIGEST_MISMATCH_HOLD'
        elif damage=='plan_pin':
            h.fixture_change('subset_approval','UPDATE video_queue.subset_approval SET plan_sha256=%s WHERE approval_id=%s',('f'*64,f['approval_id']))
            code='SUBSET_APPROVAL_CONTENT_HOLD'
        else:
            h.q('approver','SELECT video_queue.revoke_subset_approval(%s)',(f['approval_id'],)); code='SUBSET_APPROVAL_NOT_ACTIVE'
        with h.variant('G05_persisted_'+damage,unchanged=True):
            expect_guard(h,lambda:h.admit(f),code)
            require(h.recover(f)['status']=='NOT_FOUND','invalid persisted authority created a binding')


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
    h.reset(); f=h.fixture(); other=h.fixture((3,4))
    for label,oid in (('unknown',str(uuid4())),('null',None),('cross_approval',other['observation_id'])):
        with h.variant('S08_admission_'+label,unchanged=True):
            expect_guard(h,lambda:h.admit(f,observation_id=oid),'SUBSET_OBSERVATION_SCOPE')
    nonce=h.scalar('admin','SELECT nonce FROM video_queue.source_observation WHERE observation_id=%s',(f['observation_id'],))
    with h.variant('S08_duplicate_nonce_same_approval',unchanged=True):
        h.expect_error(lambda:h.scalar('collector','SELECT video_queue.observe_subset(%s,%s,%s,%s::jsonb,clock_timestamp(),%s,NULL,NULL,NULL,NULL)',
            (str(uuid4()),f['approval_id'],'ADMISSION',f['raw'],nonce)),states=('23505',),constraint='source_observation_approval_id_nonce_key')
    raw_cases=[]
    raw_cases.append(('missing_source',deepcopy(f['raw'][:1]),'SUBSET_SOURCE_SET_MISMATCH'))
    raw_cases.append(('extra_source',deepcopy(f['raw']+other['raw'][:1]),'SUBSET_SOURCE_SET_MISMATCH'))
    raw_cases.append(('duplicate_source',[deepcopy(f['raw'][0]),deepcopy(f['raw'][0])],'SUBSET_SOURCE_REVISION_CHANGED'))
    for name,patch in (('wrong_parent',{'parents':['otherFolder000001']}),('trashed',{'trashed':True})):
        raw=deepcopy(f['raw']); raw[0].update(patch); raw_cases.append((name,raw,'SUBSET_SOURCE_REVISION_CHANGED'))
    for label,raw,code in raw_cases:
        with h.variant('S08_collector_'+label,unchanged=True):
            expect_guard(h,lambda:h.observe(f,raw=raw),code)
    for label,age in (('future',-5),('expired',31)):
        with h.variant('S08_collector_'+label,unchanged=True):
            expect_guard(h,lambda:h.observe(f,age=age),'SUBSET_OBSERVATION_EXPIRED')
    h.q('admin','GRANT USAGE ON SCHEMA video_queue TO fixedsubset_attacker')
    with h.variant('S08_untrusted_collector_acl',unchanged=True):
        h.expect_error(lambda:h.observe(f,session='attacker'),states=('42501',))
    with h.variant('S08_untrusted_update_acl',unchanged=True):
        h.expect_error(lambda:h.q('attacker','UPDATE video_queue.source_observation SET observed_at=clock_timestamp(),nonce=gen_random_uuid() WHERE observation_id=%s',(f['observation_id'],)),states=('42501',))
    grant_probe(h,('source_observation',))
    untrusted=h.scalar('admin','SELECT to_jsonb(o) FROM video_queue.source_observation o WHERE observation_id=%s',(f['observation_id'],))
    untrusted.update(observation_id=str(uuid4()),nonce=str(uuid4()),collected_by='fixedsubset_collector')
    h.q('attacker',"SELECT set_config('video_queue.observation_trusted','true',false),set_config('video_queue.collector','fixedsubset_collector',false)")
    with h.variant('S08_untrusted_collector_guard',unchanged=True):
        expect_guard(h,lambda:h.q('attacker','INSERT INTO video_queue.source_observation SELECT (jsonb_populate_record(NULL::video_queue.source_observation,%s::jsonb)).*',(untrusted,)), 'SUBSET_CAPABILITY_DENIED')
    with h.variant('S08_untrusted_update_guard',unchanged=True):
        expect_guard(h,lambda:h.q('attacker','UPDATE video_queue.source_observation SET observed_at=clock_timestamp(),expires_at=clock_timestamp()+interval \'30 seconds\' WHERE observation_id=%s',(f['observation_id'],)), 'SUBSET_IMMUTABLE_HOLD')
    for field,value in (('contract_sha256','0'*64),('request_key',other['request_key']),('approval_id',other['approval_id'])):
        obs=h.observe(f)
        h.fixture_change('source_observation',h.sql.SQL('UPDATE video_queue.source_observation SET {}=%s WHERE observation_id=%s').format(h.sql.Identifier(field)),(value,obs))
        with h.variant('S08_admission_scope_'+field,unchanged=True):
            expect_guard(h,lambda:h.admit(f,observation_id=obs),'SUBSET_OBSERVATION_SCOPE')
    # Refresh original admission evidence only before any attempt to admit it;
    # no failed-observation rollback assertion is hidden by a refresh.
    f['observation_id']=h.observe(f); b=h.admit(f)
    with h.variant('S08_consumed_observation_new_request_replay',unchanged=True):
        expect_guard(h,lambda:h.admit(other,observation_id=f['observation_id']),'SUBSET_OBSERVATION_SCOPE')
        require(h.recover(f)['binding']==b,'nonce replay denial damaged committed recovery')
    h.runtime(f,True); c=h.claim()[0]
    for field,value in (('lease_token',str(uuid4())),('attempt_count',2),('job_id',str(uuid4())),
                        ('worker_key','other-worker'),('approval_id',other['approval_id']),
                        ('contract_sha256','0'*64),('request_key',other['request_key']),('consumed_at','2000-01-01T00:00:00Z')):
        obs=h.observe(f,claim=c)
        h.fixture_change('source_observation',h.sql.SQL('UPDATE video_queue.source_observation SET {}=%s WHERE observation_id=%s').format(h.sql.Identifier(field)),(value,obs))
        with h.variant('S08_attempt_scope_'+field,unchanged=True):
            expect_guard(h,lambda:h.attempt(f,c,obs=obs),'SUBSET_OBSERVATION_SCOPE')
    obs=h.observe(f,claim=c)
    h.fixture_change('source_observation','UPDATE video_queue.source_observation SET sources=%s::jsonb WHERE observation_id=%s',([],obs))
    with h.variant('S08_attempt_missing_source',unchanged=True):
        expect_guard(h,lambda:h.attempt(f,c,obs=obs),'SUBSET_SOURCE_REVISION_CHANGED')
    with h.variant('S08_recovery_observation_independent',unchanged=True):
        require(h.recover(f)['binding']==b,'observation denials changed immutable receipt')


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
    later=h.fixture((50,51),runtime=True); later_binding=h.admit(later,'admit2')
    for phase in ('while_draft_uncommitted','after_commit'):
        before=h.snapshot()
        h.expect_error(lambda:h.claim('legacy'),states=('42501',))
        h.expect_error(lambda:h.q('legacy','SET ROLE video_subset_worker_v2'),states=('42501',))
        h.q('legacy',"SELECT set_config('video_queue.worker_version','v2',false),set_config('video_queue.admission_authorized','true',false)")
        h.expect_error(lambda:h.claim('legacy'),states=('42501',))
        unchanged(h,before,'legacy worker claimed or modified a managed job')
        if phase=='while_draft_uncommitted':
            with h.variant('W01_SKIP_LOCKED_ELIGIBLE'):
                h.q('admin','BEGIN')
                h.q('admin','SELECT job_id FROM video_queue.job WHERE batch_id=%s AND is_canary FOR UPDATE',
                    (binding['batch_id'],))
                # Proves actual SKIP LOCKED behavior, independent of SQL aliases.
                skipped=h.submit(h.claim).result(8)
                require(len(skipped)==1 and str(skipped[0]['batch_id'])==later_binding['batch_id'],
                        'claim blocked or selected held/ineligible work')
                h.q('admin','COMMIT')
                claim=h.claim()
                require(len(claim)==1 and str(claim[0]['batch_id'])==binding['batch_id'],
                        'claim failed to select previously locked eligible work')
                h.verify_binding(eligible,binding); h.verify_binding(later,later_binding)
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
    # Definition hash is provenance only; behavioral assertions above are the oracle.
    h.log.append({'selector_function_sha256':hashlib.sha256(definition.encode()).hexdigest()})


def w02(h):
    operations=('heartbeat','retry','finish','attempt')
    for defect in ('null_token','stale_token','wrong_owner','expired_token','wrong_attempt_domain'):
        f,b,c=ordinary(h); original=c
        if defect=='stale_token':
            h.lease_expire(c); c=h.claim()[0]
            require(c['job_id']==original['job_id'] and c['attempt_count']==2 and c['lease_token']!=original['lease_token'], 'stale-token fixture did not actually rotate a lease')
        h.attempt(f,c); post=h.observe(f,claim=c)
        token=None if defect=='null_token' else original['lease_token'] if defect=='stale_token' else c['lease_token']
        owner='other-worker' if defect=='wrong_owner' else WORKER
        if defect=='expired_token': h.lease_expire(c)
        if defect=='wrong_attempt_domain':
            h.fixture_change('job','UPDATE video_queue.job SET attempt_count=4 WHERE job_id=%s',(c['job_id'],))
        for operation in operations:
            with h.variant('W02_'+defect+'_'+operation,unchanged=True):
                expect_guard(h,lambda:worker_probe(h,operation,c,token=token,owner=owner,observation=post), 'SUBSET_LEASE_LOST')
        with h.variant('W02_'+defect+'_recovery',unchanged=True):
            require(h.recover(f)['binding']==b,'lease denial modified immutable binding')
    # In-range observation-attempt mismatch is meaningful only for APIs that
    # accept an observation. Heartbeat/retry have no claimed attempt parameter.
    f,b,c=ordinary(h); h.attempt(f,c); post=h.observe(f,claim=c)
    h.fixture_change('source_observation','UPDATE video_queue.source_observation SET attempt_count=2 WHERE observation_id=%s',(post,))
    for operation in ('attempt','finish'):
        with h.variant('W02_wrong_observation_attempt_'+operation,unchanged=True):
            expect_guard(h,lambda:worker_probe(h,operation,c,token=c['lease_token'],owner=WORKER,observation=post),'SUBSET_OBSERVATION_SCOPE')
    with h.variant('W02_current_lease_without_observation_heartbeat'):
        worker_probe(h,'heartbeat',c,token=c['lease_token'],owner=WORKER,observation=None)
        require(h.recover(f)['binding']==b,'heartbeat changed binding')
    with h.variant('W02_current_lease_without_observation_retry'):
        require(h.retry(c)['job_status']=='QUEUED','current-lease retry should not require observation')
        require(h.scalar('admin','SELECT consumed_at IS NULL FROM video_queue.source_observation WHERE observation_id=%s',(post,)), 'lease-only APIs consumed a mismatched observation')
    f,b,c1=ordinary(h); h.attempt(f,c1); h.lease_expire(c1)
    with h.variant('W02_reclaim_second_attempt'):
        c2=h.claim()[0]
        require(c2['job_id']==c1['job_id'] and c2['attempt_count']==2 and c2['lease_token']!=c1['lease_token'], 'reclaim did not rotate token/increment existing job')
    h.attempt(f,c2); h.retry(c2); h.ready_retry(c2)
    with h.variant('W02_claim_third_attempt'):
        c3=h.claim()[0]
        require(c3['job_id']==c1['job_id'] and c3['attempt_count']==3 and c3['lease_token'] not in (c1['lease_token'],c2['lease_token']), 'third attempt mapping invalid')
    h.attempt(f,c3)
    with h.variant('W02_raw_max4_acl',unchanged=True):
        h.expect_error(lambda:h.q('worker','SELECT * FROM video_queue.retry_job(%s,%s,%s,%s,4,1)',(c3['job_id'],c3['lease_token'],WORKER,'UV_RETRY')),states=('42501',))
    h.q('admin','GRANT EXECUTE ON FUNCTION video_queue.retry_job(uuid,uuid,text,text,integer,integer) TO fixedsubset_worker')
    with h.variant('W02_raw_max4_guard',unchanged=True):
        expect_guard(h,lambda:h.q('worker','SELECT * FROM video_queue.retry_job(%s,%s,%s,%s,4,1)',(c3['job_id'],c3['lease_token'],WORKER,'UV_RETRY')), 'SUBSET_ATTEMPT_OR_RELEASE_HOLD')
    # On failure the harness owns cleanup; never mask its primary error with
    # a second failing statement in finally.
    h.q('admin','REVOKE EXECUTE ON FUNCTION video_queue.retry_job(uuid,uuid,text,text,integer,integer) FROM fixedsubset_worker')
    grant_guard_probe(h,'worker',('job',))
    with h.variant('W02_attempt_counter_reset',unchanged=True):
        expect_guard(h,lambda:h.q('worker','UPDATE video_queue.job SET attempt_count=0 WHERE job_id=%s',(c3['job_id'],)), 'SUBSET_ATTEMPT_OR_RELEASE_HOLD')
    with h.variant('W02_third_retry_terminal'):
        ended=h.retry(c3)
        require(ended['job_status']=='FAILED' and ended['batch_status']=='CANARY_BLOCKED','fixed retry wrapper did not terminate attempt3')
        require(h.claim()==[],'fourth attempt claimed')
        require(h.recover(f)['binding']==b,'retry modified immutable admission')
        require(h.scalar('admin','SELECT count(*) FROM video_queue.source_reservation')==2,'retry released reservations')
    for state in ('QUEUED','LEASED'):
        f,b,c=ordinary(h)
        h.fixture_change('job',"UPDATE video_queue.job SET attempt_count=3,status=%s,lease_owner=CASE WHEN %s='LEASED' THEN lease_owner END,lease_token=CASE WHEN %s='LEASED' THEN lease_token END,lease_expires_at=CASE WHEN %s='LEASED' THEN clock_timestamp()-interval '1 second' END WHERE job_id=%s",(state,state,state,state,c['job_id']))
        good=h.fixture((5,6),runtime=True); good_binding=h.admit(good)
        with h.variant('W02_managed_exhausted_'+state):
            claimed=h.claim()
            require(len(claimed)==1 and str(claimed[0]['batch_id'])==good_binding['batch_id'],'exhausted row starved eligible work')
            row=h.one('admin','SELECT status,attempt_count,error_code FROM video_queue.job WHERE job_id=%s',(c['job_id'],))
            require(row==dict(status='FAILED',attempt_count=3,error_code='UV_WORKER_CRASH_RETRY_EXHAUSTED'), 'exhausted job not terminalized at three')
            require(h.recover(f)['binding']==b,'exhaustion changed binding')
    for state in ('QUEUED','LEASED'):
        h.reset(managed=False); legacy=h.legacy(numbers=(90,),key='historical-exhausted')
        h.q('admin',"UPDATE video_queue.job SET status=%s,attempt_count=3,lease_owner=CASE WHEN %s='LEASED' THEN %s END,lease_token=CASE WHEN %s='LEASED' THEN %s::uuid END,lease_expires_at=CASE WHEN %s='LEASED' THEN clock_timestamp()-interval '1 second' END WHERE batch_id=%s",(state,state,WORKER,state,str(uuid4()),state,legacy['batch_id']))
        old=h.scalar('admin','SELECT to_jsonb(j) FROM video_queue.job j WHERE batch_id=%s',(legacy['batch_id'],))
        h.apply_managed(); good=h.fixture((5,6),runtime=True); binding=h.admit(good)
        with h.variant('W02_historical_exhausted_'+state):
            claimed=h.claim()
            require(len(claimed)==1 and str(claimed[0]['batch_id'])==binding['batch_id'],'historical exhausted row starved managed work')
            require(h.scalar('admin','SELECT to_jsonb(j) FROM video_queue.job j WHERE job_id=%s',(old['job_id'],))==old, 'unbound history was claimed or rewritten')


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
REQUIRED_VARIANTS.update({
    'L02_PREEXISTING_LEGACY_WRITER_CUTOVER':('L02_COMMIT','L02_ROLLBACK'),
    'W01_LEGACY_WORKER_AND_ELIGIBILITY':('W01_SKIP_LOCKED_ELIGIBLE',),
})


def save_json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')


def remember_error(report,exc,stage):
    item={'stage':stage,'error_type':type(exc).__name__,'error':str(exc)}
    report['status']='FAIL'
    if 'error' not in report:
        report.update(item)
    else:
        report.setdefault('secondary_errors',[]).append(item)


def save_evidence(path,value,report):
    try:
        save_json(path,value)
        return True
    except BaseException as exc:
        remember_error(report,exc,'evidence:'+path.name)
        return False


def run_case(harness,record,evidence,report):
    """Fail fast, retain the original exception and every available evidence item."""
    harness.case_id=record['id']; began=time.monotonic(); harness.log=[]
    record.update(status='RUNNING',sql_executed=True)
    try:
        CASES[record['id']](harness)
        expected=REQUIRED_VARIANTS.get(record['id'],())
        completed=[v for v in harness.variants if v['case']==record['id']]
        require({v['id'] for v in completed}==set(expected)
                and all(v['status']=='PASS' for v in completed),
                'required variant evidence incomplete: '+record['id'])
        clock=harness.scalar('admin','SELECT clock_timestamp()')
        state=harness.snapshot()
        require(save_evidence(evidence/(record['id']+'.state.json'),state,report),'state evidence not saved')
        record.update(status='PASS',finished_server_clock=str(clock))
    except BaseException as exc:
        record.update(status='FAIL',error_type=type(exc).__name__,error=str(exc))
        remember_error(report,exc,'case:'+record['id'])
        # Publish the original failure before attempting any diagnostic query.
        save_evidence(evidence/'results.json',report,report)
        try:
            failure=harness.failure_evidence()
            save_evidence(evidence/(record['id']+'.failure-state.json'),failure,report)
        except BaseException as diagnostic:
            record['failure_capture_error']={'error_type':type(diagnostic).__name__,'error':str(diagnostic)}
    finally:
        record['elapsed_seconds']=round(time.monotonic()-began,3)
        record['variants']=[dict(v) for v in harness.variants if v['case']==record['id']]
        if not save_evidence(evidence/(record['id']+'.sql.json'),harness.log,report):
            record['status']='FAIL'
        if not save_evidence(evidence/'results.json',report,report): record['status']='FAIL'
        print(record['id']+': '+record['status'],flush=True)
    return record['status']=='PASS'


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
            'limits':{'max_connections':8,'connection_scope':'harness LOGIN sessions including admin/cutover/relay',
                      'parallel_test_cases':1,'parallel_test_statements':2,'cleanup_seconds':45},
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
            if not run_case(harness,record,evidence,report): break
        require(len(cases)==29 and all(c['status']=='PASS' for c in cases),'incomplete execution cannot pass')
        report['status']='PASS'
    except BaseException as exc:
        remember_error(report,exc,'execution')
        if harness is not None:
            report['sql_executed']=report['sql_executed'] or bool(harness.log)
        for record in cases:
            if record['status']=='NOT_RUN': record['reason']='run stopped after setup/case failure; no reset conceals failing state'
        if harness is not None and not any(c['status']=='FAIL' for c in cases):
            save_evidence(evidence/'setup-failure.sql.json',harness.log,report)
    finally:
        if harness is not None:
            try:
                report['cleanup']=harness.close()
                if report['cleanup']['status']!='PASS':
                    remember_error(report,RuntimeError('cleanup did not complete'),'cleanup')
            except BaseException as exc:
                remember_error(report,exc,'cleanup')
            save_evidence(evidence/'cleanup.sql.json',harness.log,report)
            save_evidence(evidence/'cleanup.json',report.get('cleanup',{'status':'UNKNOWN'}),report)
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        save_evidence(evidence/'results.json',report,report)
    print(json.dumps({'status':report['status'],'passed':sum(c['status']=='PASS' for c in cases),
                      'failed':sum(c['status']=='FAIL' for c in cases),'not_run':sum(c['status']=='NOT_RUN' for c in cases),
                      'results':str(evidence/'results.json')}))
    return 0 if report['status']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
