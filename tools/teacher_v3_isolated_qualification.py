"""Run the pinned Teacher/L1/BML tests without importing the application."""
import sys
if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode) or sys.flags.optimize:
    raise RuntimeError("Use -I -S -B with assertions enabled")
import argparse
import ast
from collections import Counter
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path

ROOT=Path(__file__).absolute().parent.parent
require=lambda ok,reason: None if ok else (_ for _ in ()).throw(RuntimeError(reason))
require(ROOT.resolve(strict=True)==ROOT,"Redirected checkout root")
helper=ROOT/"tools/teacher_v3_ci_runtime.py"
require(sha256(helper.read_bytes()).hexdigest()=="6d1ac89e0b4a71995a514e41c687cd8c4dd645a060199b77613842b64b1dd5fe","CI helper differs")
spec=importlib.util.spec_from_file_location("_teacher_v3_ci_runtime",helper)
runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
parser=argparse.ArgumentParser()
parser.add_argument("--pytest-site",type=Path,required=True)
parser.add_argument("--output",type=Path,required=True)
parser.add_argument("--source-commit",default="LOCAL_CANDIDATE_NOT_COMMITTED")
args=parser.parse_args()
output=args.output.absolute()
require(output.parent==ROOT/"qualification-results" and output.suffix==".json" and not output.exists(),"New report required inside qualification-results")
output.parent.mkdir(exist_ok=True)
site=args.pytest_site.resolve(strict=True)
require(site.is_dir() and (site/"pytest/__init__.py").is_file(),"Existing pytest site directory required")
for name in tuple(os.environ):
    if name.startswith("PYTEST_"):os.environ.pop(name,None)
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"]="1"
sys.path.insert(0,str(site))
import pytest
require(pytest.__version__=="8.4.2","Expected pinned pytest 8.4.2")
manifest,owner,paths,payloads=runtime.preflight()
expected_files=manifest["expected_per_file"]
test_paths=manifest["test_paths"]
require(set(expected_files)==set(test_paths) and sum(expected_files.values())==273,"Expected exact 273 selection")
expected_bodies=Counter()
def inspect(body,path,prefix=""):
    for node in body:
        if isinstance(node,ast.ClassDef):
            inspect(node.body,path,prefix+node.name+".")
        elif isinstance(node,ast.FunctionDef) and node.name.startswith("test_"):
            count=1
            for decorator in node.decorator_list:
                if isinstance(decorator,ast.Call) and isinstance(decorator.func,ast.Attribute) and decorator.func.attr=="parametrize":
                    require(len(decorator.args)>=2 and isinstance(decorator.args[1],(ast.List,ast.Tuple)),"Literal parametrize list required")
                    count*=len(decorator.args[1].elts)
            expected_bodies[path+"::"+prefix+node.name]=count
for path in test_paths:
    inspect(ast.parse(payloads[path],filename=str(paths[path])).body,path)
    require(sum(v for k,v in expected_bodies.items() if k.startswith(path+"::"))==expected_files[path],"Independent AST count differs")

class Results:
    def __init__(self):
        self.ids=[];self.reports=[];self.collected=Counter();self.files=Counter();self.collection_errors=[]
    def pytest_collection_modifyitems(self,items):
        for item in items:
            path=Path(item.path).resolve().relative_to(ROOT).as_posix()
            require(path in expected_files,"Unexpected collected test")
            key=path+"::"+item.obj.__qualname__
            self.ids.append(item.nodeid);self.collected[key]+=1;self.files[path]+=1
    def pytest_runtest_logreport(self,report):
        self.reports.append({"id":report.nodeid,"phase":report.when,"outcome":report.outcome,"xfail":bool(getattr(report,"wasxfail",False))})
    def pytest_collectreport(self,report):
        if report.failed:self.collection_errors.append(report.nodeid)

plugin=Results();bodies=Counter();calls=Counter()
test_files={str(paths[p]):p for p in test_paths}
def profile(frame,event,arg):
    if event!="call":return
    path=test_files.get(frame.f_code.co_filename)
    if path and frame.f_code.co_name.startswith("test_"):
        bodies[path+"::"+frame.f_code.co_qualname]+=1
    name=frame.f_globals.get("__name__","")
    if name.startswith("bridge_school_api."):
        calls[name+"."+frame.f_code.co_name]+=1
config=ROOT/"tests/teacher_v3_empty_pytest.ini"
require(config.read_bytes()==b"[pytest]\naddopts=\n","Unexpected pytest config")
guard=runtime.PinnedGuard([*paths.values(),Path(__file__).absolute(),helper,config],[site])
log=io.StringIO()
with guard:
    runtime.load_modules(paths,payloads)
    sys.setprofile(profile)
    try:
        with redirect_stdout(log),redirect_stderr(log):
            code=pytest.main(["-q","-s","--noconftest","-p","no:cacheprovider","-p","no:logging",
                "--rootdir",str(ROOT),"-c",str(config),*[str(paths[p]) for p in test_paths]],plugins=[plugin])
    finally:sys.setprofile(None)
pins={f["path"]:f["sha256"] for f in manifest["files"]}
after=runtime.post_hashes(paths)
passed=[r for r in plugin.reports if r["phase"]=="call" and r["outcome"]=="passed" and not r["xfail"]]
success=(int(code)==0 and len(plugin.ids)==len(set(plugin.ids))==len(passed)==273
    and dict(plugin.files)==expected_files and plugin.collected==expected_bodies==bodies
    and not plugin.collection_errors and not guard.attempts and after==pins
    and not any(r["outcome"]!="passed" or r["xfail"] for r in plugin.reports)
    and len(plugin.reports)==3*273 and "bridge_school_api.main" not in sys.modules)
report={"schema":"teacher-v3-portable-qualification-v1","status":"PASS_273_ISOLATED_TESTS" if success else "FAIL_ISOLATED_TESTS",
    "created_utc":datetime.now(timezone.utc).isoformat(),"source_commit":args.source_commit,
    "tests":len(plugin.ids),"passed":len(passed),"partitions":{"Teacher":76,"context_completed_L1":151,"BML":9,"identity":4,"L1_safety":33},
    "test_ids":plugin.ids,"reports":plugin.reports,"AST_expected_bodies":dict(expected_bodies),
    "collected_bodies":dict(plugin.collected),"actual_body_calls":dict(bodies),"per_file":dict(plugin.files),
    "collection_errors":plugin.collection_errors,"test_log":log.getvalue(),"project_calls":dict(calls),
    "source_hashes_before":pins,"source_hashes_after":after,"guard_attempts":guard.attempts,
    "runner_sha256":sha256(Path(__file__).read_bytes()).hexdigest(),"helper_sha256":sha256(helper.read_bytes()).hexdigest(),
    "python_version":sys.version,"pytest_version":pytest.__version__,"application_initializer_executed":False,
    "live_database_read":False,"BML_FULL_CALL":"ABSTAIN","production_ready":False,
    "limits":"Observational guard and exact selected tests; not an OS sandbox or production/canon admission"}
with output.open("x",encoding="utf-8",newline="\n") as out:out.write(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
print(json.dumps({k:report[k] for k in ("status","tests","passed","partitions","guard_attempts","production_ready")}))
if not success:raise SystemExit(1)
