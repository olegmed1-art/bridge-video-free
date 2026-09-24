import json
import os
import subprocess
import sys
import unittest

from ops.incident_1912_secret_source_audit import ENDPOINTS, SUFFIX, classify


class SecretSourceAuditTests(unittest.TestCase):
    KEY = "saved_uri_authority_endpoint_current_mapping"

    def test_known_sources_and_override(self):
        for branch, prefix in ENDPOINTS.items():
            for pooler in (False, True):
                host = prefix + ("-pooler" if pooler else "") + SUFFIX
                uri = f"postgresql://fake:synthetic-private-value@{host}/neondb?sslmode=require"
                expected = branch + ("_pooler" if pooler else "_direct")
                with self.subTest(expected=expected):
                    self.assertEqual(classify(uri), {self.KEY: expected, "authority_override": False})
                    self.assertEqual(
                        classify(uri + "&host=other.example"),
                        {self.KEY: "ambiguous_override", "authority_override": True},
                    )
                    self.assertEqual(
                        classify(uri + "&port=5544"),
                        {self.KEY: "ambiguous_override", "authority_override": True},
                    )

    def test_moved_endpoint_does_not_claim_password_origin(self):
        # ep-noisy-pine previously served the old branch, but CURRENTLY maps
        # to the new default. A saved URI does not identify password validity.
        uri = "postgresql://fake:old-password@" + ENDPOINTS["current_default"] + SUFFIX + "/neondb"
        self.assertEqual(classify(uri)[self.KEY], "current_default_direct")
        self.assertNotIn("password", json.dumps(classify(uri)))

    def test_unknown_and_malformed_never_include_input(self):
        for uri, expected in (
            ("", "missing"),
            ("postgresql://fake:synthetic-private-value@other.neon.tech/neondb", "other_neon"),
            ("postgresql://fake:synthetic-private-value@other.example/neondb", "other_non_neon"),
            ("postgresql://fake:synthetic-private-value@/neondb", "malformed"),
            ("postgresql://fake:synthetic-private-value@other.neon.tech:5544/neondb", "malformed"),
        ):
            with self.subTest(expected=expected):
                output = json.dumps(classify(uri))
                self.assertEqual(json.loads(output)[self.KEY], expected)
                self.assertNotIn("synthetic-private-value", output)
                self.assertNotIn("other.example", output)

    def test_command_emits_only_fixed_categories(self):
        env = dict(os.environ)
        env["BRIDGE_APP_DATABASE_URL"] = (
            "postgresql://fake:synthetic-private-value@" + ENDPOINTS["current_legacy"] + SUFFIX + "/neondb"
        )
        result = subprocess.run(
            [sys.executable, "-m", "ops.incident_1912_secret_source_audit"],
            env=env, capture_output=True, text=True, check=True,
        )
        output = json.loads(result.stdout)
        self.assertEqual(output["BRIDGE_APP_DATABASE_URL"][self.KEY], "current_legacy_direct")
        self.assertNotIn("synthetic-private-value", result.stdout + result.stderr)
        self.assertNotIn(ENDPOINTS["current_legacy"], result.stdout + result.stderr)
        allowed = {"missing", "malformed", "ambiguous_override", "other_neon", "other_non_neon"}
        allowed.update(branch + form for branch in ENDPOINTS for form in ("_direct", "_pooler"))
        self.assertTrue(all(set(record) == {self.KEY, "authority_override"} for record in output.values()))
        self.assertTrue(all(record[self.KEY] in allowed for record in output.values()))


if __name__ == "__main__":
    unittest.main()
