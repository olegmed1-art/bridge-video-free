from __future__ import annotations

import asyncio
import json
import os
import unittest
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
    role_dispatch_comment_body,
)
from broker_app.main import role_dispatch
from broker_app.policy import RoleDispatchRequest


NOW = 1_788_153_600
DISPATCH_ID = "550e8400-e29b-41d4-a716-446655440000"
FINGERPRINT = "a" * 64
BROKER_SECRET = "s" * 43


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
        self.comment: dict[str, object] | None = None

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

    def _new_comment(self, body: str) -> dict[str, object]:
        comment_id = 9_876_543_210
        stamp = "2026-09-01T00:00:01Z"
        return {
            "id": comment_id,
            "body": body,
            "issue_url": (
                f"https://api.github.com{REPOSITORY_API_PATH}/issues/"
                f"{ROLE_DISPATCH_MAILBOX_PR}"
            ),
            "html_url": (
                f"https://github.com/{REPOSITORY_FULL_NAME}/pull/"
                f"{ROLE_DISPATCH_MAILBOX_PR}#issuecomment-{comment_id}"
            ),
            "created_at": stamp,
            "updated_at": stamp,
            "user": {"login": "bridge-autopilot[bot]", "type": "Bot"},
        }

    def open(self, request, *, timeout):
        self.requests.append(request)
        url = request.full_url
        if url.endswith("/access_tokens"):
            return _Response(self._token(), url=url, status=201)
        if request.get_method() == "GET" and "/issues/1150/comments?" in url:
            payload = [] if self.comment is None else [self.comment]
            return _Response(payload, url=url, status=200)
        if request.get_method() == "POST" and url.endswith("/issues/1150/comments"):
            body = json.loads(request.data)["body"]
            self.comment = self._new_comment(body)
            if self.lose_post_response:
                self.lose_post_response = False
                raise TimeoutError
            return _Response(self.comment, url=url, status=201)
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

    def test_body_matches_live_automation_contract_exactly(self):
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

    def test_create_uses_separate_least_permission_token_and_exact_routes(self):
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
            {"pull_requests": "write"},
        )
        repository_requests = [r for r in opener.requests if "/repos/" in r.full_url]
        self.assertEqual([r.get_method() for r in repository_requests], ["GET", "POST"])
        self.assertTrue(repository_requests[-1].full_url.endswith("/issues/1150/comments"))
        self.assertIn("since=2026-08-31T05%3A15%3A00Z", repository_requests[0].full_url)
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

    def test_response_loss_is_adopted_on_retry_without_duplicate_post(self):
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
            if r.get_method() == "POST" and "/repos/" in r.full_url
        ]
        self.assertEqual(len(repository_posts), 1)

    def test_comment_response_is_strict(self):
        request = _request()
        opener = _DispatchOpener(request)
        opener.comment = opener._new_comment(role_dispatch_comment_body(request))
        opener.comment["user"] = {"login": "human", "type": "User"}
        with self.assertRaisesRegex(BrokerContractError, "COMMENT_INVALID"):
            execute_bounded_role_dispatch(
                self.config, request, now_epoch=NOW, opener=opener
            )

    def test_operation_allowlist_has_no_generic_issue_proxy(self):
        allowed = f"{REPOSITORY_API_PATH}/issues/1150/comments"
        _authorize_github_operation(method="POST", path=allowed)
        for method, path in (
            ("POST", f"{REPOSITORY_API_PATH}/issues/1149/comments"),
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
            "comment_id": 123,
            "comment_url": "https://github.com/example",
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
