from __future__ import annotations

import asyncio
import json
import os
import unittest
import urllib.error
from datetime import datetime, timezone
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from fastapi import HTTPException
from pydantic import ValidationError

from broker_app.github import (
    BrokerContractError,
    BrokerRetryableError,
    BrokerConfig,
    REPOSITORY_API_PATH,
    REPOSITORY_FULL_NAME,
    ROLE_DISPATCH_MAILBOX_PR,
    ROLE_DISPATCH_TOKEN_PERMISSIONS,
    _authorize_github_operation,
    execute_bounded_role_dispatch,
    role_dispatch_branch_name,
    role_dispatch_comment_body,
    role_dispatch_file_path,
)
from broker_app.main import role_dispatch
from broker_app.policy import RoleDispatchRequest


NOW = 1_788_153_600
DISPATCH_ID = "550e8400-e29b-41d4-a716-446655440000"
FINGERPRINT = "a" * 64
BROKER_SECRET = "s" * 43
BASE_SHA = "b" * 40
BASE_TREE_SHA = "c" * 40
BLOB_SHA = "d" * 40
DISPATCH_TREE_SHA = "e" * 40
DISPATCH_COMMIT_SHA = "f" * 40


def _request(**overrides: object) -> RoleDispatchRequest:
    values: dict[str, object] = {
        "dispatch_id": DISPATCH_ID,
        "dispatch_epoch": 1,
        "prepared_at_epoch": NOW,
        "role": "RECOGNIZER",
        "task_fingerprint": FINGERPRINT,
        "target_pr": 1106,
        "mode": "READ_ONLY",
    }
    values.update(overrides)
    return RoleDispatchRequest(**values)


def _repair_request(**overrides: object) -> RoleDispatchRequest:
    values: dict[str, object] = {
        "mode": "REPAIR",
        "repair_attempt": 1,
        "origin_task_id": "550e8400-e29b-41d4-a716-446655440001",
        "prior_task_id": "550e8400-e29b-41d4-a716-446655440001",
        "blocked_result_code": "RECOGNIZER_READINESS_GAP",
        "blocked_summary": "Independent holdout evidence is missing.",
    }
    values.update(overrides)
    return _request(**values)


class _Response:
    def __init__(self, payload: object, *, url: str, status: int):
        self._payload = payload
        self._url = url
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def geturl(self):
        return self._url

    def read(self, limit: int):
        return json.dumps(self._payload).encode("utf-8")[:limit]


class _DispatchOpener:
    def __init__(self, request: RoleDispatchRequest, *, lose_post_response: bool = False):
        self.dispatch_request = request
        self.lose_post_response = lose_post_response
        self.requests = []
        self.branch_exists = False
        self.pull: dict[str, object] | None = None

    def _token(self) -> dict[str, object]:
        expires_at = datetime.fromtimestamp(
            NOW + 3_000, tz=timezone.utc
        ).isoformat().replace("+00:00", "Z")
        return {
            "token": "ghs_" + "x" * 36,
            "expires_at": expires_at,
            "repositories": [{"full_name": REPOSITORY_FULL_NAME}],
            "permissions": {**ROLE_DISPATCH_TOKEN_PERMISSIONS, "metadata": "read"},
        }

    def _branch(self) -> dict[str, object]:
        return {
            "ref": f"refs/heads/{role_dispatch_branch_name(self.dispatch_request)}",
            "object": {"sha": DISPATCH_COMMIT_SHA},
        }

    def _new_pull(self, body: dict[str, object]) -> dict[str, object]:
        number = 1_234
        return {
            "number": number,
            "html_url": f"https://github.com/{REPOSITORY_FULL_NAME}/pull/{number}",
            "state": "open",
            "draft": body["draft"],
            "title": body["title"],
            "body": body["body"],
            "head": {
                "ref": role_dispatch_branch_name(self.dispatch_request),
                "sha": DISPATCH_COMMIT_SHA,
            },
            "base": {"ref": "main"},
            "user": {
                "login": "bridge-school-oracle-autopilot[bot]",
                "type": "Bot",
            },
        }

    def open(self, request, *, timeout):
        self.requests.append(request)
        url = request.full_url
        if url.endswith("/access_tokens"):
            return _Response(self._token(), url=url, status=201)
        method = request.get_method()
        if method == "GET" and "/git/ref/heads/autopilot/dispatch/" in url:
            if not self.branch_exists:
                raise urllib.error.HTTPError(url, 404, "not found", {}, None)
            return _Response(self._branch(), url=url, status=200)
        if method == "GET" and url.endswith("/git/ref/heads/main"):
            return _Response(
                {"ref": "refs/heads/main", "object": {"sha": BASE_SHA}},
                url=url,
                status=200,
            )
        if method == "GET" and url.endswith(f"/git/commits/{BASE_SHA}"):
            return _Response(
                {
                    "sha": BASE_SHA,
                    "tree": {"sha": BASE_TREE_SHA},
                    "committer": {"date": "2026-09-01T00:00:00Z"},
                },
                url=url,
                status=200,
            )
        if method == "GET" and url.endswith(f"/git/commits/{DISPATCH_COMMIT_SHA}"):
            return _Response(
                {"sha": DISPATCH_COMMIT_SHA, "parents": [{"sha": BASE_SHA}]},
                url=url,
                status=200,
            )
        if method == "POST" and url.endswith("/git/blobs"):
            return _Response({"sha": BLOB_SHA}, url=url, status=201)
        if method == "POST" and url.endswith("/git/trees"):
            return _Response({"sha": DISPATCH_TREE_SHA}, url=url, status=201)
        if method == "POST" and url.endswith("/git/commits"):
            return _Response({"sha": DISPATCH_COMMIT_SHA}, url=url, status=201)
        if method == "POST" and url.endswith("/git/refs"):
            self.branch_exists = True
            return _Response(self._branch(), url=url, status=201)
        if method == "GET" and "/pulls?" in url:
            return _Response([] if self.pull is None else [self.pull], url=url, status=200)
        if method == "POST" and url.endswith("/pulls"):
            body = json.loads(request.data)
            self.pull = self._new_pull(body)
            if self.lose_post_response:
                self.lose_post_response = False
                raise TimeoutError
            return _Response(self.pull, url=url, status=201)
        raise AssertionError(f"unexpected request: {request.get_method()} {url}")


class RoleDispatchContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        cls.config = BrokerConfig(
            app_id=123,
            installation_id=456,
            private_key_pem=private_key_pem,
        )

    def test_request_is_strict_public_envelope(self):
        request = _request()
        self.assertEqual(request.mode, "READ_ONLY")
        self.assertNotIn("secret", request.model_dump_json())
        for overrides in (
            {"dispatch_id": "550E8400-E29B-41D4-A716-446655440000"},
            {"dispatch_id": "00000000-0000-1000-8000-000000000000"},
            {"dispatch_epoch": 0},
            {"prepared_at_epoch": 1_699_999_999},
            {"prepared_at_epoch": 4_102_444_801},
            {"role": "OWNER"},
            {"task_fingerprint": "A" * 64},
            {"target_pr": 0},
            {"mode": "WRITE"},
            {"extra": "blocked"},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                _request(**overrides)

    def test_public_envelope_matches_live_automation_contract_exactly(self):
        body = role_dispatch_comment_body(_request())
        self.assertEqual(
            body,
            "\n".join(
                (
                    "AUTOPILOT_DISPATCH_V1",
                    f"dispatch_id={DISPATCH_ID}",
                    "dispatch_epoch=1",
                    "role=RECOGNIZER",
                    f"task_fingerprint={FINGERPRINT}",
                    "target_pr=1106",
                    "mode=READ_ONLY",
                )
            ),
        )
        self.assertNotIn("ghs_", body)
        self.assertNotIn("Bearer", body)
        self.assertNotIn("prepared_at", body)
        self.assertEqual(
            role_dispatch_file_path(_request()),
            f"docs/evidence/autopilot/role-dispatch-{DISPATCH_ID}.md",
        )

    def test_repair_envelope_is_bounded_and_carries_only_safe_context(self):
        request = _repair_request()
        body = role_dispatch_comment_body(request)
        self.assertIn("mode=REPAIR", body)
        self.assertIn("repair_attempt=1", body)
        self.assertIn("blocked_result_code=RECOGNIZER_READINESS_GAP", body)
        self.assertIn("instruction=DIAGNOSE_MINIMAL_FIX_TEST_NO_MERGE", body)
        self.assertNotIn("Bearer", body)
        for overrides in (
            {"repair_attempt": 0},
            {"repair_attempt": 2},
            {"origin_task_id": None},
            {"prior_task_id": "not-a-uuid"},
            {"blocked_result_code": "not safe"},
            {"blocked_summary": "secret token must not be public"},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                _repair_request(**overrides)

    def test_repair_dispatch_preserves_context_in_attested_result(self):
        request = _repair_request()
        opener = _DispatchOpener(request)
        result = execute_bounded_role_dispatch(
            self.config, request, now_epoch=NOW, opener=opener
        )
        self.assertEqual(result["mode"], "REPAIR")
        self.assertEqual(result["repair_attempt"], 1)
        self.assertEqual(result["origin_task_id"], request.origin_task_id)
        self.assertEqual(result["prior_task_id"], request.prior_task_id)
        self.assertEqual(
            result["blocked_result_code"], request.blocked_result_code
        )

    def test_create_uses_separate_token_and_opens_exact_draft_pr(self):
        request = _request()
        opener = _DispatchOpener(request)
        result = execute_bounded_role_dispatch(
            self.config, request, now_epoch=NOW, opener=opener
        )
        self.assertEqual(result["status"], "created")
        self.assertFalse(result["replayed"])
        self.assertFalse(result["token_exposed"])
        token_request = opener.requests[0]
        self.assertEqual(
            json.loads(token_request.data)["permissions"],
            {"contents": "write", "pull_requests": "write"},
        )
        repository_requests = [r for r in opener.requests if "/repos/" in r.full_url]
        self.assertEqual(
            [r.get_method() for r in repository_requests],
            ["GET", "GET", "GET", "POST", "POST", "POST", "GET", "POST", "GET", "POST"],
        )
        self.assertTrue(repository_requests[-1].full_url.endswith("/pulls"))
        blob = next(r for r in repository_requests if r.full_url.endswith("/git/blobs"))
        self.assertEqual(
            json.loads(blob.data),
            {"content": role_dispatch_comment_body(request), "encoding": "utf-8"},
        )
        pull = json.loads(repository_requests[-1].data)
        self.assertTrue(pull["draft"])
        self.assertEqual(pull["body"], role_dispatch_comment_body(request))
        self.assertEqual(pull["head"], role_dispatch_branch_name(request))
        self.assertEqual(result["dispatch_pull_request"], 1_234)
        self.assertEqual(result["dispatch_commit_sha"], DISPATCH_COMMIT_SHA)
        self.assertEqual(
            result["dispatch_author_login"],
            "bridge-school-oracle-autopilot[bot]",
        )
        self.assertEqual(result["dispatch_author_type"], "Bot")
        self.assertFalse(result["production_mutation"])
        self.assertNotIn("ghs_", json.dumps(result))
        self.assertNotIn("prepared_at", json.dumps(result))

    def test_future_prepared_at_is_rejected_before_token_mint(self):
        request = _request(prepared_at_epoch=NOW + 61)
        opener = _DispatchOpener(request)
        with self.assertRaisesRegex(BrokerContractError, "PREPARED_AT_INVALID"):
            execute_bounded_role_dispatch(
                self.config, request, now_epoch=NOW, opener=opener
            )
        self.assertEqual(opener.requests, [])

    def test_response_loss_is_adopted_on_retry_without_duplicate_pr(self):
        request = _request()
        opener = _DispatchOpener(request, lose_post_response=True)
        with self.assertRaises(BrokerRetryableError):
            execute_bounded_role_dispatch(
                self.config, request, now_epoch=NOW, opener=opener
            )
        result = execute_bounded_role_dispatch(
            self.config, request, now_epoch=NOW, opener=opener
        )
        self.assertEqual(result["status"], "existing")
        self.assertTrue(result["replayed"])
        repository_posts = [
            r for r in opener.requests
            if r.get_method() == "POST" and r.full_url.endswith("/pulls")
        ]
        self.assertEqual(len(repository_posts), 1)

    def test_existing_pull_response_is_strict(self):
        request = _request()
        opener = _DispatchOpener(request)
        opener.branch_exists = True
        opener.pull = opener._new_pull(
            {
                "body": role_dispatch_comment_body(request),
                "draft": True,
                "title": f"[Autopilot dispatch] {request.role} {request.dispatch_id}",
            }
        )
        opener.pull["user"] = {"login": "different-valid-app[bot]", "type": "Bot"}
        with self.assertRaisesRegex(BrokerContractError, "PULL_INVALID"):
            execute_bounded_role_dispatch(
                self.config, request, now_epoch=NOW, opener=opener
            )

    def test_operation_allowlist_has_no_generic_issue_proxy(self):
        allowed = (
            f"{REPOSITORY_API_PATH}/git/ref/heads/"
            f"{role_dispatch_branch_name(_request())}"
        )
        _authorize_github_operation(method="GET", path=allowed)
        for method, path in (
            ("POST", f"{REPOSITORY_API_PATH}/issues/1150/comments"),
            ("GET", f"{REPOSITORY_API_PATH}/issues/1150"),
            ("PATCH", allowed),
            ("DELETE", allowed),
        ):
            with self.subTest(method=method, path=path), self.assertRaises(
                BrokerContractError
            ):
                _authorize_github_operation(method=method, path=path)

    def test_http_endpoint_returns_safe_attested_evidence(self):
        request = _request()
        safe = {
            "status": "created",
            "repository": REPOSITORY_FULL_NAME,
            "mailbox_pull_request": ROLE_DISPATCH_MAILBOX_PR,
            "dispatch_id": request.dispatch_id,
            "dispatch_epoch": request.dispatch_epoch,
            "role": request.role,
            "task_fingerprint": request.task_fingerprint,
            "target_pr": request.target_pr,
            "mode": request.mode,
            "dispatch_branch": role_dispatch_branch_name(request),
            "dispatch_commit_sha": DISPATCH_COMMIT_SHA,
            "dispatch_file": role_dispatch_file_path(request),
            "dispatch_pull_request": 123,
            "dispatch_pull_request_url": "https://github.com/example",
            "dispatch_author_login": "bridge-school-oracle-autopilot[bot]",
            "dispatch_author_type": "Bot",
            "draft": True,
            "replayed": False,
            "token_exposed": False,
            "production_mutation": False,
        }
        with patch.dict(
            os.environ,
            {
                "AUTOPILOT_TOKEN_BROKER_SECRET": BROKER_SECRET,
                "VERCEL_GIT_COMMIT_SHA": "a" * 40,
                "VERCEL_ENV": "preview",
            },
            clear=True,
        ), patch("broker_app.main.load_config", return_value=self.config), patch(
            "broker_app.main.execute_bounded_role_dispatch", return_value=safe
        ):
            response = asyncio.run(
                role_dispatch(
                    request, authorization=f"Bearer {BROKER_SECRET}"
                )
            )
        body = response.body.decode("utf-8")
        self.assertEqual(response.headers["cache-control"], "no-store, max-age=0")
        self.assertIn('"token_exposed":false', body)
        self.assertIn('"broker_policy_version":"physical-no-merge-v2"', body)
        self.assertNotIn("ghs_", body)

    def test_http_endpoint_hides_conflict_detail(self):
        request = _request()
        from broker_app.github import DraftRepairConflictError

        with patch.dict(
            os.environ,
            {
                "AUTOPILOT_TOKEN_BROKER_SECRET": BROKER_SECRET,
                "VERCEL_GIT_COMMIT_SHA": "a" * 40,
                "VERCEL_ENV": "preview",
            },
            clear=True,
        ), patch("broker_app.main.load_config", return_value=self.config), patch(
            "broker_app.main.execute_bounded_role_dispatch",
            side_effect=DraftRepairConflictError("private"),
        ):
            with self.assertRaises(HTTPException) as context:
                asyncio.run(
                    role_dispatch(
                        request, authorization=f"Bearer {BROKER_SECRET}"
                    )
                )
        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(context.exception.detail, "ROLE_DISPATCH_PRECONDITION_FAILED")


if __name__ == "__main__":
    unittest.main()
