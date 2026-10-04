"""Local HTTP harness for the existing app:app. Never starts a listening server."""
from contextlib import contextmanager, ExitStack
from unittest.mock import patch
import socket


@contextmanager
def offline_client(*, enabled=False):
    import app as entrypoint
    import psycopg
    from fastapi.testclient import TestClient
    from bridge_school_api import ai, ai_teacher, ai_decision, db, knowledge
    from bridge_school_api.main import require_api_token
    from bridge_school_api.tournament_teacher_test_adapter import OfflineTournamentTeacherTarget

    def forbidden(*args,**kwargs):
        raise AssertionError("Offline harness prohibits database/network access")

    with ExitStack() as stack:
        # Suppress any inherited deployment credentials; only local auth is stubbed.
        stack.enter_context(patch.dict("os.environ",{"VERCEL_OIDC_TOKEN":"","BRIDGE_API_TOKEN":""}))
        stack.enter_context(patch.dict(entrypoint.app.dependency_overrides,{require_api_token:lambda:None}))
        stack.enter_context(patch.object(entrypoint.app.state,"tournament_teacher_test_target",
                                       OfflineTournamentTeacherTarget() if enabled else None,create=True))
        for module in (ai,ai_teacher,ai_decision,db,knowledge,psycopg):
            stack.enter_context(patch.object(module,"connect",forbidden))
        with TestClient(entrypoint.app) as client:
            # After Windows initializes the event loop's internal socket pair.
            with patch.object(socket.socket,"connect",forbidden):
                yield client


def request_body(context):
    from bridge_school_api.tournament_teacher_test_adapter import (
        TEACHER_KEY,TEACHER_VERSION,PROFILE,SOURCE_VERSION,
    )
    return dict(teacher_key=TEACHER_KEY,teacher_version=TEACHER_VERSION,
                teacher_system=PROFILE,test_request=context,test_source_version=SOURCE_VERSION)


def request_path(context):
    from bridge_school_api.tournament_teacher_test_adapter import position_uuid
    return f"/v1/ai/positions/{position_uuid(context)}/teacher-evidence"
