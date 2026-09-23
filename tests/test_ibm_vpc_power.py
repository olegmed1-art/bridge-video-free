from __future__ import annotations

import json
import unittest
import urllib.error
from io import BytesIO, StringIO
from pathlib import Path
from unittest import mock

from ops.ibm_vpc_power import (
    BoundedClientError,
    ActionOutcomeError,
    MUTATION_AUTHORIZATION,
    _instance_url,
    _request_json,
    create_action,
    main,
    obtain_token,
    parse_instance,
    read_instance,
)


INSTANCE_ID = "02c7_4463831b-c1a7-45a7-84f4-c0388c8e03b2"
INSTANCE_NAME = "bridge-school-compute-ibm"
ROOT = Path(__file__).resolve().parents[1]


class IbmVpcActionTests(unittest.TestCase):
    def instance(self, status="stopped"):
        return {"id": INSTANCE_ID, "name": INSTANCE_NAME, "status": status}

    def submit(self, action="start", authorization=MUTATION_AUTHORIZATION):
        return create_action(
            "token", region="eu-de", instance_id=INSTANCE_ID,
            name=INSTANCE_NAME, action=action, authorization=authorization,
        )

    def test_action_id_is_not_instance_id_and_state_is_read_separately(self):
        # IBM's InstanceAction schema, independently of the Instance fixture.
        for action, before, after in (("start", "stopped", "starting"), ("stop", "running", "stopping")):
            with self.subTest(action=action), mock.patch(
                "ops.ibm_vpc_power._request_json",
                side_effect=[self.instance(before), {
                    "id": "distinct-action-id", "type": action, "status": "pending",
                    "created_at": "2026-09-23T00:00:00Z",
                }, self.instance(after)],
            ) as request:
                self.assertEqual(after, self.submit(action).status)
                calls = [call.args[0] for call in request.call_args_list]
                self.assertEqual(["GET", "POST", "GET"], [c.method for c in calls])
                self.assertEqual({"type": action}, json.loads(calls[1].data))
                self.assertIn(f"/instances/{INSTANCE_ID}/actions?", calls[1].full_url)

    def test_deprecated_action_fields_may_be_absent(self):
        with mock.patch("ops.ibm_vpc_power._request_json", side_effect=[
            self.instance(), {"type": "start", "created_at": "2026-09-23T00:00:00Z"},
            self.instance(),
        ]):
            # An accepted asynchronous command need not have changed state yet.
            self.assertEqual("stopped", self.submit().status)

    def test_preflight_failure_never_submits(self):
        cases = [BoundedClientError("provider_http_403"), self.instance("running"),
                 {**self.instance(), "id": "02c7_other"},
                 {**self.instance(), "name": "other"}]
        for value in cases:
            with self.subTest(value=value), mock.patch(
                "ops.ibm_vpc_power._request_json", side_effect=[value]
            ) as request, self.assertRaises(BoundedClientError):
                self.submit()
            self.assertEqual(["GET"], [c.args[0].method for c in request.call_args_list])

    def test_missing_authorization_never_makes_request(self):
        with mock.patch("ops.ibm_vpc_power._request_json") as request:
            with self.assertRaisesRegex(BoundedClientError, "mutation_authorization_absent"):
                self.submit(authorization="")
        request.assert_not_called()

    def test_uncertain_submission_is_not_retried_or_reported_as_refused(self):
        for response in (BoundedClientError("provider_request_failed"),
                         {"type": "stop"}, {"type": "start", "status": []}):
            with self.subTest(response=response), mock.patch(
                "ops.ibm_vpc_power._request_json", side_effect=[self.instance(), response]
            ) as request, self.assertRaises(ActionOutcomeError) as caught:
                self.submit()
            self.assertEqual("UNKNOWN", caught.exception.result)
            self.assertEqual(["GET", "POST"], [c.args[0].method for c in request.call_args_list])

    def test_accepted_action_with_failed_followup_is_not_resubmitted(self):
        for followup in (BoundedClientError("provider_http_403"),
                         {**self.instance(), "id": "02c7_wrong"}):
            with self.subTest(followup=followup), mock.patch(
                "ops.ibm_vpc_power._request_json", side_effect=[
                    self.instance(), {"type": "start", "status": "pending"}, followup,
                ]
            ) as request, self.assertRaises(ActionOutcomeError) as caught:
                self.submit()
            self.assertEqual("ACCEPTED_UNVERIFIED", caught.exception.result)
            self.assertEqual(["GET", "POST", "GET"], [c.args[0].method for c in request.call_args_list])

    def test_reported_action_failure_is_not_success(self):
        with mock.patch("ops.ibm_vpc_power._request_json", side_effect=[
            self.instance(), {"type": "start", "status": "failed"},
        ]), self.assertRaises(ActionOutcomeError) as caught:
            self.submit()
        self.assertEqual("FAILED", caught.exception.result)

    def test_cli_requires_reconciliation_after_accepted_followup_failure(self):
        output = StringIO()
        with mock.patch("ops.ibm_vpc_power.obtain_token", return_value="token"), mock.patch(
            "ops.ibm_vpc_power._request_json", side_effect=[
                self.instance(), {"type": "start"}, BoundedClientError("provider_request_failed"),
            ]
        ), mock.patch("sys.stderr", output):
            result = main(["start", "--instance-id", INSTANCE_ID, "--instance-name", INSTANCE_NAME,
                           "--mutation-authorization", MUTATION_AUTHORIZATION])
        self.assertEqual(4, result)
        self.assertIn("IBM_VPC_POWER_RESULT=ACCEPTED_UNVERIFIED", output.getvalue())
        self.assertIn("IBM_VPC_POWER_RECONCILE_REQUIRED=YES", output.getvalue())
        self.assertNotIn("REFUSED", output.getvalue())


class IbmVpcPowerContractTests(unittest.TestCase):
    def test_cloudflare_denial_is_distinguished_from_iam_without_disclosure(self):
        for code in (1010, "1010"):
            body = json.dumps({"cloudflare_error": True, "error_code": code,
                               "error_name": "browser_signature_banned", "detail": "SECRET"}).encode()
            error = urllib.error.HTTPError("https://example.invalid", 403, "denied", {}, BytesIO(body))
            with self.subTest(code=code), mock.patch("urllib.request.urlopen", side_effect=error):
                with self.assertRaisesRegex(BoundedClientError, "^provider_http_403_code_cloudflare_1010$"):
                    _request_json(mock.Mock())

    def test_unrelated_or_oversize_errors_are_not_cloudflare_denials(self):
        for body in (b'{"error_code":1010}', b'{"cloudflare_error":true,"error_code":"SECRET"}', b"x" * 65537):
            error = urllib.error.HTTPError("https://example.invalid", 403, "denied", {}, BytesIO(body))
            with mock.patch("urllib.request.urlopen", side_effect=error):
                with self.assertRaises(BoundedClientError) as caught:
                    _request_json(mock.Mock())
            self.assertNotIn("cloudflare_1010", str(caught.exception))
            self.assertNotIn("SECRET", str(caught.exception))

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
