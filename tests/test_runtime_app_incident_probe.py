import json
import os
import unittest
from unittest.mock import MagicMock, patch

from bridge_school_api import incident_db_probe as probe


SECRET = "synthetic-password-must-not-appear"
DSN = (f"postgresql://{probe.EXPECTED_PRINCIPAL}:{SECRET}@{probe.PRODUCTION_HOST}/"
       f"{probe.EXPECTED_DATABASE}?sslmode=require&channel_binding=require")


class IncidentProbeTests(unittest.TestCase):
    def run_probe(self, dsn=DSN, connect=False, extra=None):
        env = {"VERCEL_ENV": "production", "BRIDGE_APP_DATABASE_URL": dsn, **(extra or {})}
        with patch.dict(os.environ, env, clear=True):
            result = probe.probe(connect)
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotIn("postgresql://", json.dumps(result))
        return result

    def test_default_does_not_connect(self):
        with patch.object(probe.psycopg, "connect") as connect:
            self.assertEqual(self.run_probe()["status"], "configuration_pass_connection_not_tested")
            connect.assert_not_called()

    def test_source_endpoint_observations(self):
        cases = (
            (probe.PRODUCTION_HOST, True, False, "configuration_pass_connection_not_tested"),
            (probe.PRODUCTION_DIRECT_HOST, True, True, "configuration_pass_connection_not_tested"),
            ("unrelated-synthetic-endpoint.neon.tech", False, True, "source_endpoint_requires_review"),
        )
        for host, expected, rewritten, status in cases:
            with self.subTest(host=host), patch.object(probe.psycopg, "connect") as connect:
                result = self.run_probe(DSN.replace(probe.PRODUCTION_HOST, host))
                self.assertEqual(result["status"], status)
                self.assertEqual(result["source"], {
                    "raw_host_is_expected": expected,
                    "raw_host_is_pooler": host == probe.PRODUCTION_HOST,
                    "raw_host_is_direct": host == probe.PRODUCTION_DIRECT_HOST,
                    "endpoint_was_rewritten": rewritten,
                })
                self.assertNotIn(host, json.dumps(result))
                connect.assert_not_called()

    def test_unrelated_source_cannot_connect_after_normalization(self):
        with patch.object(probe.psycopg, "connect") as connect:
            result = self.run_probe(DSN.replace(probe.PRODUCTION_HOST, "other-synthetic.neon.tech"), True)
            self.assertEqual(result["status"], "source_endpoint_requires_review")
            self.assertTrue(result["checks"]["endpoint_matches"])
            connect.assert_not_called()

    def test_quoted_uri_retains_source_observations(self):
        result = self.run_probe("  '" + DSN + "'  ")
        self.assertEqual(result["status"], "configuration_pass_connection_not_tested")
        self.assertFalse(result["source"]["endpoint_was_rewritten"])

    def test_actual_health_failure_logs_config_without_extra_connection(self):
        from fastapi import HTTPException
        from bridge_school_api import main as api

        env = {"VERCEL_ENV": "production", "BRIDGE_APP_DATABASE_URL": DSN}
        with patch.dict(os.environ, env, clear=True), \
             patch.object(api, "connect", side_effect=RuntimeError("password authentication failed " + DSN)), \
             patch.object(probe.psycopg, "connect") as connect, \
             patch.object(api.logger, "error") as log:
            with self.assertRaises(HTTPException) as raised:
                api.healthz()
            self.assertEqual(raised.exception.status_code, 503)
            self.assertEqual(raised.exception.detail, "service unavailable")
            connect.assert_not_called()
            rendered = log.call_args.args[0] % log.call_args.args[1:]
            self.assertIn("configuration_pass_connection_not_tested", rendered)
            self.assertNotIn(SECRET, rendered)
            self.assertNotIn("postgresql://", rendered)
            self.assertNotIn(probe.PRODUCTION_HOST, rendered)

    def test_query_overrides_cannot_redirect_probe(self):
        for parameter in ("host=elsewhere.neon.tech", "hostaddr=127.0.0.1", "user=neondb_owner", "service=other"):
            with self.subTest(parameter=parameter), patch.object(probe.psycopg, "connect") as connect:
                self.assertEqual(self.run_probe(DSN + "&" + parameter, True)["status"], "effective_parameters_rejected")
                connect.assert_not_called()

    def test_libpq_environment_requires_review(self):
        with patch.object(probe.psycopg, "connect") as connect:
            self.assertEqual(self.run_probe(connect=True, extra={"PGHOSTADDR": "127.0.0.1"})["status"], "libpq_environment_requires_review")
            connect.assert_not_called()

    def test_connection_failure_does_not_leak(self):
        with patch.object(probe.psycopg, "connect", side_effect=RuntimeError("password authentication failed " + DSN)):
            self.assertEqual(self.run_probe(connect=True)["status"], "authentication_failed")

    def test_success_is_readonly_and_rolled_back(self):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (probe.EXPECTED_PRINCIPAL, probe.EXPECTED_DATABASE, "on")
        with patch.object(probe.psycopg, "connect", return_value=conn) as connect:
            self.assertEqual(self.run_probe(connect=True)["status"], "pass")
            self.assertIn("default_transaction_read_only=on", connect.call_args.kwargs["options"])
            conn.rollback.assert_called_once()

    def test_wrong_session_fails(self):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.cursor.return_value.__enter__.return_value.fetchone.return_value = ("neondb_owner", probe.EXPECTED_DATABASE, "off")
        with patch.object(probe.psycopg, "connect", return_value=conn):
            self.assertEqual(self.run_probe(connect=True)["status"], "session_contract_failed")


if __name__ == "__main__":
    unittest.main()
