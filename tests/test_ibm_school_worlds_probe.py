"""Offline schema, compressed bundle, artifact export and one-shot transport checks."""
import ast
import base64
import contextlib
import copy
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import platform
import sys
import tempfile
import unittest
from unittest import mock
import zlib
from bridge_school_api.ai_worlds import generate_worlds
from ops import ibm_school_worlds_probe as p
from ops import ibm_worlds_validator as v

B = dict(run_id="99999123456", attempt=1, head="a"*40)
INPUTS = dict(mode="school_worlds", diagnose_ssh=False, test_oracle=False)
ENV = dict(GITHUB_REPOSITORY=p.read_only.REPOSITORY, GITHUB_REF=p.read_only.REF,
           GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_ACTOR="olegmed1-art", GITHUB_TRIGGERING_ACTOR="olegmed1-art",
           GITHUB_RUN_ID=B["run_id"], GITHUB_RUN_ATTEMPT="1", GITHUB_SHA=B["head"], GITHUB_EVENT_PATH="/unused/event")
RESULT = generate_worlds(**v.INPUT)
WORLDS = v.canonical_json_bytes(RESULT)
DATA = base64.b64encode(zlib.compress(WORLDS, 9)).decode()
VALIDATION = v.validate_repeated_results(RESULT, RESULT, generation_python_version=platform.python_version(), expected_result_sha256=v.EXPECTED_RESULT_SHA256)


def receipt(result="WORLD_GENERATED"):
    ok = result == "WORLD_GENERATED"
    return dict(schema="bridge.ibm.school-worlds.receipt.v1", operation="generate_worlds", **B,
                snapshot_id=p.RESUME_SNAPSHOT_ID, snapshot_sha256=p.RESUME_SNAPSHOT_SHA256,
                result=result, reason="OK" if ok else "UNIT_BUSY", python_version=platform.python_version(),
                generator_sha256=v.GENERATOR_SOURCE_SHA256, input_sha256=v.INPUT_SHA256,
                validation=copy.deepcopy(VALIDATION) if ok else None,
                artifact=dict(name="worlds.json", bytes=len(WORLDS), sha256=v.EXPECTED_RESULT_SHA256,
                              encoding="zlib+base64", data=DATA) if ok else None,
                gates_verified=list(p.host_contract.safety.GATE_UNITS) if ok else [],
                output_scope="/var/lib/bridge-school-maintenance/worlds-"+B["run_id"]+"-"+B["head"][:12] if ok else None,
                scope_created=ok, restore_scope="/var/lib/bridge-school-maintenance/"+p.RESUME_SNAPSHOT_ID+"/resume-restore-"+B["run_id"]+"-"+B["head"][:12], restore_created=True, stage="COMPLETE" if ok else "SNAPSHOT_VERIFIED", elapsed_ms=1300, changes=[])


def chain(row=None):
    row=receipt() if row is None else row
    binding={key:row[key] for key in B}
    raw=v.canonical_json_bytes(row)
    return [dict(event="RUN_BOUND_SCHOOL_WORLDS", **binding), dict(event="TCP_OPEN", elapsed_seconds=0, attempts=1),
            dict(event="SCHOOL_WORLDS_SSH_STARTED", **binding),
            dict(event="SCHOOL_WORLDS_SSH_FINISHED", **binding, ssh_exit=0 if row["result"]=="WORLD_GENERATED" else 3,
                 elapsed_seconds=1.5, stdout_bytes=len(raw), stderr_bytes=0,
                 stdout_sha256=hashlib.sha256(raw).hexdigest(), stderr_sha256=hashlib.sha256(b"").hexdigest(), raw_output_exported=False),
            dict(event="SCHOOL_WORLDS_RECEIPT", **binding, receipt=row)]


def encoded(rows):
    return b"\n".join(json.dumps(row,separators=(",", ":")).encode() for row in rows)+b"\n"


class OfflineCase(unittest.TestCase):
    def setUp(self):
        self.guard=contextlib.ExitStack();self.addCleanup(self.guard.close)
        for target in ("socket.socket", "subprocess.Popen", "subprocess.run", "subprocess.check_output"):
            self.guard.enter_context(mock.patch(target,side_effect=AssertionError("LIVE_TRANSPORT_FORBIDDEN")))
        self.temp=self.guard.enter_context(tempfile.TemporaryDirectory())
        self.guard.enter_context(mock.patch.dict(os.environ,{"RUNNER_TEMP":self.temp}))

    def probe(self, rows=None, rc=0, err=b""):
        obj=p.PreparedWorlds(dict(B),"safe fixture","host fixture",71,72)
        obj.ready=True;obj.bind_identity(dict(p.IDENTITY))
        obj.call=mock.Mock(return_value=(rc,encoded(chain() if rows is None else rows),err))
        return obj

    def run_probe(self,obj,**kwargs):
        with mock.patch.object(p,"emit") as emit:
            result=obj.run_once(**kwargs)
        return result,emit


class ContextTests(OfflineCase):
    def ctx(self, env=None, inputs=None, head=None, raw=None):
        raw=json.dumps(dict(inputs=INPUTS if inputs is None else inputs)).encode() if raw is None else raw
        with mock.patch.dict(os.environ,ENV if env is None else env,clear=True), \
             mock.patch.object(Path,"read_bytes",return_value=raw), \
             mock.patch.object(p,"bounded_process",return_value=(0,(head or B["head"]).encode()+b"\n",b"")):
            return p.context()

    def test_owner_exact_workflow_dispatch(self):
        self.assertEqual(B,self.ctx())
        self.assertEqual(B,self.ctx(inputs=dict(INPUTS,diagnose_ssh="false",test_oracle="false")))

    def test_scope_owner_attempt_checkout_consumed_runs(self):
        for key,value in [("GITHUB_REPOSITORY","other/repo"),("GITHUB_REF","refs/heads/main"),
                          ("GITHUB_EVENT_NAME","push"),("GITHUB_ACTOR","other"),("GITHUB_TRIGGERING_ACTOR","other"),
                          ("GITHUB_RUN_ATTEMPT","2"),("GITHUB_RUN_ID","01"),("GITHUB_RUN_ID","١"),
                          ("GITHUB_SHA",p.RESUME_ORIGINAL_HEAD)]:
            with self.subTest(key=key),self.assertRaises(p.WorldsError):self.ctx(dict(ENV,**{key:value}))
        self.assertIn("38125459269",p.CONSUMED_RUNS)
        for run in p.CONSUMED_RUNS:
            with self.subTest(run=run),self.assertRaises(p.WorldsError):self.ctx(dict(ENV,GITHUB_RUN_ID=run))
        with self.assertRaisesRegex(p.WorldsError,"checkout_binding"):self.ctx(head="b"*40)

    def test_no_user_chosen_job_input_or_boolean_coercions(self):
        for change in (dict(mode="isolate_units"),dict(mode="trial_start"),dict(diagnose_ssh=True),dict(test_oracle=True),
                       dict(diagnose_ssh=0),dict(test_oracle=None),dict(seed=5),dict(count=1),dict(diagnose_ssh="False")):
            with self.subTest(change=change),self.assertRaises(p.WorldsError):self.ctx(inputs=dict(INPUTS,**change))
        for missing in INPUTS:
            row=dict(INPUTS);row.pop(missing)
            with self.assertRaises(p.WorldsError):self.ctx(inputs=row)

    def test_duplicate_or_nonfinite_dispatch_json(self):
        for raw in (b'{"inputs":{"mode":"school_worlds","mode":"status"}}',b'{"inputs":NaN}'):
            with self.assertRaises(p.WorldsError):self.ctx(raw=raw)


class BundleAndPinnedRouteTests(OfflineCase):
    def test_cli_style_ops_only_import_needs_no_repository_pythonpath(self):
        root=Path.cwd().resolve()
        paths=[str(root/'ops')]+[item for item in sys.path if item and Path(item).resolve()!=root]
        with mock.patch.dict(sys.modules),mock.patch.object(sys,'path',paths):
            for name in list(sys.modules):
                if name.startswith('bridge_school_api') or name.startswith('ibm_'):
                    del sys.modules[name]
            module=importlib.import_module('ibm_school_worlds_probe')
            self.assertEqual('school_worlds',module.PreparedWorlds.kind)
            self.assertNotIn('bridge_school_api',sys.modules)

    def test_real_bundle_and_request_fit_input_cap(self):
        bundle=p.bundled_host_source()
        self.assertEqual(p.HOST_SOURCE_SHA,hashlib.sha256(bundle.encode()).hexdigest())
        obj=p.PreparedWorlds(dict(B),Path('ops/ibm_ssh_probe_safe.py').read_text(),bundle,71,72)
        obj.bind_identity(dict(p.IDENTITY))
        with mock.patch.object(p,"bounded_process",return_value=(0,b"",b"")) as process:
            obj.call("generate_worlds",373)
        payload=process.call_args.kwargs["payload"]
        self.assertLessEqual(len(payload),131072)
        self.assertEqual(65536,process.call_args.kwargs['limit'])
        document=json.loads(payload)
        request=document['request']
        self.assertEqual({"schema","operation","run_id","attempt","head","identity","transport","snapshot_id","snapshot_sha256"},set(request))
        self.assertEqual('generate_worlds',request['operation'])
        self.assertEqual('bridge.ibm.school-worlds.request.v1',request['schema'])
        self.assertEqual(368,document['seconds'])
        self.assertEqual(373,process.call_args.kwargs['timeout'])
        command=process.call_args.args[0]
        self.assertIn('StrictHostKeyChecking=yes',command);self.assertIn('ForwardAgent=no',command)
        remote=p.remote_source();compile(remote,'oracle-school-worlds','exec')
        self.assertIn('/usr/bin/sudo -n /usr/bin/python3 -I -B -c ',remote)
        self.assertIn('limit=49152',remote)
        self.assertNotIn('req[\'operation\']==\'resume\'',remote)

    def test_loader_checks_every_module_before_any_execution(self):
        bundle=p.bundled_host_source()
        executed=mock.Mock()
        with mock.patch.dict(sys.modules):exec(bundle,{"exec":executed})
        self.assertEqual(4,executed.call_count)
        self.assertEqual(p.MODULE_PATHS.keys(),p.SOURCE_PINS.keys())
        self.assertIn("__bundle_started_monotonic__",executed.call_args.args[1])

    def test_loader_rejects_corrupt_hash_cap_trailing_and_duplicate_names(self):
        bundle=p.bundled_host_source()
        records=ast.literal_eval(next(line.split('=',1)[1] for line in bundle.splitlines() if line.startswith('records=')))
        def check(rows):
            source=p.BUNDLE_TEMPLATE.replace('@PINS@',repr(p.SOURCE_PINS)).replace('@RECORDS@',repr(rows))
            executed=mock.Mock()
            with self.assertRaises((RuntimeError,ValueError,zlib.error)),mock.patch.dict(sys.modules):exec(source,{"exec":executed})
            executed.assert_not_called()
        for index,value in ((1,131073),(2,'0'*64),(3,49153),(4,'0'*64),(5,'!')):
            row=list(records[0]);row[index]=value
            check((tuple(row),)+records[1:])
        row=list(records[0]);compressed=base64.b64decode(row[5])+b'trailing'
        row[3]=len(compressed);row[4]=hashlib.sha256(compressed).hexdigest();row[5]=base64.b64encode(compressed).decode()
        check((tuple(row),)+records[1:]);check((records[0],records[0])+records[2:])

    def test_source_drift_fails_before_key_or_network(self):
        with mock.patch.object(p,'context',return_value=B),mock.patch.object(Path,'read_bytes',return_value=b'changed'), \
             mock.patch.object(p,'bounded_process') as process,self.assertRaisesRegex(p.WorldsError,'source_pin'):
            p.prepare()
        process.assert_not_called()

    def test_runtime_never_recompresses_frozen_bundle(self):
        with mock.patch.object(p.zlib,'compress',side_effect=AssertionError('RUNTIME_RECOMPRESSION_FORBIDDEN')):
            bundle=p.bundled_host_source()
        self.assertEqual(p.HOST_SOURCE_SHA,hashlib.sha256(bundle.encode()).hexdigest())

    def test_old_sources_and_caps_are_unchanged(self):
        pins={'ops/ibm_unit_isolation_probe.py':'e2b7194fdeef79563442c9a2a30e364fd39356fbdcf2eddc77cac2b18eebe6a5',
              'ops/ibm_ssh_probe_safe.py':p.SAFE_SOURCE_SHA,
              'ops/ibm_unit_isolation.py':p.SOURCE_PINS['ibm_unit_isolation'],
              'ops/ibm_trial_guest_probe.py':'78ee98cfed0a6055e15ee8645763324a63722695f8e9c5955691545a0c3960d9'}
        for path,sha in pins.items():self.assertEqual(sha,hashlib.sha256(Path(path).read_bytes()).hexdigest())
        self.assertEqual(32768,p.isolation.OUTPUT_BYTES)
        self.assertEqual((265,378,368,255,240),(p.MIN_MAINTENANCE_SECONDS,p.MAINTENANCE_SECONDS,p.REMOTE_SECONDS,p.SSH_SECONDS,p.GUEST_SECONDS))


class OneShotAndArtifactTests(OfflineCase):
    def test_actual_host_receipt_and_real_archive_fixture_roundtrip(self):
        from tests.test_ibm_school_worlds import FixedSchoolWorldsTests
        harness=FixedSchoolWorldsTests(methodName='runTest')
        self.addCleanup(harness.doCleanups)
        harness.setUp()
        row=harness.execute()
        self.assertEqual('WORLD_GENERATED',row['result'])
        binding={key:row[key] for key in B}
        obj=p.PreparedWorlds(binding,'safe fixture','host fixture',71,72)
        obj.ready=True;obj.bind_identity(dict(p.IDENTITY))
        obj.call=mock.Mock(return_value=(0,encoded(chain(row)),b''))
        with mock.patch.object(p,'RESUME_SNAPSHOT_SHA256',harness.fixture_sha):
            self.assertTrue(self.run_probe(obj)[0])
        self.assertEqual(WORLDS,(Path(obj.output_path)/'worlds.json').read_bytes())

    def test_success_independently_validates_persists_and_never_logs_data(self):
        obj=self.probe();ok,emitted=self.run_probe(obj)
        self.assertTrue(ok);obj.call.assert_called_once_with('generate_worlds',373)
        root=Path(obj.output_path)
        self.assertEqual({'worlds.json','worlds.pbn','input.json','receipt.json','manifest.json'},{f.name for f in root.iterdir()})
        self.assertEqual(WORLDS,(root/'worlds.json').read_bytes())
        self.assertEqual(v.INPUT_BYTES,(root/'input.json').read_bytes())
        self.assertEqual(v.render_pbn(RESULT),(root/'worlds.pbn').read_bytes())
        manifest=json.loads((root/'manifest.json').read_bytes())
        self.assertEqual(B['head'],manifest['source_commit']);self.assertEqual(p.SOURCE_PINS,manifest['reviewed_sources'])
        self.assertEqual(v.EXPECTED_RESULT_SHA256,manifest['files']['worlds.json']['sha256'])
        self.assertNotIn('data',json.loads((root/'receipt.json').read_bytes())['artifact'])
        self.assertNotIn(DATA,str(emitted.call_args_list))
        with self.assertRaisesRegex(p.WorldsError,'not_ready_or_replay'):obj.run_once()
        obj.call.assert_called_once()

    def test_valid_blocked_receipt_is_saved_without_worlds(self):
        obj=self.probe(chain(receipt('BLOCKED')),rc=3)
        self.assertFalse(self.run_probe(obj)[0])
        self.assertEqual({'receipt.json','manifest.json'},{f.name for f in Path(obj.output_path).iterdir()})

    def test_bound_budget_readiness_identity_and_one_attempt(self):
        for seconds in (0,264.999,378.001,True,math.inf,math.nan):
            obj=self.probe()
            with self.subTest(seconds=seconds),self.assertRaises(p.WorldsError):obj.run_once(max_seconds=seconds)
            self.assertFalse(obj.attempted);obj.call.assert_not_called()
        for field in ('ready','identity_bound'):
            obj=self.probe();setattr(obj,field,False)
            with self.assertRaises(p.WorldsError):obj.run_once()
            self.assertFalse(obj.attempted);obj.call.assert_not_called()
        obj=self.probe();obj.call.side_effect=p.WorldsError('transport_timeout')
        with self.assertRaises(p.WorldsError):obj.run_once()
        self.assertTrue(obj.attempted)
        with self.assertRaises(p.WorldsError):obj.run_once()
        obj.call.assert_called_once()

    def test_minimum_budget(self):
        obj=self.probe();self.assertTrue(self.run_probe(obj,max_seconds=265)[0])
        obj.call.assert_called_once_with('generate_worlds',260)

    def test_artifact_is_larger_than_old_cap_but_within_new_caps(self):
        self.assertEqual(72174,len(WORLDS))
        # Compression ratios may change across zlib releases; artifact identity
        # and the reviewed transport caps remain exact.
        self.assertLessEqual(len(zlib.compress(WORLDS,9)),32768)
        self.assertLessEqual(len(DATA),43692)
        self.assertGreater(len(v.canonical_json_bytes(receipt())),16384)
        self.assertLess(len(v.canonical_json_bytes(receipt())),49152)
        self.assertLess(len(encoded(chain())),65536)

    def test_output_collision_does_not_overwrite(self):
        root=Path(self.temp)/'ibm-school-worlds';root.mkdir();(root/'old').write_text('retained')
        with self.assertRaisesRegex(p.WorldsError,'runner_output_collision'):self.run_probe(self.probe())
        self.assertEqual('retained',(root/'old').read_text())
        self.assertEqual(['old'],[f.name for f in root.iterdir()])

    def test_artifact_encoding_hash_schema_and_bounded_decompression(self):
        good=receipt()['artifact']
        edits=[dict(name='../worlds.json'),dict(bytes=True),dict(sha256='0'*64),dict(encoding='base64'),dict(data='!'),
               dict(data=base64.b64encode(zlib.compress(WORLDS)+b'trailing').decode()),
               dict(data=base64.b64encode(zlib.compress(b'x'*(p.ARTIFACT_BYTES+1))).decode()),
               dict(data=base64.b64encode(zlib.compress(b'{}\n')).decode()),dict(extra='unapproved')]
        for edit in edits:
            with self.subTest(edit=list(edit)),self.assertRaises(p.WorldsError):p.artifact_result(dict(good,**edit))
        raw,result,check=p.artifact_result(good)
        self.assertEqual(WORLDS,raw);self.assertEqual(RESULT,result);self.assertTrue(check['valid'])

    def test_receipt_missing_extra_binding_scope_reasons_and_validation_rejected(self):
        edits=[dict(head='b'*40),dict(attempt=True),dict(snapshot_sha256='0'*64),dict(reason='UNSAFE_RAW'),
               dict(gates_verified=[]),dict(changes=['systemctl start']),dict(stage='REQUEST'),dict(scope_created=False),
               dict(output_scope='/tmp/other'),dict(input_sha256='0'*64),dict(generator_sha256='0'*64),
               dict(validation={}),dict(python_version='raw-secret'),dict(elapsed_ms=True),dict(elapsed_ms=120000),
               dict(restore_scope='/tmp/other'),dict(restore_created=False),dict(extra='unapproved')]
        for edit in edits:
            with self.subTest(edit=edit),self.assertRaises(p.WorldsError):p.validate_receipt(dict(receipt(),**edit),B)
        row=receipt();row.pop('artifact')
        with self.assertRaises(p.WorldsError):p.validate_receipt(row,B)
        row=receipt();row['validation']['repeatability']['runs_checked']=True
        with self.assertRaises(p.WorldsError):p.validate_receipt(row,B)

    def test_different_runner_patch_version_does_not_masquerade_as_host(self):
        row=receipt();row['python_version']='3.12.3'
        repeat=row['validation']['repeatability']
        repeat.update(generation_python_version_reported='3.12.3',validator_python_version='3.12.3')
        self.assertTrue(self.run_probe(self.probe(chain(row)))[0])

    def test_capture_hash_exit_chain_and_total_output_are_checked(self):
        rows=chain();rows[3]['stdout_sha256']='0'*64
        with self.assertRaises(p.WorldsError):self.run_probe(self.probe(rows))
        rows=chain();rows[3]['stdout_bytes']=49153
        with self.assertRaises(p.WorldsError):self.run_probe(self.probe(rows))
        with self.assertRaises(p.WorldsError):self.run_probe(self.probe(chain(),rc=3))
        obj=self.probe();obj.call.return_value=(0,b'x'*65537,b'')
        with self.assertRaises(p.WorldsError):self.run_probe(obj)
        rows=chain();rows[2],rows[3]=rows[3],rows[2]
        self.assertFalse(self.run_probe(self.probe(rows))[0])

    def test_close_reuses_idempotent_existing_descriptor_cleanup(self):
        obj=p.PreparedWorlds(B,'safe','host',71,72)
        with mock.patch.object(p.os,'close') as close:obj.close();obj.close()
        self.assertEqual([mock.call(71),mock.call(72)],close.call_args_list)
        with self.assertRaises(p.WorldsError):obj.call('generate_worlds',373)


if __name__=='__main__':unittest.main()
