from __future__ import annotations

import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest import mock

from ops.ibm_vpc_power import (
    BoundedClientError,
    MUTATION_AUTHORIZATION,
    _instance_url,
    _request_json,
    obtain_token,
    parse_instance,
    read_instance,
)


INSTANCE_ID = "02c7_4463831b-c1a7-45a7-84f4-c0388c8e03b2"
INSTANCE_NAME = "bridge-school-compute-ibm"
ROOT = Path(__file__).resolve().parents[1]


class IbmVpcPowerContractTests(unittest.TestCase):
    def test_exact_target_and_known_state_are_accepted(self) -> None:
        instance = parse_instance(
            {"id": INSTANCE_ID, "name": INSTANCE_NAME, "status": "running"},
            expected_id=INSTANCE_ID,
            expected_name=INSTANCE_NAME,
        )
        self.assertEqual("running", instance.status)

    def test_different_id_is_refused(self) -> None:
        with self.assertRaisesRegex(BoundedClientError, "instance_id_mismatch"):
            parse_instance(
                {"id": "02c7_other", "name": INSTANCE_NAME, "status": "running"},
                expected_id=INSTANCE_ID,
                expected_name=INSTANCE_NAME,
            )

    def test_different_name_is_refused(self) -> None:
        with self.assertRaisesRegex(BoundedClientError, "instance_name_mismatch"):
            parse_instance(
                {"id": INSTANCE_ID, "name": "other", "status": "running"},
                expected_id=INSTANCE_ID,
                expected_name=INSTANCE_NAME,
            )

    def test_unknown_state_is_refused(self) -> None:
        with self.assertRaisesRegex(BoundedClientError, "instance_status_unknown"):
            parse_instance(
                {"id": INSTANCE_ID, "name": INSTANCE_NAME, "status": "surprising"},
                expected_id=INSTANCE_ID,
                expected_name=INSTANCE_NAME,
            )

    def test_region_and_generation_are_pinned(self) -> None:
        url = _instance_url("eu-de", INSTANCE_ID)
        self.assertTrue(url.startswith("https://eu-de.iaas.cloud.ibm.com/"))
        self.assertIn("generation=2", url)
        with self.assertRaisesRegex(BoundedClientError, "region_not_pinned"):
            _instance_url("us-south", INSTANCE_ID)

    def test_mutation_authorization_is_exact_constant(self) -> None:
        self.assertEqual("IBM_POWER_MUTATION_AUTHORIZED=YES", MUTATION_AUTHORIZATION)

    def test_probe_workflow_is_read_only(self) -> None:
        workflow = (ROOT / ".github/workflows/ibm-vpc-power-probe.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("ibm_vpc_power.py status", workflow)
        self.assertNotIn("ibm_vpc_power.py start", workflow)
        self.assertNotIn("ibm_vpc_power.py stop", workflow)
        self.assertNotIn("--mutation-authorization", workflow)

    def test_http_error_reports_only_safe_status(self) -> None:
        request = mock.Mock()
        error = urllib.error.HTTPError("https://example.invalid", 403, "denied", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(
                BoundedClientError, "^provider_http_403_shape_empty_category_unknown$"
            ):
                _request_json(request)

    def test_http_error_reports_bounded_machine_code_only(self) -> None:
        request = mock.Mock()
        body = BytesIO(
            b'{"errors":[{"code":"missing_permission","message":"do not echo"}],'
            b'"trace":"do not echo"}'
        )
        error = urllib.error.HTTPError(
            "https://example.invalid", 403, "denied", {}, body
        )
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(
                BoundedClientError,
                "^provider_http_403_code_missing_permission$",
            ) as caught:
                _request_json(request)
        rendered = str(caught.exception)
        self.assertNotIn("do not echo", rendered)
        self.assertNotIn("trace", rendered)

    def test_http_error_ignores_unsafe_machine_code(self) -> None:
        request = mock.Mock()
        body = BytesIO(b'{"code":"bad code with spaces","message":"private"}')
        error = urllib.error.HTTPError(
            "https://example.invalid", 403, "denied", {}, body
        )
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(
                BoundedClientError, "^provider_http_403_shape_json_object_category_unknown$"
            ):
                _request_json(request)

    def test_http_error_classifies_html_without_echoing_body(self) -> None:
        request = mock.Mock()
        body = BytesIO(b"<!doctype html><title>private gateway response</title>")
        error = urllib.error.HTTPError(
            "https://example.invalid", 403, "denied", {}, body
        )
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(
                BoundedClientError, "^provider_http_403_shape_html_category_unknown$"
            ) as caught:
                _request_json(request)
        self.assertNotIn("private", str(caught.exception))

    def test_http_error_classifies_known_denial_without_echoing_message(self) -> None:
        request = mock.Mock()
        body = BytesIO(b'{"message":"request blocked by context-based restrictions"}')
        error = urllib.error.HTTPError(
            "https://example.invalid", 403, "denied", {}, body
        )
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(
                BoundedClientError,
                "^provider_http_403_shape_json_object_category_context_restriction$",
            ) as caught:
                _request_json(request)
        self.assertNotIn("request blocked", str(caught.exception))

    def test_iam_failure_is_stage_specific(self) -> None:
        with mock.patch(
            "ops.ibm_vpc_power._request_json",
            side_effect=BoundedClientError("provider_http_400"),
        ):
            with self.assertRaisesRegex(BoundedClientError, "^iam_provider_http_400$"):
                obtain_token("valid-looking-key")

    def test_vpc_failure_is_stage_specific(self) -> None:
        with mock.patch(
            "ops.ibm_vpc_power._request_json",
            side_effect=BoundedClientError("provider_http_403"),
        ):
            with self.assertRaisesRegex(BoundedClientError, "^vpc_provider_http_403$"):
                read_instance(
                    "token",
                    region="eu-de",
                    instance_id=INSTANCE_ID,
                    name=INSTANCE_NAME,
                )


if __name__ == "__main__":
    unittest.main()
