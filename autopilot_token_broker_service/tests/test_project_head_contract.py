from __future__ import annotations

import asyncio
import json
import os
import unittest
import urllib.error
from datetime import datetime, timezone
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError

from broker_app.github import (
    BrokerConfig,
    BrokerContractError,
    PROJECT_HEAD_TOKEN_PERMISSIONS,
    REPOSITORY_API_PATH,
    REPOSITORY_FULL_NAME,
    _authorize_github_operation,
    execute_bounded_project_head,
)
from broker_app.main import project_head
from broker_app.policy import ProjectHeadRequest


NOW = 1_788_153_600
BROKER_SECRET = "s" * 43
HEAD_SHA = "a" * 40


class _Response:
    def __init__(self, payload: object, *, url: str, status: int):
        self.payload = payload
        self.url = url
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def geturl(self):
        return self.url

    def read(self, limit: int):
        return json.dumps(self.payload).encode("utf-8")[:limit]


class _ProjectHeadOpener:
    def __init__(
        self, *, state: str = "open", head_sha: str = HEAD_SHA, missing: bool = False
    ):
        self.state = state
        self.head_sha = head_sha
        self.missing = missing
        self.requests = []

    def open(self, request, *, timeout):
        self.requests.append(request)
        url = request.full_url
        if url.endswith("/access_tokens"):
            expires_at = datetime.fromtimestamp(
                NOW + 3_000, tz=timezone.utc
            ).isoformat().replace("+00:00", "Z")
            return _Response(
                {
                    "token": "ghs_" + "x" * 36,
                    "expires_at": expires_at,
                    "repositories": [{"full_name": REPOSITORY_FULL_NAME}],
                    "permissions": {
                        **PROJECT_HEAD_TOKEN_PERMISSIONS,
                        "metadata": "read",
                    },
                },
                url=url,
                status=201,
            )
        if url.endswith("/pulls/42"):
            if self.missing:
                raise urllib.error.HTTPError(url, 404, "not found", {}, None)
            return _Response(
                {
                    "number": 42,
                    "html_url": f"https://github.com/{REPOSITORY_FULL_NAME}/pull/42",
                    "state": self.state,
                    "head": {"sha": self.head_sha},
                },
                url=url,
                status=200,
            )
        raise AssertionError(f"unexpected request: {request.get_method()} {url}")


class ProjectHeadContractTests(unittest.TestCase):
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

    def test_request_is_exact_and_strict(self):
        request = ProjectHeadRequest(repository=REPOSITORY_FULL_NAME, pr_number=42)
        self.assertEqual(request.pr_number, 42)
        for values in (
            {"repository": "different/repository", "pr_number": 42},
            {"repository": REPOSITORY_FULL_NAME, "pr_number": 0},
            {"repository": REPOSITORY_FULL_NAME, "pr_number": 42, "extra": True},
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                ProjectHeadRequest(**values)

    def test_only_canonical_numeric_pr_paths_are_allowed(self):
        _authorize_github_operation(
            method="GET", path=f"{REPOSITORY_API_PATH}/pulls/42"
        )
        _authorize_github_operation(
            method="GET", path=f"{REPOSITORY_API_PATH}/pulls/1000000"
        )
        for path in (
            f"{REPOSITORY_API_PATH}/pulls/0",
            f"{REPOSITORY_API_PATH}/pulls/01",
            f"{REPOSITORY_API_PATH}/pulls/1000001",
            f"{REPOSITORY_API_PATH}/pulls/42/comments",
            f"{REPOSITORY_API_PATH}/pulls/42?extra=true",
        ):
            with self.subTest(path=path), self.assertRaises(BrokerContractError):
                _authorize_github_operation(method="GET", path=path)

    def test_executor_uses_read_only_token_and_returns_minimal_evidence(self):
        request = ProjectHeadRequest(repository=REPOSITORY_FULL_NAME, pr_number=42)
        opener = _ProjectHeadOpener(state="closed")
        result = execute_bounded_project_head(
            self.config, request, now_epoch=NOW, opener=opener
        )
        self.assertEqual(result["head_sha"], HEAD_SHA)
        self.assertFalse(result["open"])
        self.assertFalse(result["token_exposed"])
        self.assertFalse(result["production_mutation"])
        self.assertNotIn("ghs_", json.dumps(result))
        token_request = opener.requests[0]
        self.assertEqual(
            json.loads(token_request.data)["permissions"],
            {"pull_requests": "read"},
        )
        self.assertEqual(
            [request.get_method() for request in opener.requests], ["POST", "GET"]
        )

    def test_executor_rejects_forged_head(self):
        request = ProjectHeadRequest(repository=REPOSITORY_FULL_NAME, pr_number=42)
        opener = _ProjectHeadOpener(head_sha="not-a-sha")
        with self.assertRaisesRegex(BrokerContractError, "RESPONSE_INVALID"):
            execute_bounded_project_head(
                self.config, request, now_epoch=NOW, opener=opener
            )

    def test_executor_preserves_not_found_as_a_distinct_outcome(self):
        from broker_app.github import ProjectHeadNotFoundError

        request = ProjectHeadRequest(repository=REPOSITORY_FULL_NAME, pr_number=42)
        with self.assertRaises(ProjectHeadNotFoundError):
            execute_bounded_project_head(
                self.config,
                request,
                now_epoch=NOW,
                opener=_ProjectHeadOpener(missing=True),
            )

    def test_http_endpoint_returns_attested_token_free_result(self):
        request = ProjectHeadRequest(repository=REPOSITORY_FULL_NAME, pr_number=42)
        safe = {
            "repository": REPOSITORY_FULL_NAME,
            "pr_number": 42,
            "open": True,
            "head_sha": HEAD_SHA,
            "http_method": "GET",
            "token_exposed": False,
            "production_mutation": False,
            "operation_count": 2,
        }
        with patch.dict(
            os.environ,
            {
                "AUTOPILOT_TOKEN_BROKER_SECRET": BROKER_SECRET,
                "VERCEL_ENV": "preview",
            },
            clear=True,
        ), patch("broker_app.main.load_config", return_value=self.config), patch(
            "broker_app.main.execute_bounded_project_head", return_value=safe
        ):
            response = asyncio.run(
                project_head(request, authorization=f"Bearer {BROKER_SECRET}")
            )
        body = response.body.decode("utf-8")
        self.assertEqual(response.headers["cache-control"], "no-store, max-age=0")
        self.assertIn('"token_exposed":false', body)
        self.assertIn('"http_method":"GET"', body)
        self.assertNotIn("ghs_", body)


if __name__ == "__main__":
    unittest.main()
