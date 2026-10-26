"""SOURCE_ONLY guard-store contract/static checks. No PostgreSQL execution.

Existing pure Canon gate/record functions serve as compatibility oracles; old
test suites are not imported. SQL schema/name/privilege resolution is NOT tested.
"""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import re
import unittest
import jsonschema
from pglast import parse_sql
from pglast.ast import CreateFunctionStmt,CreateStmt,GrantStmt,InsertStmt,UpdateStmt
from bridge_contracts.definition_v01_records import canonical_bytes,parse_canonical,digest,manifest_digest,evaluate_test_state,ContractError
from bridge_contracts.video_canon_ai_promotion import REQUIRED_CHECKS,STATE_DEPENDENT_CHECKS

ROOT=Path(__file__).resolve().parents[1]
MIGRATION=ROOT/'database/migrations/0376_workflow_definition_bundle_store_successor.sql'
ROLLBACK=ROOT/'database/rollbacks/0376_workflow_definition_bundle_store_successor.sql'
BASE=(ROOT.parent/'primary/0329_workflow_video_canon_ai_promotion.sql')
if not BASE.exists():BASE=ROOT/'database/migrations/0329_workflow_video_canon_ai_promotion.sql'
SQL=MIGRATION.read_text(encoding='utf-8');DOWN=ROLLBACK.read_text(encoding='utf-8')
AST=parse_sql(SQL);DOWNAST=parse_sql(DOWN)
sha=lambda text:hashlib.sha256(text.encode()).hexdigest()


def functions(text):
    text='\n'.join(line for line in text.splitlines() if not line.startswith('\\'))
    result={}
    for stmt in parse_sql(text):
        n=stmt.stmt
        if isinstance(n,CreateFunctionStmt):
            result['.'.join(x.sval for x in n.funcname)]={x.defname:x.arg for x in n.options}
    return result


def source(name):
    return functions(SQL)['bidding.'+name]['as'][0].sval


def witness():
    context=dict(evidence_mode='SOURCE_ONLY',school_snapshot_sha256=sha('SOURCE_ONLY:snapshot'),
        source_policy_sha256=sha('SOURCE_ONLY:policy'),membership_sha256=sha('SOURCE_ONLY:members'),
        policy_from_us=0,policy_until_us=10000,candidate_active=True,set_active=True,source_active=True,attestors_active=True)
    scope=dict(school='SOURCE_ONLY:school',profile='SOURCE_ONLY:profile',level='SOURCE_ONLY:level',scope='SOURCE_ONLY:scope')
    cases=[dict(key='SOURCE_ONLY:'+kind,version=1,kind=kind,input_sha256=sha(kind+' input'),
        oracle_sha256=sha(kind+' oracle'),executor_sha256=sha(kind+' executor'),source_binding_sha256=sha(kind+' source'))
        for kind in ['POSITIVE','NEGATIVE','BOUNDARY','INTERFERENCE']]
    manifest=dict(evidence_mode='SOURCE_ONLY',contract='DEFINITION_TEST_STATE_PROPOSAL_0_1',candidate_hash=sha('SOURCE_ONLY:candidate'),
        scope=scope,set_version=1,gate_profile_sha256=sha('SOURCE_ONLY:gate'),max_attempt_us=100,max_age_us=1000,cases=cases)
    mh=manifest_digest(manifest);ch=digest('DEFINITION_DEPENDENCY_CONTEXT_0_1',context)
    attempts=[];outcomes=[]
    for case in cases:
        key=case['key']+':attempt1'
        attempts.append(dict(attempt_key=key,case_key=case['key'],case_version=1,attempt_seq=1,
            candidate_hash=manifest['candidate_hash'],manifest_sha256=mh,context_sha256=ch,started_us=100))
        outcomes.append(dict(attempt_key=key,status='PASS',finished_us=110,observed_sha256=case['oracle_sha256'],
            executor_sha256=case['executor_sha256'],artifact_sha256=sha(key+' artifact')))
    return manifest,attempts,outcomes,context


def frozen_oracle(m,a,o,c,now=200):return evaluate_test_state(m,a,o,c,now)


class SourceTests(unittest.TestCase):
    def test_01_SQL_AST_and_only_two_legacy_functions_replaced(self):
        self.assertGreater(len(AST),50);self.assertGreater(len(DOWNAST),20)
        replacements={'.'.join(x.sval for x in r.stmt.funcname) for r in AST
            if isinstance(r.stmt,CreateFunctionStmt) and r.stmt.replace}
        self.assertEqual(replacements,{'bidding.validate_video_canon_verification_bundle','bidding.validate_video_canon_verification'})
        tables={r.stmt.relation.relname for r in AST if isinstance(r.stmt,CreateStmt)}
        self.assertEqual(tables,{'video_definition_store_control','video_definition_store_binding'})

    def test_02_BIDDING_bodies_and_attributes_exact_with_rollback_restore(self):
        old=functions(BASE.read_text(encoding='utf-8'));new=functions(SQL);down=functions(DOWN)
        for name in ('bidding.validate_video_canon_verification_bundle','bidding.validate_video_canon_verification'):
            body=old[name]['as'][0].sval;wrapped=new[name]['as'][0].sval
            # Exact original body begins at its first old SELECT and runs unchanged to END.
            original_tail=body[body.index('    SELECT'):]
            self.assertTrue(wrapped.endswith(original_tail))
            self.assertEqual(wrapped[:wrapped.index('BEGIN\n')],body[:body.index('BEGIN\n')])
            self.assertEqual(down[name]['as'][0].sval,body)
            self.assertEqual({k:str(v) for k,v in old[name].items() if k!='as'},
                {k:str(v) for k,v in new[name].items() if k!='as'})

    def test_03_no_grants_roles_deletes_or_Canon_writes(self):
        for stmt in [*AST,*DOWNAST]:
            n=stmt.stmt
            if isinstance(n,GrantStmt):self.assertFalse(n.is_grant)
            self.assertNotIn(type(n).__name__,{'CreateRoleStmt','AlterRoleStmt','AlterRoleSetStmt','DropRoleStmt','DropStmt','TruncateStmt','DeleteStmt'})
        self.assertNotRegex(SQL,r'(?im)^\s*(CREATE|ALTER)\s+ROLE\b')
        mutations=set(re.findall(r'(?i)INSERT\s+INTO\s+([a-z_.]+)',SQL))
        self.assertEqual(mutations,{'bidding.video_definition_store_control','bidding.video_canon_ai_verification_bundle','bidding.video_canon_ai_verification','public.schema_migration'})
        self.assertNotIn('INSERT INTO bidding.video_definition_store_binding',SQL)
        self.assertNotRegex(SQL,r'(?i)(INSERT\s+INTO|UPDATE)\s+public\.(canon_activation|knowledge_item|knowledge_version)\b')

    def test_04_registration_is_assignments_only_not_native16_receipts(self):
        register=source('register_video_definition_bundle');read=source('read_current_video_definition_bundle')
        self.assertNotIn('INSERT INTO bidding.video_canon_ai_verification(',register)
        self.assertIn('jsonb_array_length(check_rows)=16',read)
        self.assertIn('DEFINITION_ALL16_GENUINE_NATIVE_ROWS_REQUIRED',read)
        self.assertIn('FROM bidding.video_canon_ai_verification v',read)
        self.assertNotRegex(read,r'(?i)\b(INSERT|UPDATE|DELETE)\b')

    def test_05_actual_actor_scope_policy_and_no_client_authority_flags(self):
        binding=source('video_definition_require_binding');record=source('record_video_definition_verification')
        for required in ('b.execution_principal=session_user','pg_has_role','b.action=p_action',
            'b.valid_from<=clock_timestamp()','clock_timestamp()<b.valid_to','FOR SHARE','DEFINITION_STORE_DISABLED'):
            self.assertIn(required,binding)
        self.assertIn('session_user,p_assurance_level',record)
        self.assertNotIn('p_execution_principal',record)
        self.assertNotRegex(SQL,r"current_setting\('.*(authenticated|trusted|actor|principal|lease)")
        self.assertIn("capability='bridge_school_canon_control_verifier'",source('video_definition_test_state'))

    def test_06_RW_lifetime_fences_and_deferred_commit_rechecks(self):
        fence=source('video_definition_metadata_fence')
        self.assertIn("current_setting('transaction_read_only')='off'",fence)
        self.assertIn('LOCK TABLE public.analysis_candidate IN EXCLUSIVE MODE',fence)
        for table in ('public.source','bidding.video_canon_source_policy','bidding.video_canon_verifier_registry',
            'bidding.video_canon_definition_state','public.canon_activation','bidding.rule_test_run'):
            self.assertIn(table,fence)
        self.assertEqual(SQL.count('DEFERRABLE INITIALLY DEFERRED'),2)
        self.assertIn('video_definition_validate_check_row(NEW)',source('video_definition_commit_guard'))

    def test_07_raw_hash_before_parse_duplicate_and_size_guards(self):
        for name,raw_name in [('register_video_definition_bundle','p_canonical_json'),('video_definition_validate_bundle_row','n.bundle_canonical_json')]:
            body=source(name)
            self.assertLess(body.index("public.digest(convert_to("+raw_name),body.index(raw_name+'::json'))
            self.assertIn('1048576',body);self.assertIn('video_definition_unique_json',body)
        self.assertIn('count(DISTINCT key COLLATE "C")',source('video_definition_unique_json'))
        self.assertIn('p_depth<=64',source('video_definition_unique_json'))

    def test_08_canonical_integer_domain_codec_golden_vectors(self):
        vectors=[({},b'{}'),({'z':[True,False,None],'a':'é'},'{"a":"é","z":[true,false,null]}'.encode()),
            ({'n':-7,'a':'\n"\\'},b'{"a":"\\n\\"\\\\","n":-7}')]
        for value,expected in vectors:
            self.assertEqual(canonical_bytes(value),expected)
            self.assertEqual(parse_canonical(expected),value)
            self.assertEqual(digest('SOURCE_ONLY_VECTOR',value),hashlib.sha256(b'SOURCE_ONLY_VECTOR\0'+expected).hexdigest())
        for raw in (b'{"a":1.0}',b'{"a":true,"a":false}',b'{"a":9007199254740992}',b'{"a":NaN}'):
            with self.assertRaises(ContractError):parse_canonical(raw)
        codec=source('video_definition_canonical_int');hashfn=functions(SQL)['bidding.video_definition_digest']['as'][0].sval
        self.assertIn('COLLATE "C"',codec);self.assertIn('9007199254740991',codec)
        self.assertIn("decode('00','hex')",hashfn);self.assertNotIn('p::text',hashfn)

    def test_09_latest_reserved_pending_blocks_earlier_PASS(self):
        m,a,o,c=witness();self.assertTrue(frozen_oracle(m,a,o,c)['structural_eligibility'])
        latest=deepcopy(a[0]);latest.update(attempt_key='SOURCE_ONLY:newer',attempt_seq=2,started_us=150);a.append(latest)
        result=frozen_oracle(m,a,o,c);self.assertFalse(result['structural_eligibility'])
        self.assertTrue(any(x.startswith('PENDING:') for x in result['blockers']))
        body=source('video_definition_test_state')
        self.assertIn("ORDER BY (value->>'attempt_seq')::bigint DESC LIMIT 1",body)
        self.assertIn('DEFINITION_LATEST_PENDING_OR_FAILED',body)

    def test_10_context_oracle_version_and_exact_expiry_fail_closed(self):
        for mode in ('snapshot','expiry','oracle','version','revoked','sequence'):
            m,a,o,c=witness()
            if mode=='snapshot':c['school_snapshot_sha256']=sha('SOURCE_ONLY:changed')
            if mode=='expiry':
                result=frozen_oracle(m,a,o,c,now=1110);self.assertFalse(result['structural_eligibility']);continue
            if mode=='oracle':o[0]['observed_sha256']=sha('SOURCE_ONLY:wrong')
            if mode=='version':a[0]['case_version']=True
            if mode=='revoked':c['attestors_active']=False
            if mode=='sequence':a[0]['attempt_seq']=2
            try:self.assertFalse(frozen_oracle(m,a,o,c)['structural_eligibility'])
            except ContractError:pass
        sql=source('video_definition_test_state')
        self.assertIn('now_us<end_us',sql);self.assertNotIn('rule_test_state_sha256',sql)
        self.assertIn("'DEFINITION_TEST_STATE_PROPOSAL_0_1'",sql)
        self.assertIn("'DEFINITION_DEPENDENCY_CONTEXT_0_1'",sql)

    def test_11_all16_keys_current_binding_and_independence_contract(self):
        body=source('video_definition_descriptor')
        checks=set(re.findall(r"'([A-Z_]+)'",body))&set(REQUIRED_CHECKS)
        self.assertEqual(checks,set(REQUIRED_CHECKS))
        self.assertIn('n.verification_bundle_sha256=bundle.verification_bundle_sha256',source('video_definition_validate_check_row'))
        self.assertIn("r->>'result'='PASS'",body)
        self.assertIn("count(DISTINCT value->>'execution_principal')=3",body)
        self.assertIn("IN ('I2','I3')",body)
        self.assertIn('DEFINITION_STATELESS_SNAPSHOT_CLAIM',body)
        self.assertIn('DEFINITION_CURRENT_TEST_OR_CANON_SNAPSHOT',body)
        self.assertIn("jsonb_typeof(p->'semantic_confidence')='number'",body)
        self.assertIn('BETWEEN 0.95 AND 1',body)

    def test_12_declarative_exact_template_and_actual_rows_contract(self):
        # Independent declarative presence/uniqueness oracle for the new store seam.
        schema={'type':'array','minItems':16,'maxItems':16,'allOf':[{'contains':{'type':'object','required':['check_id','result'],
            'properties':{'check_id':{'const':key},'result':{'const':'PASS'}}},'minContains':1,'maxContains':1} for key in sorted(REQUIRED_CHECKS)]}
        oracle=jsonschema.Draft202012Validator(schema)
        claims=[dict(check_id=k,result='PASS') for k in sorted(REQUIRED_CHECKS)]
        self.assertTrue(oracle.is_valid(claims))
        for actual in ([],claims[:-1],claims+[deepcopy(claims[0])],claims[:-1]+[deepcopy(claims[0])]):
            self.assertFalse(oracle.is_valid(actual))
        for bad in (True,None,0,'FAIL'):
            actual=deepcopy(claims);actual[0]['result']=bad;self.assertFalse(oracle.is_valid(actual))

    def test_13_rollback_retains_data_and_denies_native_NEW_and_OLD_Definition(self):
        self.assertNotIn('DROP TRIGGER',DOWN);self.assertNotIn('DROP TABLE',DOWN)
        self.assertIn('SET enabled=false,generation=generation+1',DOWN)
        guard=source('video_definition_denial_guard')
        self.assertIn('NEW.bundle_payload',guard);self.assertIn('OLD.bundle_payload',guard)
        self.assertIn('NEW.video_canon_ai_verification_bundle_id',guard)
        self.assertIn('OLD.video_canon_ai_verification_bundle_id',guard)
        self.assertIn('FOR SHARE',guard)
        self.assertNotIn('video_definition_store_binding',source('video_definition_control_guard'))

    def test_14_default_disabled_empty_policy_and_versioned_native_assignments(self):
        inserts=[r.stmt for r in AST if isinstance(r.stmt,InsertStmt)]
        self.assertEqual({x.relation.relname for x in inserts},{'video_definition_store_control','schema_migration'})
        self.assertIn('VALUES(true,false,1)',SQL)
        policy=source('video_definition_binding_guard')
        for required in ('NEW.recorded_by_principal=session_user','pg_has_role','NEW.allowed_check_ids<@reg.allowed_check_ids',
            "NEW.status='revoked'",'DEFINITION_BINDING_IMMUTABLE_ONLY_REVOKE'):
            self.assertIn(required,policy)
        self.assertNotIn('issue_',SQL)

    def test_15_RPCS_have_native_bindings_and_no_body_is_Teacher_readiness(self):
        fn=functions(SQL)
        for name in ('register_video_definition_bundle','record_video_definition_verification','read_current_video_definition_bundle','read_video_definition_test_context'):
            body=source(name);self.assertIn('video_definition_require_binding',body)
            self.assertIn('security',fn['bidding.'+name]);self.assertTrue(fn['bidding.'+name]['security'].boolval)
        self.assertNotIn('teacher_packet',SQL);self.assertNotIn('FULL_CALL',SQL)
        self.assertNotIn('SET ROLE',SQL)

    def test_16_original8_assignments_make_bundle_hash_constructible(self):
        fields={'check_id','result','verifier_family','verifier_version','execution_principal','assurance_level','evidence_sha256','canon_snapshot_sha256'}
        assignments=[dict(check_id=k,result='PASS',verifier_family='SOURCE_ONLY:family',verifier_version='SOURCE_ONLY:1',
            execution_principal='SOURCE_ONLY:principal',assurance_level='I1',evidence_sha256=sha(k),
            canon_snapshot_sha256=sha('SOURCE_ONLY:snapshot') if k in STATE_DEPENDENT_CHECKS else None) for k in sorted(REQUIRED_CHECKS)]
        raw=json.dumps({'schema':'SOURCE_ONLY:hash-constructibility-witness','checks':assignments},sort_keys=True,separators=(',',':')).encode()
        bundle_sha=hashlib.sha256(raw).hexdigest()
        self.assertNotIn(bundle_sha.encode(),raw)
        projected=[dict(row,candidate_payload_hash=sha('SOURCE_ONLY:candidate'),verification_bundle_sha256=bundle_sha,school_key='SOURCE_ONLY:school') for row in assignments]
        self.assertEqual(len(projected),16)
        self.assertTrue(all(set(r)==fields for r in assignments))
        self.assertTrue(all(r['verification_bundle_sha256']==bundle_sha for r in projected))
        self.assertTrue(all({k:v for k,v in r.items() if k in fields}==a for r,a in zip(projected,assignments)))
        native=(ROOT/'database/definition_initial_postgres.py').read_text(encoding='utf-8')
        self.assertIn('ORIGINAL_EIGHT_ASSIGNMENT_FIELDS_REQUIRED',native)
        self.assertIn("verification_bundle_sha256=request['verification_bundle_sha256']",native)

    def test_17_all_consumed_assignment_rows_and_phantoms_are_fenced(self):
        fence=source('video_definition_metadata_fence');binding=source('video_definition_require_binding')
        full='LOCK TABLE bidding.video_definition_store_binding IN SHARE MODE'
        self.assertIn(full,fence)
        self.assertLess(fence.index(full),fence.index('LOCK TABLE public.analysis_candidate'))
        self.assertLess(binding.index('video_definition_metadata_fence()'),binding.index('WHERE binding_id=p_id FOR SHARE'))
        self.assertEqual(binding.count("b.execution_principal=session_user::text"),2)

    def test_18_new_SECDEF_catalog_and_default_ACL_boundaries(self):
        fn=functions(SQL)
        for name,options in fn.items():
            if name in ('bidding.validate_video_canon_verification','bidding.validate_video_canon_verification_bundle'):continue
            body=options['as'][0].sval
            self.assertNotRegex(body,r'(?<![a-zA-Z0-9_.])pg_roles\b')
            if 'security' in options and options['security'].boolval:
                node=next(raw.stmt for raw in AST if isinstance(raw.stmt,CreateFunctionStmt)
                    and '.'.join(x.sval for x in raw.stmt.funcname)==name)
                paths=[o.arg for o in node.options if o.defname=='set' and o.arg.name=='search_path']
                self.assertEqual(len(paths),1)
                path_values=[a.val.sval for a in paths[0].args]
                self.assertEqual(path_values[0],'pg_catalog')
                self.assertEqual(path_values[-1],'pg_temp')
        self.assertIn('DEFINITION_UNEXPECTED_NEW_TABLE_ACL',SQL)
        self.assertIn('DEFINITION_UNEXPECTED_NEW_FUNCTION_ACL',SQL)
        self.assertIn('DEFINITION_UNREVIEWED_LOGIN_INHERITS_INSTALLATION_OWNER',SQL)
        self.assertIn('a.grantee<>t.relowner',SQL)
        self.assertIn('a.grantee<>p.proowner',SQL)

    def test_19_both_release_migrations_register_before_commit(self):
        for number, name in (("0375", "contract_store"), ("0376", "bundle_store_successor")):
            migration = ROOT / f'database/migrations/{number}_workflow_definition_{name}.sql'
            body = migration.read_text(encoding='utf-8')
            key = f'{number}_workflow_definition_{name}'
            self.assertEqual(body.count("schema_migration(migration_key)"), 1)
            self.assertIn(f"VALUES ('{key}')", body.replace('VALUES(', 'VALUES ('))
            self.assertLess(body.index('schema_migration(migration_key)'), body.rindex('COMMIT;'))
            self.assertTrue(any(isinstance(statement.stmt, InsertStmt)
                                for statement in parse_sql(body)))


if __name__=='__main__':unittest.main()
