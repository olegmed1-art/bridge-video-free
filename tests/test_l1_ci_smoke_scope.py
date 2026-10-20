"""No live Git, DB, network, credentials or application imports in these tests."""
import importlib.util
from pathlib import Path
import subprocess
import types
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('l1_ci_smoke_scope',ROOT/'tools/l1_ci_smoke_scope.py')
scope=importlib.util.module_from_spec(spec);spec.loader.exec_module(scope)


def changed(*entries):
    return b''.join(status.encode()+b'\0'+path.encode()+b'\0' for status,path in entries)


class ScopeChecks(unittest.TestCase):
    def test_exact_six_file_l1_fix_withholds_dsn(self):
        data=changed(*[('A' if p.endswith('observation_safety.py') else 'M',p)
                       for p in sorted(scope.L1_PATHS)])
        self.assertEqual(scope.classify_diff(data),(False,'exact_l1_or_ci_only'))

    def test_complete_ci_bootstrap_and_mixed_l1_ci_withhold_dsn(self):
        for paths in [scope.CI_PATHS, scope.CI_PATHS|scope.L1_PATHS]:
            self.assertEqual(scope.classify_diff(changed(*[('M',p) for p in sorted(paths)])),
                             (False,'exact_l1_or_ci_only'))

    def test_other_api_change_preserves_ordinary_read_only_smoke(self):
        for path in ['app.py','bridge_school_api/main.py','bridge_school_api/db.py',
                     'bridge_school_api/l1_canonical_runtime.py.extra']:
            with self.subTest(path=path):
                self.assertEqual(scope.classify_diff(changed(('M',path))),
                                 (True,'ordinary_read_only_smoke'))

    def test_mixed_change_cannot_hide_a_non_l1_file(self):
        self.assertEqual(scope.classify_diff(changed(('M',next(iter(scope.L1_PATHS))),
                                                    ('M','bridge_school_api/db.py'))),
                         (True,'ordinary_read_only_smoke'))

    def test_deletes_renames_types_and_unmerged_changes_withhold(self):
        for status in ['D','R100','C100','T','U','X']:
            with self.subTest(status=status):
                self.assertEqual(scope.classify_diff(changed((status,'app.py'))),
                                 (False,'unsupported_change_status'))

    def test_nul_truncation_empty_and_malformed_records_withhold(self):
        for data in [b'',b'M\0app.py',b'M\0',b'M\0app.py\0M\0',b'\0']:
            with self.subTest(data=data):self.assertFalse(scope.classify_diff(data)[0])

    def test_unsafe_paths_duplicates_and_invalid_utf8_withhold(self):
        for path in ['/app.py','../app.py','a//b','a/./b','a\\b','a\nb','a\tb','a\x7fb']:
            with self.subTest(path=path):
                self.assertEqual(scope.classify_diff(changed(('M',path))),(False,'malformed_path'))
        self.assertFalse(scope.classify_diff(b'M\0\xff\0')[0])
        self.assertFalse(scope.classify_diff(changed(('M','app.py'),('M','app.py')))[0])

    def test_pr_or_other_ref_never_reads_git(self):
        def forbidden(*args,**kwargs):raise AssertionError('must not invoke Git')
        for env in [{},{'CI_EVENT_NAME':'pull_request','CI_REF':'refs/heads/main'},
                    {'CI_EVENT_NAME':'push','CI_REF':'refs/heads/test/l1'}]:
            self.assertEqual(scope.main_push_scope(env,forbidden),(False,'not_main_push'))

    def test_invalid_zero_identical_or_injected_sha_never_reads_git(self):
        def forbidden(*args,**kwargs):raise AssertionError('must not invoke Git')
        for before,after in [('0'*40,'b'*40),('a'*40,'0'*40),('a'*40,'a'*40),
                             ('A'*40,'b'*40),('a'*40+'; echo unsafe','b'*40),
                             ('a'*40,''),(None,'b'*40)]:
            env={'CI_EVENT_NAME':'push','CI_REF':'refs/heads/main',
                 'CI_BEFORE':before,'CI_AFTER':after}
            self.assertEqual(scope.main_push_scope(env,forbidden),(False,'invalid_push_range'))

    def fake_git(self,**overrides):
        calls=[]
        def run(command,**kwargs):
            calls.append(command)
            self.assertEqual(command[0],'git')
            self.assertEqual(kwargs,{'stdout':subprocess.PIPE,'stderr':subprocess.PIPE,
                                     'check':True,'timeout':20})
            key=tuple(command[1:])
            default={('rev-parse','HEAD'):b'b'*40+b'\n',
                     ('rev-parse','--is-shallow-repository'):b'false\n',
                     ('cat-file','-t','a'*40):b'commit\n',
                     ('cat-file','-t','b'*40):b'commit\n',
                     ('merge-base','--is-ancestor','a'*40,'b'*40):b'',
                     ('diff','--name-status','--no-renames','-z','a'*40,'b'*40,'--'):
                          changed(('M','app.py'))}
            value=overrides.get('responses',{}).get(key,default[key])
            if isinstance(value,Exception):raise value
            return types.SimpleNamespace(stdout=value)
        return run,calls

    def env(self):
        return {'CI_EVENT_NAME':'push','CI_REF':'refs/heads/main',
                'CI_BEFORE':'a'*40,'CI_AFTER':'b'*40}

    def test_complete_verified_main_push_uses_event_range_not_head_parent(self):
        run,calls=self.fake_git()
        self.assertEqual(scope.main_push_scope(self.env(),run),(True,'ordinary_read_only_smoke'))
        self.assertEqual(calls[-1],['git','diff','--name-status','--no-renames','-z',
                                    'a'*40,'b'*40,'--'])
        self.assertTrue(all('HEAD^' not in arg for cmd in calls for arg in cmd))

    def test_verified_l1_push_withholds_before_secret_job(self):
        key=('diff','--name-status','--no-renames','-z','a'*40,'b'*40,'--')
        run,_=self.fake_git(responses={key:changed(('M','bridge_school_api/l1_canonical_runtime.py'))})
        self.assertEqual(scope.main_push_scope(self.env(),run),(False,'exact_l1_or_ci_only'))

    def test_wrong_checkout_shallow_history_or_non_commit_withholds(self):
        cases=[(('rev-parse','HEAD'),b'c'*40+b'\n','checkout_not_event_after'),
               (('rev-parse','--is-shallow-repository'),b'true\n','shallow_history'),
               (('cat-file','-t','a'*40),b'blob\n','missing_commit_object')]
        for key,value,reason in cases:
            with self.subTest(reason=reason):
                run,_=self.fake_git(responses={key:value})
                self.assertEqual(scope.main_push_scope(self.env(),run),(False,reason))

    def test_non_ancestor_timeout_or_git_failure_withholds_without_leak(self):
        key=('merge-base','--is-ancestor','a'*40,'b'*40)
        for error in [subprocess.CalledProcessError(1,['git'],stderr=b'PRIVATE_VALUE'),
                      subprocess.TimeoutExpired(['git'],20),OSError('PRIVATE_VALUE')]:
            run,_=self.fake_git(responses={key:error})
            result=scope.main_push_scope(self.env(),run)
            self.assertEqual(result,(False,'unverified_push_diff'))
            self.assertNotIn('PRIVATE_VALUE',str(result))


if __name__=='__main__':unittest.main(verbosity=2)
