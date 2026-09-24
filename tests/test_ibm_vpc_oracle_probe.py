from __future__ import annotations

import base64
import contextlib
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest import mock

from ops import ibm_vpc_oracle_probe as probe
from ops.ibm_vpc_power import BoundedClientError


def token(**overrides):
    claims = {"iam_id": "iam-" + probe.SERVICE_ID, "account": {"bss": probe.ACCOUNT_ID},
              "exp": time.time() + 3600, **overrides}
    return "header." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".signature"


class OracleProbeTests(unittest.TestCase):
    def test_wrong_identity_account_expiry_or_shape_fails_before_ssh(self):
        for value in (token(iam_id="someone-else"), token(account={"bss": "wrong"}),
                      token(exp=0), token(exp=time.time() + 99999), token(account=[]), "malformed"):
            with self.subTest(value=value), mock.patch.object(probe.subprocess, "run") as run:
                with self.assertRaises(BoundedClientError):
                    probe.probe_over_ssh(value, key="key", known_hosts="hosts")
                run.assert_not_called()

    def test_expected_identity_can_use_id_claim(self):
        probe.verify_identity(token(iam_id=None, id=probe.SERVICE_ID))

    def test_token_is_only_in_stdin_and_host_check_is_mandatory(self):
        value = token()
        output = StringIO()
        with mock.patch.object(probe.subprocess, "run", return_value=subprocess.CompletedProcess(
            [], 0, "ORACLE_IBM_READ_RESULT=PASS\nORACLE_IBM_INSTANCE_STATUS=stopped\n", ""
        )) as run, contextlib.redirect_stdout(output):
            self.assertEqual(0, probe.probe_over_ssh(value, key="key", known_hosts="hosts"))
        args, kwargs = run.call_args
        self.assertNotIn(value, " ".join(args[0]))
        self.assertNotIn(value, output.getvalue())
        self.assertIn("StrictHostKeyChecking=yes", args[0])
        self.assertIn("UserKnownHostsFile=hosts", args[0])
        self.assertIn(probe.ORACLE_HOST, args[0])
        payload = json.loads(kwargs["input"])
        self.assertEqual(value, payload["token"])
        self.assertEqual(probe.INSTANCE_ID, payload["instance_id"])
        self.assertNotIn("api_key", payload)
        run.assert_called_once()

    def test_denial_is_failure_and_not_retried(self):
        with mock.patch.object(probe.subprocess, "run", return_value=subprocess.CompletedProcess(
            [], 3, "ORACLE_IBM_READ_RESULT=FAIL reason=vpc_provider_http_403_code_cloudflare_1010\n", ""
        )) as run, contextlib.redirect_stdout(StringIO()):
            self.assertEqual(3, probe.probe_over_ssh(token(), key="key", known_hosts="hosts"))
        run.assert_called_once()

    def test_transport_errors_do_not_echo_stdin_or_stderr(self):
        for value in (subprocess.CompletedProcess([], 255, "", "SECRET"),
                      subprocess.TimeoutExpired("ssh", 60, output="SECRET", stderr="SECRET")):
            output = StringIO()
            kwargs = {"side_effect": value} if isinstance(value, Exception) else {"return_value": value}
            with mock.patch.object(probe.subprocess, "run", **kwargs) as run, contextlib.redirect_stdout(output):
                with self.assertRaises(BoundedClientError) as caught:
                    probe.probe_over_ssh(token(), key="key", known_hosts="hosts")
            self.assertNotIn("SECRET", output.getvalue() + str(caught.exception))
            run.assert_called_once()

    def test_remote_program_performs_exactly_one_authenticated_get(self):
        source = Path(probe.__file__).with_name("ibm_vpc_power.py").read_text()
        # Exercise the actual serialized remote program and client, stubbing only
        # the network transport. A POST, wrong target or missing bearer fails.
        source += '''
from io import BytesIO
def fake_urlopen(request, timeout):
    assert request.method == "GET"
    assert request.get_header("Authorization") == "Bearer test-token"
    assert request.full_url.startswith("https://eu-de.iaas.cloud.ibm.com/v1/instances/02c7_4463831b-c1a7-45a7-84f4-c0388c8e03b2?")
    assert not getattr(fake_urlopen, "called", False)
    fake_urlopen.called = True
    return BytesIO(b'{"id":"02c7_4463831b-c1a7-45a7-84f4-c0388c8e03b2","name":"bridge-school-compute-ibm","status":"stopped"}')
urllib.request.urlopen = fake_urlopen
'''
        result = subprocess.run([sys.executable, "-c", probe.REMOTE_READ], input=json.dumps({
            "client_source": source, "token": "test-token", "instance_id": probe.INSTANCE_ID,
            "instance_name": probe.INSTANCE_NAME,
        }), text=True, capture_output=True, timeout=5)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("ORACLE_IBM_AUTHENTICATED_REQUEST=YES", result.stdout)
        self.assertIn("ORACLE_IBM_READ_RESULT=PASS", result.stdout)
        self.assertIn("ORACLE_IBM_INSTANCE_STATUS=stopped", result.stdout)
        self.assertNotIn("test-token", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
