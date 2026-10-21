"""Exercise the real gate/entry function; no live Git, DB or credentials here."""
from contextlib import redirect_stdout,redirect_stderr
from datetime import datetime,timezone,timedelta
import importlib.util
import io
from pathlib import Path
import subprocess
import types
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('l1_ci_smoke_scope',ROOT/'tools/l1_ci_smoke_scope.py')
scope=importlib.util.module_from_spec(spec);spec.loader.exec_module(scope)
NOW=datetime(2026,10,20,12,tzinfo=timezone.utc)
UNSET=object()


def raw(entries):
    return b''.join((':'+oldmode+' '+newmode+' '+old+' '+new+' '+status).encode()+b'\0'+path.encode()+b'\0'
                    for path,status,oldmode,newmode,old,new in entries)


def ordinary(path='bridge_school_api/main.py'):
    return (path,'M','100644','100644','1'*40,'2'*40)


class ScopeChecks(unittest.TestCase):
    def env(self):return {'CI_EVENT_NAME':'push','CI_REF':'refs/heads/main','CI_BEFORE':'a'*40,'CI_AFTER':'b'*40}

    def fake_git(self,data=UNSET,responses=None):
        calls=[];responses=responses or {}
        def run(command,**kwargs):
            calls.append(command);self.assertEqual(command[0],'git')
            self.assertEqual(kwargs,{'stdout':subprocess.PIPE,'stderr':subprocess.PIPE,'check':True,'timeout':20})
            key=tuple(command[1:])
            defaults={('rev-parse','HEAD'):b'b'*40+b'\n',('rev-parse','--is-shallow-repository'):b'false\n',
                ('cat-file','-t','a'*40):b'commit\n',('cat-file','-t','b'*40):b'commit\n',
                ('merge-base','--is-ancestor','a'*40,'b'*40):b'',
                ('diff','--raw','--no-abbrev','--no-renames','-z','a'*40,'b'*40,'--'):
                    raw([ordinary()]) if data is UNSET else data}
            if key in responses:value=responses[key]
            elif key[:2]==('cat-file','-t') and len(key)==3 and key[2] not in ('a'*40,'b'*40):value=b'blob\n'
            else:value=defaults[key]
            if isinstance(value,Exception):raise value
            return types.SimpleNamespace(stdout=value)
        return run,calls

    def entry(self,env=None,run=None,now=NOW):
        run=run or self.fake_git()[0];out=io.StringIO();err=io.StringIO()
        with redirect_stdout(out),redirect_stderr(err):code=scope.main(self.env() if env is None else env,run,now=now)
        return code,out.getvalue(),err.getvalue()

    def assert_error(self,data,reason=None):
        with self.assertRaises(scope.ScopeError) as caught:scope.classify_diff(data,now=NOW)
        if reason:self.assertEqual(str(caught.exception),reason)
        code,out,_=self.entry(run=self.fake_git(data)[0])
        self.assertEqual(code,2);self.assertIn('allow_smoke=false\n',out);self.assertIn('scope_status=error\n',out)

    def test_exact_all_six_old_new_identities_exempt(self):
        data=raw(scope.APPROVED_DELTA)
        self.assertEqual(scope.classify_diff(data,now=NOW),(False,'exact_accepted_L1_delta'))
        run,calls=self.fake_git(data);code,out,_=self.entry(run=run)
        self.assertEqual(code,0);self.assertIn('scope_status=exempt\n',out);self.assertIn('allow_smoke=false\n',out)
        self.assertEqual(len([c for c in calls if c[1:3]==['cat-file','-t']]),13) # two commits + eleven blobs

    def test_raw_entry_order_is_irrelevant(self):
        self.assertEqual(scope.classify_diff(raw(tuple(reversed(scope.APPROVED_DELTA))),now=NOW),
                         (False,'exact_accepted_L1_delta'))

    def test_every_partial_accepted_change_is_error(self):
        for size in range(1,6):self.assert_error(raw(scope.APPROVED_DELTA[:size]),'accepted_L1_delta_mismatch')

    def test_exact_six_plus_api_or_ci_or_future_workflow_is_error(self):
        for path in ('bridge_school_api/db.py','.github/workflows/bridge-school-api-ci.yml',
                     '.github/workflows/l1-canonical-runtime-v4-ci.yml'):
            self.assert_error(raw((*scope.APPROVED_DELTA,ordinary(path))),'accepted_L1_delta_mismatch')

    def test_each_old_or_new_content_change_is_error(self):
        for i in range(6):
            for field in (4,5):
                entries=list(scope.APPROVED_DELTA);row=list(entries[i])
                if row[1]=='A' and field==4:continue
                row[field]='f'*40;entries[i]=tuple(row)
                self.assert_error(raw(entries),'accepted_L1_delta_mismatch')

    def test_each_file_mode_change_is_error(self):
        for i in range(6):
            entries=list(scope.APPROVED_DELTA);row=list(entries[i]);row[3]='100755';entries[i]=tuple(row)
            self.assert_error(raw(entries),'accepted_L1_delta_mismatch')

    def test_original_fifteen_path_names_no_longer_grant_an_exemption(self):
        paths=('.github/workflows/bridge-school-api-ci.yml','.github/workflows/issue-881-contract-ci.yml',
               '.github/workflows/issue-881-current-main-authoritative-ci.yml','.github/workflows/secret-gate.yml',
               '.github/workflows/universal-video-ci.yml','.github/workflows/l1-canonical-runtime-v4-ci.yml',
               'tests/test_api_smoke_workflow_contract.py','tools/l1_ci_smoke_scope.py','tests/test_l1_ci_smoke_scope.py')
        for path in paths:
            self.assertEqual(scope.classify_diff(raw([ordinary(path)]),now=NOW),(True,'ordinary_read_only_smoke'))
        for path in (r[0] for r in scope.APPROVED_DELTA):
            self.assert_error(raw([ordinary(path)]),'accepted_L1_delta_mismatch')

    def test_ordinary_verified_outside_changes_keep_baseline_smoke(self):
        for path in ('app.py','bridge_school_api/main.py','bridge_school_api/db.py','bridge_school_api/l1_canonical_runtime.py.extra'):
            self.assertEqual(scope.classify_diff(raw([ordinary(path)]),now=NOW),(True,'ordinary_read_only_smoke'))
        code,out,_=self.entry();self.assertEqual(code,0);self.assertIn('allow_smoke=true\n',out);self.assertIn('scope_status=run\n',out)

    def test_deadline_is_exclusive_and_expiry_is_error(self):
        boundary=datetime(2026,10,21,tzinfo=timezone.utc);data=raw(scope.APPROVED_DELTA)
        self.assertEqual(scope.EXPIRES_AT,boundary)
        self.assertFalse(scope.classify_diff(data,now=boundary-timedelta(microseconds=1))[0])
        for now in (boundary,boundary+timedelta(days=365)):
            code,out,_=self.entry(run=self.fake_git(data)[0],now=now)
            self.assertEqual(code,2);self.assertIn('accepted_L1_approval_expired',out);self.assertIn('allow_smoke=false',out)

    def test_clock_and_event_environment_cannot_extend_approval(self):
        data=raw(scope.APPROVED_DELTA)
        with self.assertRaises(scope.ScopeError):scope.classify_diff(data,now=datetime(2026,10,20))
        env={**self.env(),'CI_APPROVAL_EXPIRY':'2099-01-01','CI_SKIP_SMOKE':'true'}
        code,out,_=self.entry(env,self.fake_git(data)[0],datetime(2026,10,21,tzinfo=timezone.utc))
        self.assertEqual(code,2);self.assertIn('expired',out)

    def test_empty_truncated_non_bytes_or_oversized_diff_is_error(self):
        for data in (b'',b'M\0app.py',b'\0',raw([ordinary()])[:-1],None,'raw',b'x'*(1024*1024+1)):
            self.assert_error(data)

    def test_bad_header_shortened_sha_and_noncanonical_modes_are_error(self):
        data=raw([ordinary()])
        for bad in (data.replace(b'100644',b'10064',1),data.replace(b'1'*40,b'1'*7,1),
                    data.replace(b'1'*40,b'A'*40,1),b'not raw\0app.py\0',data+b'extra\0'):
            self.assert_error(bad)

    def test_deleted_renamed_typechanged_unmerged_records_are_error(self):
        for status in ('D','R100','C100','T','U','X'):
            row=list(ordinary());row[1]=status;self.assert_error(raw([row]))

    def test_invalid_add_modify_identities_and_links_are_error(self):
        for field,value in ((1,'A'),(2,'000000'),(3,'120000'),(3,'160000'),(4,'0'*40),(5,'0'*40),(5,'1'*40)):
            row=list(ordinary());row[field]=value;self.assert_error(raw([row]))

    def test_unsafe_duplicate_and_non_utf8_paths_are_error(self):
        for path in ('/app.py','../app.py','a//b','a/./b','a\\b','a\nb','a\tb','a\x7fb'):
            self.assert_error(raw([ordinary(path)]),'malformed_path')
        self.assert_error(raw([ordinary(),ordinary()]),'malformed_path')
        self.assert_error(raw([ordinary()]).replace(b'bridge_school_api/main.py',b'\xff'),'malformed_path')

    def test_recognized_non_main_context_is_distinct_and_never_calls_git(self):
        def forbidden(*a,**kw):raise AssertionError('Git forbidden')
        for env in ({'CI_EVENT_NAME':'pull_request','CI_REF':'refs/pull/1/merge'},
                    {'CI_EVENT_NAME':'push','CI_REF':'refs/heads/test/local'}):
            code,out,_=self.entry(env,forbidden);self.assertEqual(code,0)
            self.assertIn('scope_status=not_applicable',out);self.assertNotIn('scope_status=exempt',out)

    def test_missing_or_unsupported_context_is_error_before_git(self):
        def forbidden(*a,**kw):raise AssertionError('Git forbidden')
        for env in ({},{'CI_EVENT_NAME':'push','CI_REF':''},{'CI_EVENT_NAME':'workflow_dispatch','CI_REF':'refs/heads/main'}):
            code,out,_=self.entry(env,forbidden);self.assertEqual(code,2);self.assertIn('scope_status=error',out)

    def test_invalid_zero_identical_or_injected_commit_ids_are_error(self):
        def forbidden(*a,**kw):raise AssertionError('Git forbidden')
        for before,after in [('0'*40,'b'*40),('a'*40,'0'*40),('a'*40,'a'*40),('A'*40,'b'*40),
                             ('a'*40+'; unsafe','b'*40),(None,'b'*40)]:
            code,out,_=self.entry({**self.env(),'CI_BEFORE':before,'CI_AFTER':after},forbidden)
            self.assertEqual(code,2);self.assertIn('invalid_push_range',out)

    def test_event_range_and_full_raw_metadata_used_not_head_parent(self):
        run,calls=self.fake_git();self.entry(run=run)
        self.assertEqual(calls[-1],['git','diff','--raw','--no-abbrev','--no-renames','-z','a'*40,'b'*40,'--'])
        self.assertTrue(all('HEAD^' not in arg for call in calls for arg in call))

    def test_checkout_shallow_and_noncommit_errors_have_nonzero_exit(self):
        for key,value,reason in [(('rev-parse','HEAD'),b'c'*40+b'\n','checkout_not_event_after'),
                                (('rev-parse','--is-shallow-repository'),b'true\n','shallow_history'),
                                (('cat-file','-t','a'*40),b'blob\n','missing_commit_object')]:
            code,out,_=self.entry(run=self.fake_git(responses={key:value})[0])
            self.assertEqual(code,2);self.assertIn(reason,out)

    def test_nonancestor_timeout_and_git_failure_never_leak_or_succeed(self):
        key=('merge-base','--is-ancestor','a'*40,'b'*40)
        for error in (subprocess.CalledProcessError(1,['git'],stderr=b'PRIVATE_VALUE'),
                      subprocess.TimeoutExpired(['git'],20),OSError('PRIVATE_VALUE')):
            code,out,err=self.entry(run=self.fake_git(responses={key:error})[0])
            self.assertEqual(code,2);self.assertIn('unverified_push_diff',out);self.assertNotIn('PRIVATE_VALUE',out+err)

    def test_non_bytes_git_output_is_error(self):
        code,out,_=self.entry(run=self.fake_git(responses={('rev-parse','HEAD'):'b'*40})[0])
        self.assertEqual(code,2);self.assertIn('unverified_git_output',out)

    def test_missing_approved_blob_refuses_exact_delta(self):
        blob=scope.APPROVED_DELTA[0][5]
        code,out,_=self.entry(run=self.fake_git(raw(scope.APPROVED_DELTA),{('cat-file','-t',blob):b'tree\n'})[0])
        self.assertEqual(code,2);self.assertIn('approved_blob_unavailable',out);self.assertIn('allow_smoke=false',out)


if __name__=='__main__':unittest.main(verbosity=2)
