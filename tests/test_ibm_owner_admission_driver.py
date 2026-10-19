"""Admit-only CLI and supervisor contracts. No owner credentials or live clients."""
import argparse
import ast
from dataclasses import asdict
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import types
import unittest
from unittest.mock import patch

from ops.ibm_diagnostic_v9 import owner_admission as admission

DRIVER = Path(__file__).resolve().parents[1] / "ops/ibm_diagnostic_v9/owner_setup.py"


def need(value, code):
    if not value:
        raise RuntimeError(code)


def selected(*names, **extra):
    tree = ast.parse(DRIVER.read_bytes())
    body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in body} == set(names)
    namespace = dict(__file__=str(DRIVER), argparse=argparse, hashlib=hashlib, json=json,
                     Path=Path, re=re, sys=sys, types=types, need=need,
                     OWNER_BUDGET_SECONDS=420)
    namespace.update(extra)
    exec(compile(ast.Module(body=body, type_ignores=[]), str(DRIVER), "exec"), namespace)
    return namespace


def frame():
    cycle = admission.Cycle(1234567, "a" * 32, "b" * 40, "c" * 64)
    pins = admission.Pins("d" * 64, "e" * 64, "f" * 64)
    return dict(kind=admission.FRAME_KIND, **asdict(cycle), **asdict(pins),
                observed_wall_ms=2000000, expires_ms=2360000, armed=False,
                power_post_attempted=False, live_go=False)


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.context = dict(GITHUB_REPOSITORY="olegmed1-art/bridge-video-free",
                            GITHUB_ACTOR="olegmed1-art", GITHUB_TRIGGERING_ACTOR="olegmed1-art",
                            GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_REF="refs/heads/main",
                            GITHUB_SHA="b" * 40, GITHUB_RUN_ATTEMPT="1", GITHUB_RUN_ID="1234567",
                            GITHUB_JOB="ibm-diagnostic-trial")
        self.arguments = ["--archive-sha", "d" * 64, "--manifest-sha", "e" * 64,
                          "--expected-main", "b" * 40]

    def test_cli_admit_only_selects_short_budget_and_dedicated_mode(self):
        calls = []
        ns = selected("main", os=types.SimpleNamespace(environ=self.context),
                      _run=lambda *_: None,
                      _supervise=lambda _fn, _start, **kw: calls.append(kw) or 0)
        self.assertEqual(ns["main"](self.arguments + ["--admit-only"]), 0)
        self.assertEqual(calls, [dict(seconds=120, collect_only=False, admit_only=True)])

    def test_cli_modes_are_mutually_exclusive(self):
        ns = selected("main", os=types.SimpleNamespace(environ=self.context))
        for mode in ("--run-reviewed", "--collect-only"):
            with self.subTest(mode=mode), patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                ns["main"](self.arguments + ["--admit-only", mode])

    def test_owner_context_still_refuses_rerun_or_foreign_actor(self):
        ns = selected("main", os=types.SimpleNamespace(environ=self.context))
        for key, value in (("GITHUB_ACTOR", "foreign"), ("GITHUB_RUN_ATTEMPT", "2")):
            with self.subTest(key=key), patch.dict(self.context, {key:value}), self.assertRaisesRegex(RuntimeError, "OWNER_SETUP_CONTEXT"):
                ns["main"](self.arguments + ["--admit-only"])

    def test_public_frame_requires_exact_fields_and_literal_false(self):
        pin = hashlib.sha256(DRIVER.with_name("owner_admission.py").read_bytes()).hexdigest()
        ns = selected("_public_frame", ADMISSION_SOURCE_SHA256=pin)
        self.assertEqual(ns["_public_frame"](json.dumps(frame())), frame())
        for key, value in (("armed", True), ("live_go", 0), ("control_run", True),
                           ("raw_capture", "forbidden"), ("source_sha", "wrong")):
            with self.subTest(key=key), self.assertRaises((RuntimeError, admission.Refused)):
                ns["_public_frame"](json.dumps(dict(frame(), **{key:value})))

    def test_public_frame_checks_literal_source_pin_and_duplicate_keys(self):
        ns = selected("_public_frame", ADMISSION_SOURCE_SHA256="0" * 64)
        with self.assertRaisesRegex(RuntimeError, "ADMIT_MODULE_PIN"):
            ns["_public_frame"](json.dumps(frame()))
        with self.assertRaisesRegex(RuntimeError, "PUBLIC_DUPLICATE"):
            ns["_public_frame"]('{"kind":"x","kind":"x"}')

    def test_driver_literal_pin_matches_the_public_helper_bytes(self):
        tree=ast.parse(DRIVER.read_bytes())
        value=next(n.value.value for n in tree.body if isinstance(n,ast.Assign)
                   and any(isinstance(t,ast.Name) and t.id=="ADMISSION_SOURCE_SHA256" for t in n.targets))
        self.assertEqual(value,hashlib.sha256(DRIVER.with_name("owner_admission.py").read_bytes()).hexdigest())

    def test_mode_mismatch_refuses_before_existing_owner_secret_is_read(self):
        args = argparse.Namespace(archive_sha="d"*64, manifest_sha="e"*64, expected_main="b"*40,
                                  admit_only=True, run_reviewed=False, collect_only=False)
        for value in (False, 1, "TRUE"):
            event = json.dumps(dict(inputs=dict(run_reviewed=False, collect_only=False, admit_only=value))).encode()
            fake_os = types.SimpleNamespace(environ={"GITHUB_EVENT_PATH":"fixture-only"}, O_RDONLY=0,
                        O_NOFOLLOW=0, O_NONBLOCK=0, open=lambda *_:5, read=lambda *_:event,
                        close=lambda *_:None)
            ns = selected("_run", os=fake_os, setup_phase=lambda *_:None)
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, "OWNER_DISPATCH_MODE"):
                ns["_run"](args, 100)


@unittest.skipUnless(os.name == "posix", "real local fork/pipe supervisor requires POSIX")
class PosixSupervisorTests(unittest.TestCase):
    def run_case(self, messages, admit_only):
        # A fresh local Python process owns stdout/fork/pipe. No private modules,
        # transport, credentials or provider entrypoints are loaded.
        code = r'''
import ast,hashlib,json,os,pathlib,re,sys,time,types
path=pathlib.Path(sys.argv[1]);tree=ast.parse(path.read_bytes())
def need(ok,code):
 if not ok:raise RuntimeError(code)
ns=dict(__file__=str(path),Path=pathlib.Path,hashlib=hashlib,json=json,os=os,re=re,sys=sys,types=types,
 need=need,OWNER_BUDGET_SECONDS=420,STATUS_PHASES=("ARMED","START_REQUESTED","START_ACKNOWLEDGED","RUNNING_OBSERVED"),
 ADMISSION_SOURCE_SHA256=hashlib.sha256(path.with_name("owner_admission.py").read_bytes()).hexdigest(),
 setup_refusal=lambda _: {"kind":"OWNER_SETUP_REFUSED"})
nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ("_public_frame","_supervise")]
exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),ns)
messages=json.loads(sys.argv[2])
def target(_):
 for value in messages:print(json.dumps(value),flush=True)
 return 0
raise SystemExit(ns['_supervise'](target,time.monotonic(),seconds=3,admit_only=sys.argv[3]=='true'))
'''
        return subprocess.run([sys.executable,"-I","-S","-B","-c",code,str(DRIVER),json.dumps(messages),
                               "true" if admit_only else "false"], capture_output=True, timeout=7, text=True)

    def test_admit_only_accepts_terminal_without_arm(self):
        result = self.run_case([frame()], True)
        self.assertEqual(result.returncode, 0, result.stderr)
        values = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(values[-1], frame())
        self.assertFalse(any(value.get("phase")=="ARMED" for value in values))

    def test_admit_only_rejects_child_arm_status(self):
        result = self.run_case([dict(kind="DIAGNOSTIC_STATUS",phase="ARMED"),frame()], True)
        self.assertEqual(result.returncode, 78, result.stderr)
        self.assertNotIn(admission.FRAME_KIND, result.stdout)
        self.assertIn("OWNER_DEADLINE_UNKNOWN", result.stdout)

    def test_other_modes_reject_admit_terminal(self):
        result = self.run_case([frame()], False)
        self.assertEqual(result.returncode, 78, result.stderr)
        self.assertNotIn(admission.FRAME_KIND, result.stdout)


if __name__ == "__main__":
    unittest.main()
