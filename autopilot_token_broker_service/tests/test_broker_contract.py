from __future__ import annotations

import asyncio
import base64
import json
import os
import unittest
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import HTTPException
from pydantic import ValidationError

from autopilot_phase3b.policy import (
    FileChange as CanonicalFileChange,
    RepairRequest as CanonicalRepairRequest,
    repair_fingerprint as canonical_repair_fingerprint,
)
from broker_app.github import (
    BrokerConfigurationError,
    BrokerContractError,
    BrokerRetryableError,
    BrokerConfig,
    DraftRepairConflictError,
    REPOSITORY_FULL_NAME,
    TOKEN_PERMISSIONS,
    build_app_jwt,
    execute_bounded_draft_repair,
    issue_installation_token,
    load_config,
)
from broker_app.main import (
    _require_broker_authorization,
    _require_preview_runtime,
    draft_repair,
    healthz,
)
from broker_app.policy import (
    DraftRepairRequest,
    RepairFileChange,
    expected_branch_name,
    repair_fingerprint,
)


NOW = 1_788_153_600
STRONG_BROKER_SECRET = "s" * 43
BASE_SHA = "a" * 40
BASE_TREE_SHA = "b" * 40
BLOB_SHA = "c" * 40
TREE_SHA = "d" * 40
COMMIT_SHA = "e" * 40


def _decode_segment(value: str) -> dict[str, object]:
    padding_bytes = "=" * (-len(value) % 4)
    return json.loads(base64.urlsafe_b64decode(value + padding_bytes))


def _repair_request(
    *,
    task_key: str = "phase3b-canary-20260831",
    path: str = "docs/evidence/autopilot/phase3b-canary.md",
    operation: str = "CREATE",
    expected_blob_sha: str | None = None,
) -> DraftRepairRequest:
    change = RepairFileChange(
        path=path,
        operation=operation,
        content_utf8="Phase 3B bounded draft canary.\n",
        expected_blob_sha=expected_blob_sha,
    )
    values = {
        "allow_force_push": False,
        "allow_merge": False,
        "base_branch": "main",
        "branch_name": expected_branch_name(task_key),
        "changes": (change,),
        "expected_base_sha": BASE_SHA,
        "production_mutation": False,
        "repository": REPOSITORY_FULL_NAME,
        "require_draft": True,
        "task_key": task_key,
        "title": "[Autopilot draft] Phase 3B bounded canary",
    }
    return DraftRepairRequest(
        **values,
        action_fingerprint=repair_fingerprint(values),
        manifest_version=1,
    )


class _FakeResponse:
    def __init__(self, payload: dict[str, object], *, url: str, status: int = 200):
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


class _TokenOpener:
    def __init__(self, payload: dict[str, object]):
        self.payload = payload
        self.request = None
        self.timeout = None

    def open(self, request, *, timeout):
        self.request = request
        self.timeout = timeout
        return _FakeResponse(self.payload, url=request.full_url, status=201)


class _RepairOpener:
    def __init__(
        self,
        token_payload: dict[str, object],
        *,
        base_sha: str = BASE_SHA,
        branch_exists: bool = False,
    ):
        self.token_payload = token_payload
        self.base_sha = base_sha
        self.branch_exists = branch_exists
        self.requests = []

    def open(self, request, *, timeout):
        self.requests.append(request)
        parsed = urllib.parse.urlsplit(request.full_url)
        path = parsed.path
        method = request.get_method()
        body = json.loads(request.data) if request.data is not None else None

        if path.endswith("/access_tokens"):
            return _FakeResponse(
                self.token_payload, url=request.full_url, status=201
            )
        if path.endswith("/git/ref/heads/main") and method == "GET":
            return _FakeResponse(
                {"object": {"sha": self.base_sha}}, url=request.full_url
            )
        if path.endswith(f"/git/commits/{BASE_SHA}") and method == "GET":
            return _FakeResponse(
                {"sha": BASE_SHA, "tree": {"sha": BASE_TREE_SHA}},
                url=request.full_url,
            )
        if "/git/ref/heads/autopilot/repair/" in path and method == "GET":
            if self.branch_exists:
                return _FakeResponse(
                    {"ref": "refs/heads/autopilot/repair/existing"},
                    url=request.full_url,
                )
            raise urllib.error.HTTPError(
                request.full_url, 404, "Not Found", {}, None
            )
        if "/contents/docs/evidence/autopilot/phase3b-canary.md" in path:
            raise urllib.error.HTTPError(
                request.full_url, 404, "Not Found", {}, None
            )
        if path.endswith("/git/blobs") and method == "POST":
            assert body == {
                "content": "Phase 3B bounded draft canary.\n",
                "encoding": "utf-8",
            }
            return _FakeResponse({"sha": BLOB_SHA}, url=request.full_url, status=201)
        if path.endswith("/git/trees") and method == "POST":
            assert body == {
                "base_tree": BASE_TREE_SHA,
                "tree": [
                    {
                        "mode": "100644",
                        "path": "docs/evidence/autopilot/phase3b-canary.md",
                        "sha": BLOB_SHA,
                        "type": "blob",
                    }
                ],
            }
            return _FakeResponse({"sha": TREE_SHA}, url=request.full_url, status=201)
        if path.endswith("/git/commits") and method == "POST":
            assert body["parents"] == [BASE_SHA]
            assert body["tree"] == TREE_SHA
            return _FakeResponse(
                {"sha": COMMIT_SHA}, url=request.full_url, status=201
            )
        if path.endswith("/git/refs") and method == "POST":
            assert body["sha"] == COMMIT_SHA
            return _FakeResponse(
                {"ref": body["ref"], "object": {"sha": COMMIT_SHA}},
                url=request.full_url,
                status=201,
            )
        if path.endswith("/pulls") and method == "POST":
            return _FakeResponse(
                {
                    "number": 1234,
                    "html_url": (
                        "https://github.com/olegmed1-art/bridge-video-free/pull/1234"
                    ),
                    "draft": True,
                    "head": {"ref": body["head"], "sha": COMMIT_SHA},
                    "base": {"ref": "main", "sha": BASE_SHA},
                },
                url=request.full_url,
                status=201,
            )
        raise AssertionError(f"unexpected request: {method} {request.full_url}")


class BrokerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.private_key = rsa.generate_private_key(
            public_exponent=65_537, key_size=2_048
        )
        cls.private_key_pem = cls.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        cls.config = BrokerConfig(
            app_id=4_776_443,
            installation_id=12_345_678,
            private_key_pem=cls.private_key_pem,
        )

    def _token_payload(self, **overrides):
        values = {
            "token": "ghs_" + "x" * 36,
            "expires_at": datetime.fromtimestamp(
                NOW + 3_600, tz=timezone.utc
            ).isoformat().replace("+00:00", "Z"),
            "repositories": [{"full_name": REPOSITORY_FULL_NAME}],
            "permissions": {**TOKEN_PERMISSIONS, "metadata": "read"},
        }
        values.update(overrides)
        return values

    def test_config_requires_numeric_ids_and_rsa_key(self):
        config = load_config(
            {
                "AUTOPILOT_GITHUB_APP_ID": "4776443",
                "AUTOPILOT_GITHUB_INSTALLATION_ID": "12345678",
                "AUTOPILOT_GITHUB_PRIVATE_KEY": self.private_key_pem,
            }
        )
        self.assertEqual(config.app_id, 4_776_443)
        with self.assertRaisesRegex(BrokerConfigurationError, "GITHUB_APP_ID_INVALID"):
            load_config(
                {
                    "AUTOPILOT_GITHUB_APP_ID": "not-an-id",
                    "AUTOPILOT_GITHUB_INSTALLATION_ID": "12345678",
                    "AUTOPILOT_GITHUB_PRIVATE_KEY": self.private_key_pem,
                }
            )
        with self.assertRaisesRegex(
            BrokerConfigurationError, "GITHUB_APP_PRIVATE_KEY_INVALID"
        ):
            load_config(
                {
                    "AUTOPILOT_GITHUB_APP_ID": "4776443",
                    "AUTOPILOT_GITHUB_INSTALLATION_ID": "12345678",
                    "AUTOPILOT_GITHUB_PRIVATE_KEY": "not-a-key",
                }
            )

    def test_app_jwt_is_short_lived_and_signed_by_pinned_key(self):
        token = build_app_jwt(self.config, now_epoch=NOW)
        header, payload, signature = token.split(".")
        self.assertEqual(_decode_segment(header), {"alg": "RS256", "typ": "JWT"})
        self.assertEqual(
            _decode_segment(payload),
            {"exp": NOW + 540, "iat": NOW - 60, "iss": 4_776_443},
        )
        signature_bytes = base64.urlsafe_b64decode(
            signature + "=" * (-len(signature) % 4)
        )
        self.private_key.public_key().verify(
            signature_bytes,
            f"{header}.{payload}".encode("ascii"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )

    def test_installation_credential_is_repository_and_permission_scoped(self):
        opener = _TokenOpener(self._token_payload())
        credential = issue_installation_token(
            self.config, now_epoch=NOW, opener=opener
        )
        self.assertTrue(credential.token.startswith("ghs_"))
        self.assertEqual(opener.timeout, 15)
        request_body = json.loads(opener.request.data)
        self.assertEqual(request_body["repositories"], ["bridge-video-free"])
        self.assertEqual(request_body["permissions"], TOKEN_PERMISSIONS)
        self.assertTrue(opener.request.headers["Authorization"].startswith("Bearer "))
        self.assertNotIn(self.private_key_pem, str(opener.request.headers))

    def test_unexpected_repository_or_permission_fails_closed(self):
        for payload in (
            self._token_payload(repositories=[{"full_name": "other/repo"}]),
            self._token_payload(
                permissions={**TOKEN_PERMISSIONS, "administration": "write"}
            ),
            self._token_payload(permissions={"contents": "write"}),
        ):
            with self.subTest(payload=payload), self.assertRaises(BrokerContractError):
                issue_installation_token(
                    self.config,
                    now_epoch=NOW,
                    opener=_TokenOpener(payload),
                )

    def test_expired_or_unbounded_token_fails_closed(self):
        for lifetime in (-1, 4_000):
            payload = self._token_payload(
                expires_at=datetime.fromtimestamp(
                    NOW + lifetime, tz=timezone.utc
                ).isoformat().replace("+00:00", "Z")
            )
            with self.subTest(lifetime=lifetime), self.assertRaisesRegex(
                BrokerContractError, "EXPIRY_INVALID"
            ):
                issue_installation_token(
                    self.config,
                    now_epoch=NOW,
                    opener=_TokenOpener(payload),
                )

    def test_transient_github_error_is_separate_from_contract_error(self):
        class FailingOpener:
            def open(self, request, *, timeout):
                raise TimeoutError

        with self.assertRaises(BrokerRetryableError):
            issue_installation_token(
                self.config, now_epoch=NOW, opener=FailingOpener()
            )

    def test_ingress_auth_fails_closed_and_uses_strong_secret(self):
        for env in ({}, {"AUTOPILOT_TOKEN_BROKER_SECRET": "weak"}):
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True):
                with self.assertRaises(HTTPException) as context:
                    _require_broker_authorization("Bearer anything")
            self.assertEqual(context.exception.status_code, 503)
        with patch.dict(
            os.environ,
            {"AUTOPILOT_TOKEN_BROKER_SECRET": STRONG_BROKER_SECRET},
            clear=True,
        ):
            with self.assertRaises(HTTPException) as context:
                _require_broker_authorization("Bearer wrong")
            self.assertEqual(context.exception.status_code, 401)
            _require_broker_authorization(f"Bearer {STRONG_BROKER_SECRET}")

    def test_request_recomputes_fingerprint_and_rejects_wider_capabilities(self):
        request = _repair_request()
        self.assertEqual(request.repository, REPOSITORY_FULL_NAME)
        self.assertTrue(request.branch_name.startswith("autopilot/repair/"))
        raw = request.model_dump()
        raw["action_fingerprint"] = "f" * 64
        with self.assertRaises(ValidationError):
            DraftRepairRequest(**raw)

    def test_json_request_and_canonical_policy_have_identical_fingerprint(self):
        request = _repair_request()
        decoded = DraftRepairRequest.model_validate_json(request.model_dump_json())
        self.assertEqual(decoded, request)
        canonical = CanonicalRepairRequest(
            task_key=request.task_key,
            repository=request.repository,
            base_branch=request.base_branch,
            expected_base_sha=request.expected_base_sha,
            branch_name=request.branch_name,
            title=request.title,
            changes=tuple(
                CanonicalFileChange(
                    path=change.path,
                    operation=change.operation,
                    content_utf8=change.content_utf8,
                    expected_blob_sha=change.expected_blob_sha,
                )
                for change in request.changes
            ),
            require_draft=request.require_draft,
            allow_merge=request.allow_merge,
            allow_force_push=request.allow_force_push,
            production_mutation=request.production_mutation,
        )
        self.assertEqual(
            request.action_fingerprint, canonical_repair_fingerprint(canonical)
        )
        with self.assertRaises(ValidationError):
            _repair_request(path=".github/workflows/unsafe.yml")
        with self.assertRaises(ValidationError):
            _repair_request(operation="UPDATE")
        raw = request.model_dump()
        raw["allow_merge"] = True
        with self.assertRaises(ValidationError):
            DraftRepairRequest(**raw)

    def test_health_is_preview_only_and_never_exposes_raw_token(self):
        with patch.dict(os.environ, {}, clear=True):
            payload = asyncio.run(healthz())
        self.assertTrue(payload["preview_only"])
        self.assertFalse(payload["production_mutations_enabled"])
        self.assertFalse(payload["github_token_broker_enabled"])
        self.assertFalse(payload["raw_installation_token_exposed"])

    def test_runtime_guard_rejects_every_non_preview_environment(self):
        for value in ("", "production", "development"):
            with self.subTest(value=value), patch.dict(
                os.environ, {"VERCEL_ENV": value}, clear=True
            ), self.assertRaises(HTTPException) as context:
                _require_preview_runtime()
            self.assertEqual(context.exception.status_code, 503)
            self.assertEqual(context.exception.detail, "TOKEN_BROKER_PREVIEW_ONLY")
        with patch.dict(os.environ, {"VERCEL_ENV": "preview"}, clear=True):
            _require_preview_runtime()

    def test_bounded_executor_keeps_token_internal_and_uses_exact_sequence(self):
        request = _repair_request()
        opener = _RepairOpener(self._token_payload())
        result = execute_bounded_draft_repair(
            self.config, request, now_epoch=NOW, opener=opener
        )
        encoded = json.dumps(result, sort_keys=True)
        self.assertNotIn("ghs_", encoded)
        self.assertNotIn("expires_at", encoded)
        self.assertFalse(result["token_exposed"])
        self.assertTrue(result["draft"])
        self.assertFalse(result["merge_allowed"])
        self.assertEqual(result["operation_count"], 10)
        self.assertEqual(result["pull_request_number"], 1234)

        methods = [request.get_method() for request in opener.requests]
        self.assertEqual(
            methods,
            [
                "POST",
                "GET",
                "GET",
                "GET",
                "GET",
                "POST",
                "POST",
                "POST",
                "GET",
                "POST",
                "POST",
            ],
        )
        self.assertNotIn("PATCH", methods)
        self.assertNotIn("DELETE", methods)
        self.assertEqual(
            sum(request.full_url.endswith("/git/ref/heads/main") for request in opener.requests),
            2,
        )
        installation_requests = [
            item for item in opener.requests if "/repos/" in item.full_url
        ]
        self.assertTrue(installation_requests)
        for item in installation_requests:
            self.assertTrue(item.headers["Authorization"].startswith("Bearer ghs_"))
            self.assertNotIn("/merges", item.full_url)
            self.assertNotIn("/actions", item.full_url)
            self.assertNotIn("/deployments", item.full_url)

    def test_stale_base_fails_before_any_repository_write(self):
        opener = _RepairOpener(self._token_payload(), base_sha="f" * 40)
        with self.assertRaises(DraftRepairConflictError):
            execute_bounded_draft_repair(
                self.config, _repair_request(), now_epoch=NOW, opener=opener
            )
        repository_methods = [
            item.get_method() for item in opener.requests if "/repos/" in item.full_url
        ]
        self.assertEqual(repository_methods, ["GET"])

    def test_replay_stops_at_existing_branch_before_object_writes(self):
        opener = _RepairOpener(self._token_payload(), branch_exists=True)
        with self.assertRaises(DraftRepairConflictError):
            execute_bounded_draft_repair(
                self.config, _repair_request(), now_epoch=NOW, opener=opener
            )
        repository_methods = [
            item.get_method() for item in opener.requests if "/repos/" in item.full_url
        ]
        self.assertEqual(repository_methods, ["GET", "GET", "GET"])

    def test_http_response_is_no_store_and_contains_only_safe_evidence(self):
        request = _repair_request()
        safe_result = {
            "status": "created",
            "repository": REPOSITORY_FULL_NAME,
            "task_key": request.task_key,
            "action_fingerprint": request.action_fingerprint,
            "manifest_version": 1,
            "base_sha": BASE_SHA,
            "branch_name": request.branch_name,
            "commit_sha": COMMIT_SHA,
            "pull_request_number": 1234,
            "pull_request_url": (
                "https://github.com/olegmed1-art/bridge-video-free/pull/1234"
            ),
            "draft": True,
            "token_exposed": False,
            "merge_allowed": False,
            "production_mutation": False,
            "operation_count": 10,
        }
        with (
            patch.dict(
                os.environ,
                {
                    "AUTOPILOT_TOKEN_BROKER_SECRET": STRONG_BROKER_SECRET,
                    "VERCEL_ENV": "preview",
                },
                clear=True,
            ),
            patch("broker_app.main.load_config", return_value=self.config),
            patch(
                "broker_app.main.execute_bounded_draft_repair",
                return_value=safe_result,
            ),
        ):
            response = asyncio.run(
                draft_repair(
                    request,
                    authorization=f"Bearer {STRONG_BROKER_SECRET}",
                )
            )
        body = response.body.decode("utf-8")
        self.assertEqual(response.headers["cache-control"], "no-store, max-age=0")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertIn('"token_exposed":false', body)
        self.assertNotIn("ghs_", body)
        self.assertNotIn(self.private_key_pem, body)

    def test_http_boundary_hides_internal_errors(self):
        request = _repair_request()
        for failure, expected_status, expected_detail in (
            (
                BrokerConfigurationError("private detail"),
                503,
                "TOKEN_BROKER_NOT_CONFIGURED",
            ),
            (
                BrokerRetryableError("private detail"),
                502,
                "GITHUB_TOKEN_TRANSIENT_ERROR",
            ),
            (
                DraftRepairConflictError("private detail"),
                409,
                "DRAFT_REPAIR_PRECONDITION_FAILED",
            ),
            (
                BrokerContractError("private detail"),
                502,
                "GITHUB_TOKEN_CONTRACT_ERROR",
            ),
        ):
            with self.subTest(failure=type(failure).__name__), patch.dict(
                os.environ,
                {
                    "AUTOPILOT_TOKEN_BROKER_SECRET": STRONG_BROKER_SECRET,
                    "VERCEL_ENV": "preview",
                },
                clear=True,
            ), patch("broker_app.main.load_config") as config_loader, patch(
                "broker_app.main.execute_bounded_draft_repair"
            ) as executor:
                if isinstance(failure, BrokerConfigurationError):
                    config_loader.side_effect = failure
                else:
                    config_loader.return_value = self.config
                    executor.side_effect = failure
                with self.assertRaises(HTTPException) as context:
                    asyncio.run(
                        draft_repair(
                            request,
                            authorization=f"Bearer {STRONG_BROKER_SECRET}",
                        )
                    )
            self.assertEqual(context.exception.status_code, expected_status)
            self.assertEqual(context.exception.detail, expected_detail)
            self.assertNotIn("private detail", str(context.exception.detail))


if __name__ == "__main__":
    unittest.main()
