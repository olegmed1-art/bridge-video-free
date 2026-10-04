"""Pure launch/entrypoint guards; database fault coverage lives in adapter_pg.py."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import subprocess
import types
from uuid import uuid4
import pytest
from .launch_contract import Launch, Permit, digest
from .fixed_adapter import compile_plan
from .resident_preflight import Refused
from . import owner_stage

NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)
C = "f513afc4a1a4cae325b7baeeef5f4848dd29a7a5"


def launch(school, now=NOW):
    _, fingerprint = compile_plan(school, C)
    return Launch.parse(dict(intent=str(uuid4()), code_sha=C, runtime_sha=C,
        deployment_sha=C, deployment_id="dpl_Original123", validation_build_id="dpl_Validation123",
        controller_run_id=123, plan_hash=fingerprint, module_hash=owner_stage.program_hash(),
        open_at=(now-timedelta(seconds=1)).isoformat().replace("+00:00","Z"),
        admission_until=(now+timedelta(minutes=4)).isoformat().replace("+00:00","Z"),
        stage_until=(now+timedelta(minutes=14)).isoformat().replace("+00:00","Z")))


def permit(l, stage, now=NOW):
    return Permit(l.fingerprint, stage,
        {"baseline":"preflight","initial":"baseline","revoke":"active","reactivate":"revoked"}[stage],
        "a"*64, "b"*64, now)


def environment(l, stage, p=None):
    from .owner_probe import REPOSITORY, WORKFLOW
    return dict(GITHUB_REPOSITORY=REPOSITORY, GITHUB_REF="refs/heads/main",
        GITHUB_EVENT_NAME="workflow_dispatch",GITHUB_ACTOR="olegmed1-art",
        GITHUB_TRIGGERING_ACTOR="olegmed1-art",GITHUB_RUN_ATTEMPT="1",GITHUB_RUN_ID="456",
        GITHUB_WORKFLOW_REF=REPOSITORY+"/"+WORKFLOW+"@refs/heads/main",
        EXPECTED_MAIN=l.runtime_sha,GITHUB_SHA=l.runtime_sha,CANON_STAGE_SCOPE=stage,
        EXPECTED_CANON_CONTRACT=l.fingerprint,
        EXPECTED_CANON_PERMIT=digest(p.public()) if p else "")


class Guarded(dict):
    def get(self, key, default=None):
        assert key not in ("NATIVE_OWNER_DATABASE_URL", "GH_TOKEN"), "Credential accessed before guard"
        return super().get(key, default)
    def __iter__(self):
        raise AssertionError("Environment iteration")


@pytest.mark.parametrize("change", [
    {"GITHUB_RUN_ATTEMPT":"2"},{"GITHUB_ACTOR":"other"},{"GITHUB_REF":"refs/heads/test"},
    {"GITHUB_EVENT_NAME":"push"},{"EXPECTED_CANON_CONTRACT":"0"*64},
    {"EXPECTED_CANON_PERMIT":"0"*64},{"CANON_STAGE_SCOPE":"emergency"},
    {"GITHUB_SHA":"0"*40}])
def test_real_invoke_context_before_credentials(change):
    l=launch(uuid4()); p=permit(l,"baseline")
    env=Guarded(environment(l,"baseline",p)|change)
    with pytest.raises(Refused):
        owner_stage.invoke(env,lambda *a:l.runtime_sha,l,"baseline",p,
            connect=lambda **kw:pytest.fail("connect"),check_source=lambda s:pytest.fail("source"),
            now=lambda:NOW)


def test_expired_invoke_before_credentials():
    l=launch(uuid4());p=permit(l,"baseline")
    with pytest.raises(Refused,match="stage_window"):
        owner_stage.invoke(Guarded(environment(l,"baseline",p)),lambda *a:l.runtime_sha,l,
            "baseline",p,connect=lambda **kw:pytest.fail("connect"),
            check_source=lambda s:pytest.fail("source"),now=lambda:NOW+timedelta(days=1))


def test_recovery_context_independent_of_normal_window():
    l=launch(uuid4())
    owner_stage.context(Guarded(environment(l,"emergency")),lambda *a:l.runtime_sha,
                        l,"emergency",None,NOW+timedelta(days=2))


@pytest.mark.parametrize("change", [
    {"admission_until":"2026-10-05T00:20:00Z"}, {"stage_until":"2026-10-05T00:30:00Z"},
    {"deployment_sha":"0"*40},{"validation_build_id":"dpl_Original123"},
    {"controller_run_id":True},{"intent":"missing"},{"open_at":"2026-10-05T00:00:00"}])
def test_launch_rejects_unbounded_or_misbound(change):
    with pytest.raises(Refused):
        Launch.parse(launch(uuid4()).public()|change)


def test_metadata_only_compiler_change():
    # Execute immutable baseline compiler text in its original package namespace.
    original = subprocess.check_output(["git","show",
        "5c5dd778a2c6b40b899981378b911f89845d76ff:tools/canon_auth/pilot_sql.py"],text=True)
    old=types.ModuleType("tools.canon_auth.golden_compiler")
    exec(compile(original,"immutable-baseline","exec"),old.__dict__)
    from .pilot_sql import plan
    school=uuid4();before=old.plan(school,C);after=plan(school,C)
    assert {k:after[k] for k in before} == before
    assert len(after["declared_rows"]) == 42


def test_real_owner_invoke_supervisor_before_connection():
    import time
    l=launch(uuid4())
    l=Launch.parse(l.public()|{"admission_until":"2026-10-05T00:00:00.300000Z",
                              "stage_until":"2026-10-05T00:00:00.300000Z"})
    p=permit(l,"baseline")
    reached=[]
    def blocked(*args):
        reached.append(True);time.sleep(2)
        raise AssertionError("Owner supervisor failed")
    with patch.object(owner_stage,"_invoke_verified",blocked):
        with pytest.raises(Refused,match="owner_stage_process_deadline"):
            owner_stage.invoke(Guarded(environment(l,"baseline",p)),lambda *a:l.runtime_sha,
                l,"baseline",p,connect=None,check_source=None,now=lambda:NOW)
    assert reached


def transition(l, now=NOW):
    return dict(purpose="owned-emergency-revoke-only",code_sha=l.code_sha,runtime_sha=l.runtime_sha,
        plan_hash=l.plan_hash,contract_hash=l.fingerprint,module_hash=l.module_hash,
        checkout_tree="c"*40,current_main="d"*40,reviewed_by="olegmed1-art",
        authorized_at=(now-timedelta(seconds=1)).isoformat().replace("+00:00","Z"),
        expires_at=(now+timedelta(minutes=10)).isoformat().replace("+00:00","Z"))


def transition_git(l, value):
    def git(*args):
        return l.runtime_sha if args==("rev-parse","HEAD") else (
            value["checkout_tree"] if args==("rev-parse","HEAD^{tree}") else "")
    return git


def test_reviewed_transition_allows_only_pinned_owned_revoke_on_exact_new_main():
    l=launch(uuid4());value=transition(l)
    env=Guarded(environment(l,"emergency")|dict(EXPECTED_RECOVERY_TRANSITION=digest(value),
        EXPECTED_MAIN=value["current_main"],GITHUB_SHA=value["current_main"]))
    assert owner_stage.context(env,transition_git(l,value),l,"emergency",None,NOW,value)==value["current_main"]


@pytest.mark.parametrize("change",[
    {"contract_hash":"0"*64},{"code_sha":"0"*40},{"plan_hash":"0"*64},
    {"runtime_sha":"0"*40},{"module_hash":"0"*64},{"reviewed_by":"other"},
    {"expires_at":"2026-10-04T23:59:59Z"},{"purpose":"activate"}])
def test_transition_cannot_change_original_tuple(change):
    l=launch(uuid4());value=transition(l)|change
    env=Guarded(environment(l,"emergency")|dict(EXPECTED_RECOVERY_TRANSITION=digest(value),
        EXPECTED_MAIN=value["current_main"],GITHUB_SHA=value["current_main"]))
    with pytest.raises(Refused):
        owner_stage.invoke(env,transition_git(l,value),l,"emergency",None,
            connect=lambda **kw:pytest.fail("credential"),check_source=lambda s:pytest.fail("source"),
            now=lambda:NOW,transition=value)


def test_transition_cannot_admit_normal_write():
    l=launch(uuid4());value=transition(l);p=permit(l,"baseline")
    env=Guarded(environment(l,"baseline",p)|{"EXPECTED_RECOVERY_TRANSITION":digest(value)})
    with pytest.raises(Refused,match="revoke_only"):
        owner_stage.invoke(env,transition_git(l,value),l,"baseline",p,
            connect=None,check_source=None,now=lambda:NOW,transition=value)


def test_transition_requires_reviewed_digest_and_entire_checkout_tree():
    l=launch(uuid4());value=transition(l)
    env=Guarded(environment(l,"emergency")|dict(EXPECTED_MAIN=value["current_main"],
        GITHUB_SHA=value["current_main"],EXPECTED_RECOVERY_TRANSITION="0"*64))
    with pytest.raises(Refused,match="reviewed_recovery_transition"):
        owner_stage.context(env,transition_git(l,value),l,"emergency",None,NOW,value)
    env["EXPECTED_RECOVERY_TRANSITION"]=digest(value)
    with pytest.raises(Refused,match="pinned_recovery_checkout"):
        owner_stage.context(env,lambda *a:l.runtime_sha,l,"emergency",None,NOW,value)
