from __future__ import annotations

import asyncio
import base64
import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import HTTPException
from pydantic import ValidationError

from broker_app.github import (
    BrokerConfigurationError,
    BrokerContractError,
    BrokerRetryableError,
    BrokerConfig,
    REPOSITORY_FULL_NAME,
    TOKEN_PERMISSIONS,
    build_app_jwt,
    issue_installation_token,
    load_config,
)
from broker_app.main import (
    InstallationTokenRequest,
    _require_broker_authorization,
    healthz,
    installation_token,
)


NOW = 1_788_153_600
STRONG_BROKER_SECRET = "s" * 43


def _decode_segment(value: str) -> dict[str, object]:
    padding_bytes = "=" * (-len(value) % 4)
    return json.loads(base64.urlsafe_b64decode(value + padding_bytes))


class _FakeResponse:
    def __init__(self, payload: dict[str, object], *, url: str, status: int = 201):
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


class _FakeOpener:
    def __init__(self, payload: dict[str, object]):
        self.payload = payload
        self.request = None
        self.timeout = None

    def open(self, request, *, timeout):
        self.request = request
        self.timeout = timeout
        return _FakeResponse(self.payload, url=request.full_url)


class BrokerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.private_key = rsa.generate_private_key(public_exponent=65_537, key_size=2_048)
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

    def test_installation_token_request_is_repository_and_permission_scoped(self):
        opener = _FakeOpener(self._token_payload())
        result = issue_installation_token(
            self.config,
            now_epoch=NOW,
            opener=opener,
        )
        self.assertEqual(result["repository"], REPOSITORY_FULL_NAME)
        self.assertEqual(result["permissions"], TOKEN_PERMISSIONS)
        self.assertEqual(result["token_type"], "github_app_installation")
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
            with self.subTest(payload=payload):
                with self.assertRaises(BrokerContractError):
                    issue_installation_token(
                        self.config,
                        now_epoch=NOW,
                        opener=_FakeOpener(payload),
                    )

    def test_expired_or_unbounded_token_fails_closed(self):
        for lifetime in (-1, 4_000):
            payload = self._token_payload(
                expires_at=datetime.fromtimestamp(
                    NOW + lifetime, tz=timezone.utc
                ).isoformat().replace("+00:00", "Z")
            )
            with self.subTest(lifetime=lifetime):
                with self.assertRaisesRegex(BrokerContractError, "EXPIRY_INVALID"):
                    issue_installation_token(
                        self.config,
                        now_epoch=NOW,
                        opener=_FakeOpener(payload),
                    )

    def test_transient_github_error_is_separate_from_contract_error(self):
        class FailingOpener:
            def open(self, request, *, timeout):
                raise TimeoutError

        with self.assertRaises(BrokerRetryableError):
            issue_installation_token(
                self.config,
                now_epoch=NOW,
                opener=FailingOpener(),
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

    def test_request_contract_binds_task_fingerprint_and_repository(self):
        valid = InstallationTokenRequest(
            task_key="phase3b-canary-20260831",
            action_fingerprint="a" * 64,
            repository=REPOSITORY_FULL_NAME,
            manifest_version=1,
        )
        self.assertEqual(valid.repository, REPOSITORY_FULL_NAME)
        with self.assertRaises(ValidationError):
            InstallationTokenRequest(
                task_key="bad task",
                action_fingerprint="a" * 64,
                repository=REPOSITORY_FULL_NAME,
                manifest_version=1,
            )
        with self.assertRaises(ValidationError):
            InstallationTokenRequest(
                task_key="safe-task",
                action_fingerprint="a" * 64,
                repository="other/repo",
                manifest_version=1,
            )

    def test_health_is_preview_only_and_non_mutating(self):
        with patch.dict(os.environ, {}, clear=True):
            payload = asyncio.run(healthz())
        self.assertTrue(payload["preview_only"])
        self.assertFalse(payload["production_mutations_enabled"])
        self.assertFalse(payload["github_token_broker_enabled"])

    def test_http_response_is_no_store_and_never_contains_private_key(self):
        request = InstallationTokenRequest(
            task_key="phase3b-canary-20260831",
            action_fingerprint="a" * 64,
            repository=REPOSITORY_FULL_NAME,
            manifest_version=1,
        )
        token_payload = {
            "token": "ghs_" + "x" * 36,
            "expires_at": "2026-08-31T13:00:00Z",
            "repository": REPOSITORY_FULL_NAME,
            "permissions": TOKEN_PERMISSIONS,
            "token_type": "github_app_installation",
        }
        with (
            patch.dict(
                os.environ,
                {"AUTOPILOT_TOKEN_BROKER_SECRET": STRONG_BROKER_SECRET},
                clear=True,
            ),
            patch("broker_app.main.load_config", return_value=self.config),
            patch(
                "broker_app.main.issue_installation_token",
                return_value=token_payload,
            ),
        ):
            response = asyncio.run(
                installation_token(
                    request,
                    authorization=f"Bearer {STRONG_BROKER_SECRET}",
                )
            )
        body = response.body.decode("utf-8")
        self.assertEqual(response.headers["cache-control"], "no-store, max-age=0")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertIn('"action_fingerprint":"' + "a" * 64 + '"', body)
        self.assertNotIn(self.private_key_pem, body)

    def test_http_boundary_hides_internal_configuration_and_github_errors(self):
        request = InstallationTokenRequest(
            task_key="phase3b-canary-20260831",
            action_fingerprint="a" * 64,
            repository=REPOSITORY_FULL_NAME,
            manifest_version=1,
        )
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
                BrokerContractError("private detail"),
                502,
                "GITHUB_TOKEN_CONTRACT_ERROR",
            ),
        ):
            with self.subTest(failure=type(failure).__name__), (
                patch.dict(
                    os.environ,
                    {"AUTOPILOT_TOKEN_BROKER_SECRET": STRONG_BROKER_SECRET},
                    clear=True,
                )
            ), patch("broker_app.main.load_config") as config_loader, patch(
                "broker_app.main.issue_installation_token"
            ) as issuer:
                if isinstance(failure, BrokerConfigurationError):
                    config_loader.side_effect = failure
                else:
                    config_loader.return_value = self.config
                    issuer.side_effect = failure
                with self.assertRaises(HTTPException) as context:
                    asyncio.run(
                        installation_token(
                            request,
                            authorization=f"Bearer {STRONG_BROKER_SECRET}",
                        )
                    )
            self.assertEqual(context.exception.status_code, expected_status)
            self.assertEqual(context.exception.detail, expected_detail)
            self.assertNotIn("private detail", str(context.exception.detail))


if __name__ == "__main__":
    unittest.main()
