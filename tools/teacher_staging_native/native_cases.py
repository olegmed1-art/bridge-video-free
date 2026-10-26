"""Future real-PG synthetic qualification. Import/default mode never connects.

This is test-only client wiring, not a project provider/issuer or live lesson.
"""
from pathlib import Path
from contextlib import contextmanager
import argparse,copy,hashlib,json,os,sys,tempfile,time,uuid
HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE/'source'),str(HERE)]
import staging_runner as runner
from native_fixture import create,encode,digest
from prerequisites import (load_contract,validate_writer_prerequisites,PrerequisiteMissing,
    safe_sql_diagnostic,safe_failure_code,safe_failure_type,writer_failure_summary)

class NativeAdapter:
    evidence_class='AUTHORIZED_CALLER_CONNECTION'  # Actual disposable PG connection, never project authority.
    def __init__(self,*,abort=False,ack_lost=False,isolation='repeatable read',same_identity=False):
        self.outcomes={};self.pids={};self.target=None;self.abort=abort;self.ack_lost=ack_lost
        self.isolation=isolation;self.same_identity=same_identity;self.write_pid=None
        self.abort_reached=False;self.ack_suppressed=False
        self.schema_contract=load_contract();self.last_sql_diagnostic=None
    def connect(self):
        import psycopg
        c=psycopg.connect(host='/var/run/postgresql',port=5432,dbname='teacher0332',user='postgres',
            passfile='/dev/null',password='',connect_timeout=2,autocommit=True,
            application_name='SOURCE_ONLY_NATIVE',options='-csearch_path=pg_catalog,public,bidding -cstatement_timeout=3000 -clock_timeout=1000')
        try:
            value=c.execute("SELECT current_database(),current_setting('server_version_num')::int,inet_server_addr() IS NULL,"
                "current_user,session_user,(SELECT oid::text FROM pg_database WHERE datname=current_database()),"
                "(pg_control_system()).system_identifier::text,pg_backend_pid(),split_part(pg_read_file('postmaster.pid',0,64),E'\\n',1)::int").fetchone()
            assert value[:5]==('teacher0332',180006,True,'postgres','postgres'),'EXACT_NATIVE_TARGET'
            assert c.info.host=='/var/run/postgresql' and value[7]>0 and value[8]==1,'SOCKET_ONLY_FINAL_POSTMASTER'
            expected=json.loads(os.environ['QUALIFICATION_TARGET_JSON'])
            assert (value[5],value[6])==(expected['database_oid'],expected['system_identifier']),'HOST_SERVER_IDENTITY_MISMATCH'
            target=value[:7]
            if self.target is None:self.target=target
            assert self.target==target,'NATIVE_TARGET_CHANGED'
            self.pids[id(c)]=value[7]
            return c
        except BaseException:c.close();raise
    def begin_attempt(self,nonce):
        assert nonce not in self.outcomes;self.outcomes[nonce]='UNKNOWN'
    def outcome_for(self,nonce):return self.outcomes.get(nonce,'UNKNOWN')
    def connection_identity(self,c):return self.write_pid if self.same_identity else self.pids[id(c)]
    @contextmanager
    def write_transaction(self,nonce):
        import psycopg
        c=self.connect();self.write_pid=self.pids[id(c)]
        transaction=c.transaction();self.phase='BEGIN_REQUESTED'
        try:
            transaction.__enter__();self.phase='BODY'
            try:
                yield c
                if self.abort:
                    self.abort_reached=True;raise RuntimeError('SOURCE_ONLY_BEFORE_COMMIT_ABORT')
            except BaseException as exc:
                self.last_sql_diagnostic=safe_sql_diagnostic(exc,self.schema_contract)
                self.phase='ROLLBACK_REQUESTED'
                suppressed=transaction.__exit__(*sys.exc_info())
                check(not suppressed and not c.closed and c.info.transaction_status==psycopg.pq.TransactionStatus.IDLE,'ROLLBACK_ACK_NOT_CONFIRMED')
                self.phase='ROLLBACK_RETURNED';self.outcomes[nonce]='ROLLED_BACK'
                raise
            self.phase='COMMIT_REQUESTED'
            transaction.__exit__(None,None,None)
            self.phase='COMMIT_RETURNED'
            self.outcomes[nonce]='UNKNOWN' if self.ack_lost else 'COMMITTED'
            if self.ack_lost:
                self.ack_suppressed=True;raise RuntimeError('SOURCE_ONLY_SUPPRESSED_COMMIT_ACK')
        finally:c.close()
    @contextmanager
    def read_transaction(self,nonce):
        c=self.connect()
        try:
            with c.transaction():
                c.execute('SET TRANSACTION ISOLATION LEVEL '+self.isolation.upper()+' READ ONLY')
                yield c
        finally:c.close()

def check(ok,code):
    if not ok:raise AssertionError(code)

class Cases:
    def __init__(self):
        self.a=NativeAdapter();self.results=[];self.fixture_dir=tempfile.TemporaryDirectory(prefix='SOURCE_ONLY_',dir='/tmp')
        self.p,self.proposal,self.scope,self.pins=create(Path(self.fixture_dir.name)/'base',runner)
        self.cid=None;self.output=None
        self.prerequisite_report=None;self.writer_failure=None
    def connection(self):return self.a.connect()
    def q(self,c,sql,args=()):return c.execute(sql,args).fetchall()
    def census(self,c):
        from write_bounds import CORE_TABLES
        names=sorted(set(CORE_TABLES)|{'source_identity','analysis_candidate','knowledge_item','knowledge_version','canon_activation',
            'bidding.video_canon_ai_verification_bundle','bidding.video_canon_ai_verification'})
        return {n:c.execute('SELECT count(*) FROM '+n).fetchone()[0] for n in names}
    def canon_zero(self,c):
        for n in ('knowledge_item','knowledge_version','canon_activation','bidding.video_canon_ai_verification_bundle','bidding.video_canon_ai_verification','bidding.video_definition_store_binding','bridge_video_evidence_gate'):
            check(c.execute('SELECT count(*) FROM '+n).fetchone()[0]==0,'GENUINE_OR_CANON_ROWS:'+n)
    def read(self,c=None):
        if c is not None and c.info.transaction_status and c.execute('SHOW transaction_read_only').fetchone()[0]=='on':
            return runner.module('database.video_staging_read').read_internal_school_video_candidate(c,self.cid)
        with self.a.read_transaction('SOURCE_ONLY_FRESH_READ') as actual:
            return runner.module('database.video_staging_read').read_internal_school_video_candidate(actual,self.cid)
    def selected(self,c,school=None):
        from database.definition_initial_postgres import InitialTransaction
        sid=school or c.execute('SELECT school_id::text FROM school WHERE stable_name=%s',(self.scope['school_key'],)).fetchone()[0]
        t=InitialTransaction(None,c,None,dict(scope=dict(school=sid),candidate_hash=digest(self.proposal)),True)
        return t.candidate(dict(stable_key=self.proposal['knowledge_item']['stable_key'],candidate_payload_hash=digest(self.proposal),scope=self.scope))
    @contextmanager
    def mutation(self):
        # Reader must observe committed faults from a genuinely READ ONLY
        # backend. Capture exact small fixture preimages, commit each fault,
        # then restore only those fixture rows; this is not an atomic restore
        # claim. Atomic writer rollback is tested separately in N06.
        import psycopg
        from psycopg.types.json import Jsonb
        c=self.connection()
        tables={'analysis_candidate':'analysis_candidate_id','artifact_version':'artifact_version_id','source':'source_id',
            'analysis_run':'analysis_run_id','transcript':'transcript_id','transcript_segment':'transcript_segment_id'}
        def observe(table,key,cols,oids):
            rows={r[cols.index(key)]:copy.deepcopy(r) for r in c.execute('SELECT * FROM '+table).fetchall()}
            check(len(rows)<100,'BOUNDED_SYNTHETIC_FIXTURE_ROWS')
            indices=[i for i,t in enumerate(oids) if t in (114,3802)]
            flags={pk:{} for pk in rows}
            if indices:
                query=psycopg.sql.SQL('SELECT {},{} FROM {}').format(psycopg.sql.Identifier(key),
                    psycopg.sql.SQL(',').join(psycopg.sql.SQL('{} IS NULL').format(psycopg.sql.Identifier(cols[i])) for i in indices),psycopg.sql.Identifier(table))
                flags={r[0]:dict(zip(indices,r[1:])) for r in c.execute(query).fetchall()}
            return rows,flags
        def equal(i,a,b,af,bf):
            if i in af:return af[i]==bf[i] and (af[i] or encode(a)==encode(b))
            return type(a) is type(b) and a==b
        try:
            snapshots={}
            for table,key in tables.items():
                cur=c.execute('SELECT * FROM '+table);cols=[x.name for x in cur.description];oids=[x.type_code for x in cur.description]
                rows,flags=observe(table,key,cols,oids);snapshots[table]=(key,cols,oids,rows,flags)
            try:yield c
            finally:
                with c.transaction():
                    for table,(key,cols,oids,old,oldflags) in reversed(list(snapshots.items())):
                        current,currentflags=observe(table,key,cols,oids)
                        for pk in set(current)-set(old):c.execute('DELETE FROM '+table+' WHERE '+key+'=%s',(pk,))
                        for pk,row in old.items():
                            check(pk in current,'FIXTURE_ROW_WAS_DELETED')
                            changed=[i for i in range(len(cols)) if not equal(i,current[pk][i],row[i],currentflags[pk],oldflags[pk])]
                            if changed:
                                values=[None if oldflags[pk].get(i) else Jsonb(row[i]) if oids[i] in (114,3802) else row[i] for i in changed]
                                query=psycopg.sql.SQL('UPDATE {} SET {} WHERE {}=%s').format(psycopg.sql.Identifier(table),
                                    psycopg.sql.SQL(',').join(psycopg.sql.SQL('{}=%s').format(psycopg.sql.Identifier(cols[i])) for i in changed),psycopg.sql.Identifier(key))
                                c.execute(query,values+[pk])
                    for table,(key,cols,oids,old,oldflags) in snapshots.items():
                        actual,flags=observe(table,key,cols,oids)
                        check(set(actual)==set(old) and all(equal(i,actual[pk][i],row[i],flags[pk],oldflags[pk]) for pk,row in old.items() for i in range(len(cols))),
                            'FIXTURE_PREIMAGE_NOT_RESTORED:'+table)
        finally:c.close()
    def blocked(self,c,expected=None):
        r=self.read(c);check(r['status']=='BLOCKED','NEGATIVE_READ_ACCEPTED')
        if expected:check(r['gaps']==[expected],'UNEXPECTED_REFUSAL:'+str(r['gaps']))
    def after_case(self):
        with self.connection() as c:self.canon_zero(c)
    def sql_source(self,name):return (HERE/'sql'/name).read_text('utf8')
    def n01(self):
        import psycopg
        check(sys.version_info[:3]==(3,12,15),'CPYTHON_PIN')
        check(psycopg.__version__=='3.3.4' and psycopg.pq.__impl__=='binary','BINARY_DRIVER_PIN')
        with self.connection() as c:self.canon_zero(c)
    def n02(self):
        with self.connection() as c:
            self.prerequisite_report=validate_writer_prerequisites(c,self.a.schema_contract)
            self.canon_zero(c)
            check(c.execute('SELECT count(*) FROM algorithm_version WHERE version_label=%s',('SOURCE_ONLY_NATIVE_STAGING_R4',)).fetchone()[0]==1,'FIXTURE_ALGORITHM')
            check(c.execute("SELECT count(*) FROM algorithm_version WHERE version_label='3.1-free-r25.12-meta' AND status='candidate'").fetchone()[0]==1,'FIXTURE_META_REGISTRATION')
            check(c.execute('SELECT count(*) FROM source').fetchone()[0]==0,'FRESH_FIXTURE_REQUIRED')
    def n03(self):
        r=runner.persist_with_adapter(self.p,self.a);self.writer_failure=writer_failure_summary(r)
        check(r['status']=='COMMITTED_AND_READ_BACK','FULL_CHAIN')
        self.output=r;self.cid=r['writer_outputs']['staging_result']['candidate_ids'][0]
        read=r['writer_outputs']['candidate_readback']
        check(read['stage']=='DEFINITION_RESEARCH' and read['accepted_canon']==[] and read['student_delivery_allowed'] is False,'STAGING_HOLD')
        check(read['payload_sha256']==digest(self.proposal) and read['parent_raw_sha256']==hashlib.sha256(self.p.master_raw).hexdigest(),'EXACT_SOURCE_READBACK')
        check(len(set(self.a.pids.values()))>=2,'DISTINCT_REAL_BACKENDS')
    def n04(self):
        with self.connection() as c:
            check(self.selected(c)['analysis_candidate_id']==self.cid,'NATIVE_SELECTOR')
            scope=c.execute('SELECT bidding.video_definition_scope(%s::uuid,%s::jsonb)',(self.cid,json.dumps(self.scope))).fetchone()[0]
            sid=c.execute('SELECT school_id::text FROM school WHERE stable_name=%s',(self.scope['school_key'],)).fetchone()[0]
            check(scope==dict(school=sid,profile=self.scope['system_profile'],level=self.scope['learner_level'],scope=self.scope['activation_scope']),
                'NATIVE_SCOPE_PROJECTION_MISMATCH')
    def n05(self):
        with self.connection() as c:before=self.census(c)
        r=runner.persist_with_adapter(self.p,NativeAdapter());check(r['status']=='COMMITTED_AND_READ_BACK','EXPLICIT_IDEMPOTENT_TEST')
        with self.connection() as c:check(self.census(c)==before,'IDEMPOTENCY_GROWTH')
    def n06(self):
        p,_,_,_=create(Path(self.fixture_dir.name)/'abort',runner,job='SOURCE_ONLY_ABORT')
        with self.connection() as c:before=self.census(c)
        a=NativeAdapter(abort=True);r=runner.persist_with_adapter(p,a)
        check(a.abort_reached and r['status']=='ROLLED_BACK','INTENDED_ABORT_STAGE_AND_ROLLBACK_ACK')
        with self.connection() as c:check(self.census(c)==before,'PARTIAL_WRITE_SURVIVED')
    def n07(self):
        p,_,_,_=create(Path(self.fixture_dir.name)/'ack',runner,job='SOURCE_ONLY_ACK')
        a=NativeAdapter(ack_lost=True);r=runner.persist_with_adapter(p,a)
        check(a.ack_suppressed and r['status']=='UNKNOWN_COMMIT_OR_ROLLBACK' and not r['automatic_retry_allowed'],'INTENDED_SUPPRESSED_ACK_MUST_BE_UNKNOWN')
        with self.connection() as c:check(c.execute("SELECT count(*) FROM analysis_run WHERE parameters_snapshot->>'job_id'='SOURCE_ONLY_ACK'").fetchone()[0]==1,'INDEPENDENT_COMMITTED_STATE')
    def n08(self):
        for a in (NativeAdapter(isolation='read committed'),NativeAdapter(same_identity=True)):
            r=runner.persist_with_adapter(self.p,a);check(r['status']=='COMMITTED_READBACK_FAILED','ISOLATION_OR_IDENTITY_GUARD')
    def n09(self):
        with self.mutation() as c:
            c.execute("UPDATE analysis_candidate SET payload_hash=%s WHERE analysis_candidate_id=%s",('0'*64,self.cid));self.blocked(c)
            try:self.selected(c)
            except ValueError:pass
            except Exception as e:check(type(e).__name__=='StoreDenied','SELECTOR_EXCEPTION')
            else:raise AssertionError('BAD_HASH_SELECTOR_ACCEPTED')
    def n10(self):
        with self.mutation() as c:
            rid=self.output['writer_outputs']['staging_result']['replay_receipt_id']
            env=c.execute('SELECT payload FROM analysis_candidate WHERE analysis_candidate_id=%s',(rid,)).fetchone()[0]
            replay=env['replay'];replay['definition_staging']['parent_diagnostic_replay_sha256']='0'*64
            replay.pop('replay_sha256');replay['replay_sha256']=digest(replay);h=digest(env)
            c.execute('UPDATE analysis_candidate SET payload=%s::jsonb,payload_hash=%s,stable_key=%s WHERE analysis_candidate_id=%s',(json.dumps(env),h,h,rid))
            self.blocked(c,'DEFINITION_DIAGNOSTIC_PARENT_MISMATCH')
    def n11(self):
        with self.mutation() as c:
            av=self.output['writer_outputs']['persisted_result']['artifact_version_id']
            c.execute("UPDATE artifact_version SET provenance=jsonb_set(provenance,'{master_json_sha256}',to_jsonb(%s::text)) WHERE artifact_version_id=%s",('0'*64,av));self.blocked(c)
    def n12(self):
        from database.definition_contract_store import StoreDenied
        with self.connection() as c:
            try:self.selected(c,school=str(uuid.uuid4()))
            except StoreDenied:pass
            else:raise AssertionError('FOREIGN_SCHOOL_ACCEPTED')
        w=self.output['writer_outputs']['staging_result']
        with self.a.read_transaction('SOURCE_ONLY_FOREIGN_RUN') as c:
            try:runner.module('database.video_staging_receipt_read').read_committed_receipt_on_connection(c,receipt_id=w['replay_receipt_id'],analysis_run_id=str(uuid.uuid4()),source_id=w['source_id'])
            except ValueError as exc:check(str(exc)=='STAGING_RECEIPT_NOT_DURABLE','EXACT_FOREIGN_RUN_REFUSAL')
            else:raise AssertionError('FOREIGN_RUN_RECEIPT_ACCEPTED')
    def n13(self):
        from decimal import Decimal
        clause=" WHERE metadata->>'worker_segment_id'='SOURCE_ONLY_S1' AND transcript_id IN (SELECT transcript_id FROM transcript WHERE source_id=%s)"
        args=(self.output['writer_outputs']['persisted_result']['source_id'],)
        with self.mutation() as c:
            before=c.execute('SELECT start_seconds FROM transcript_segment'+clause,args).fetchall()
            # The existing storage column is numeric(14,3): 1ms is its
            # representable quantum; 1us would silently round back to10.000.
            after=c.execute('UPDATE transcript_segment SET start_seconds=start_seconds+0.001'+clause+' RETURNING start_seconds',args).fetchall()
            check(len(before)==len(after)==1 and after[0][0]-before[0][0]==Decimal('0.001'),'NATIVE_INTERVAL_MUTATION_NOT_STORED')
            self.blocked(c,'DEFINITION_NATIVE_SEGMENT_MISMATCH')
        with self.mutation() as c:
            changed=c.execute("UPDATE transcript_segment SET metadata=jsonb_set(metadata,'{raw_text}','\"SOURCE_ONLY_FORGED\"')"+clause+" RETURNING metadata->>'raw_text'",args).fetchall()
            check(changed==[('SOURCE_ONLY_FORGED',)],'NATIVE_TEXT_MUTATION_NOT_STORED')
            self.blocked(c,'DEFINITION_NATIVE_SEGMENT_MISMATCH')
    def n14(self):
        with self.mutation() as c:
            c.execute("UPDATE transcript_segment SET metadata=jsonb_set(metadata,'{worker_segment_id}','\"SOURCE_ONLY_FOREIGN\"') WHERE metadata->>'worker_segment_id'='SOURCE_ONLY_S1' AND transcript_id IN (SELECT transcript_id FROM transcript WHERE source_id=%s)",(self.output['writer_outputs']['persisted_result']['source_id'],))
            self.blocked(c,'DEFINITION_NATIVE_SEGMENT_NOT_UNIQUE')
        with self.mutation() as c:
            c.execute("INSERT INTO transcript_segment(transcript_segment_id,transcript_id,sequence_no,start_seconds,end_seconds,speaker_label,text,confidence_class,metadata) SELECT %s,transcript_id,sequence_no+10000,start_seconds,end_seconds,speaker_label,text,confidence_class,metadata FROM transcript_segment WHERE metadata->>'worker_segment_id'='SOURCE_ONLY_S1' AND transcript_id IN (SELECT transcript_id FROM transcript WHERE source_id=%s)",(uuid.uuid4(),self.output['writer_outputs']['persisted_result']['source_id']))
            self.blocked(c,'DEFINITION_NATIVE_SEGMENT_NOT_UNIQUE')
    def n15(self):
        with self.mutation() as c:
            c.execute("UPDATE transcript SET provenance=jsonb_set(provenance,'{job_id}','\"SOURCE_ONLY_FOREIGN\"') WHERE source_id=%s",(self.output['writer_outputs']['persisted_result']['source_id'],))
            self.blocked(c,'DEFINITION_NATIVE_SEGMENT_NOT_UNIQUE')
    def n16(self):
        operations=[("UPDATE analysis_candidate SET status='superseded' WHERE analysis_candidate_id=%s",(self.cid,)),
            ("UPDATE source SET status='retired' WHERE source_id=%s",(self.output['writer_outputs']['persisted_result']['source_id'],)),
            ("UPDATE analysis_run SET run_status='failed' WHERE analysis_run_id=%s",(self.output['writer_outputs']['persisted_result']['analysis_run_id'],))]
        for sql,args in operations:
            with self.mutation() as c:c.execute(sql,args);self.blocked(c)
    def clone_candidate(self,c,cid,*,key=None,fingerprint=None):
        from database.video_candidate_persistence import _COLUMNS
        names=[n.strip() for n in _COLUMNS.split(',')]
        row=list(c.execute('SELECT '+_COLUMNS+' FROM analysis_candidate WHERE analysis_candidate_id=%s',(cid,)).fetchone())
        row[0]=uuid.uuid4()
        if key is not None:row[5]=key
        if fingerprint is not None:row[6]=fingerprint
        for i,n in enumerate(names):
            if n in ('payload','evidence_refs','rejection_reasons'):
                from psycopg.types.json import Jsonb
                row[i]=Jsonb(row[i])
        c.execute('INSERT INTO analysis_candidate('+_COLUMNS+') VALUES ('+','.join(['%s']*len(row))+')',row)
    def n17(self):
        from database.definition_contract_store import StoreDenied
        with self.mutation() as c:
            self.clone_candidate(c,self.cid,key=self.proposal['knowledge_item']['stable_key'])
            try:self.selected(c)
            except StoreDenied:pass
            else:raise AssertionError('AMBIGUOUS_K_W_ACCEPTED')
    def n18(self):
        with self.mutation() as c:
            self.clone_candidate(c,self.output['writer_outputs']['staging_result']['replay_receipt_id'],fingerprint='SOURCE_ONLY_DUPLICATE')
            self.blocked(c,'REPLAY_SNAPSHOT_NOT_UNIQUE')
    def n19(self):
        # Current pure reader contract exercised on a real database projection.
        # This SOURCE_ONLY observation is not a project authenticated lease.
        from bridge_contracts.video_teacher_read import read_teacher_video_candidate,_validate
        from unittest.mock import patch
        from datetime import datetime,timezone,timedelta
        observed=[];mod=runner.module('database.video_staging_read')
        def capture(*a,**k):observed.append(copy.deepcopy(a[2]));return _validate(*a,**k)
        with self.connection() as c:
            with c.transaction():
                c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                with patch.object(mod,'_validate',capture):check(self.read(c)['status']=='READABLE_STAGING','NATIVE_PROJECTION')
        now=datetime.now(timezone.utc);data=observed[-1];auth=dict(actor_context_valid=True,teacher_authorized=True,school_id=data['candidate']['school_id'],status='active',revoked=False,checked_at=now,valid_until=now+timedelta(seconds=60))
        class Snapshot:
            def authorization(self):return auth
            def lookup(self,**k):return data
            def revalidate(self,**k):return dict(authorization=auth,data=data)
        class Reader:
            @contextmanager
            def snapshot(self):yield Snapshot()
        data['binding'].update(checked_at=now-timedelta(seconds=61),valid_until=now-timedelta(seconds=1))
        check(read_teacher_video_candidate(self.cid,reader=Reader(),now=now)['gaps']==['READ_BINDING_STALE_OR_REVOKED'],'STALE_BINDING')
    def n20(self):
        up=self.sql_source('up.sql')
        with self.connection() as c:
            c.execute(up);c.execute(up)
            before=c.execute("SELECT proconfig FROM pg_proc WHERE oid='bidding.video_definition_scope(uuid,jsonb)'::regprocedure").fetchone()
            with c.transaction(force_rollback=True):
                c.execute("ALTER FUNCTION bidding.video_definition_scope(uuid,jsonb) SET search_path=pg_catalog")
                start=up.index('\nBEGIN;\n');body=up[:start]+up[start+len('\nBEGIN;'):];check(body.rstrip().endswith('COMMIT;'),'PINNED_SQL_WRAPPER');body=body.rstrip()[:-len('COMMIT;')]
                try:
                    with c.transaction():c.execute(body)
                except Exception as exc:check('DEFINITION_SCOPE_METADATA_DRIFT' in str(exc),'EXACT_METADATA_REFUSAL')
                else:raise AssertionError('METADATA_DRIFT_ACCEPTED')
            check(c.execute("SELECT proconfig FROM pg_proc WHERE oid='bidding.video_definition_scope(uuid,jsonb)'::regprocedure").fetchone()==before,'RESTORE_METADATA_PREIMAGE')
    def n21(self):
        with self.connection() as c:
            count=c.execute('SELECT count(*) FROM analysis_candidate').fetchone()[0]
            c.execute(self.sql_source('down.sql'))
            try:c.execute('SELECT bidding.video_definition_scope(%s::uuid,%s::jsonb)',(self.cid,json.dumps(self.scope)))
            except Exception as exc:check('DEFINITION_NATIVE_CANDIDATE_SCOPE' in str(exc),'OLD_SCOPE_W_REFUSAL')
            else:raise AssertionError('OLD_SCOPE_ACCEPTED_W')
            c.execute(self.sql_source('up.sql'))
            check(c.execute('SELECT count(*) FROM analysis_candidate').fetchone()[0]==count,'ROLLBACK_REWROTE_ROWS')
    def n22(self):
        a=self.connection();b=self.connection()
        try:
            with a.transaction():
                a.execute('LOCK TABLE bidding.video_definition_store_binding IN SHARE MODE')
                started=time.monotonic()
                try:b.execute('LOCK TABLE bidding.video_definition_store_binding IN ACCESS EXCLUSIVE MODE')
                except Exception as exc:check(getattr(exc,'sqlstate',None)=='25P01','LOCK_REQUIRES_TRANSACTION')
                else:raise AssertionError('AUTO_LOCK_ACCEPTED')
                try:
                    with b.transaction():b.execute('LOCK TABLE bidding.video_definition_store_binding IN ACCESS EXCLUSIVE MODE')
                except Exception as exc:check(getattr(exc,'sqlstate',None)=='55P03','EXPECTED_BOUNDED_LOCK_TIMEOUT')
                else:raise AssertionError('CONCURRENT_FENCE_NOT_HELD')
                check(time.monotonic()-started<3,'LOCK_TEST_UNBOUNDED')
        finally:a.close();b.close()
    def n23(self):
        legacy=runner.prepare_from_bundle(Path(self.fixture_dir.name)/'base',**self.pins)
        r=runner.persist_with_adapter(legacy,NativeAdapter());check(r['status']=='COMMITTED_AND_READ_BACK','LEGACY_STAGING_READ')
        from bridge_contracts.card_play_definition_admission import DefinitionAdmissionConsumer
        r=DefinitionAdmissionConsumer(reader=None,scope=self.scope).check(self.proposal,verification_bundle_sha256='0'*64)
        check(r['status']=='ABSTAIN' and r['Canon_checks_closed_by_this_run']==0,'NO_GENUINE_PROVIDER_HOLD')
    def execute(self):
        for number in range(1,24):
            self.writer_failure=None
            try:getattr(self,'n'+str(number).zfill(2))();self.after_case();self.results.append(dict(id='N'+str(number).zfill(2),status='PASS'))
            except BaseException as exc:
                diagnostic=exc.diagnostic if isinstance(exc,PrerequisiteMissing) else self.a.last_sql_diagnostic or safe_sql_diagnostic(exc,self.a.schema_contract)
                self.results.append(dict(id='N'+str(number).zfill(2),status='FAIL',type=safe_failure_type(exc),code=safe_failure_code(exc),
                    prerequisite_diagnostic=diagnostic,writer_outcome=self.writer_failure));break
        return dict(status='PASS_NATIVE_CASES_N01_N23' if len(self.results)==23 and all(r['status']=='PASS' for r in self.results) else 'FAIL_OR_INCOMPLETE',
            cases=self.results,cleanup_case='N24_HOST_PENDING',synthetic_data=True,genuine_checks=0,teacher_ready=False,
            target=list(self.a.target or ()),actual_backend_pids=sorted(set(self.a.pids.values())),
            writer_schema_prerequisites=self.prerequisite_report,
            ack_fault='Controlled client ACK suppression after actual commit; not an induced network failure',
            source_commit=os.environ.get('GITHUB_SHA'))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--execute',action='store_true');args=parser.parse_args()
    runner.verify_vendor(hashlib.sha256((runner.ROOT/'DEPENDENCY_PINS.json').read_bytes()).hexdigest())
    if not args.execute:print(json.dumps(dict(status='SOURCE_PINS_ONLY_NO_SQL',genuine_checks=0)));return 0
    check(sys.platform=='linux' and os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('QUALIFICATION_RUN'), 'FUTURE_CI_ONLY')
    check(not any(k.startswith('PG') or k.endswith('DATABASE_URL') for k in os.environ),'NO_DB_ENV_FALLBACK')
    report=Cases().execute();print(json.dumps(report,ensure_ascii=True,separators=(',',':')))
    return 0 if report['status']=='PASS_NATIVE_CASES_N01_N23' else 1
if __name__=='__main__':raise SystemExit(main())
