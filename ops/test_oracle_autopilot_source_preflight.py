"""Contract tests use synthetic credentials only; never connect to a database."""
import unittest
from ops.oracle_autopilot_source_preflight import HOST, connection_parameters


class Contract(unittest.TestCase):
    def test_fixed_readonly_tls_settings(self):
        p = connection_parameters(f"postgresql://neondb_owner:test@{HOST}/neondb?sslmode=require&channel_binding=require&options=unsafe", "neondb_owner")
        self.assertEqual(p["sslmode"], "verify-full")
        self.assertIn("default_transaction_read_only=on", p["options"])
        self.assertNotIn("unsafe", p["options"])

    def test_wrong_source_rejected(self):
        good = f"postgresql://neondb_owner:test@{HOST}/neondb?sslmode=require&channel_binding=require"
        for bad in (good.replace(HOST, "example.com"), good.replace("/neondb", "/other"),
                    good.replace("neondb_owner:", "other:"), good.replace("sslmode=require", "sslmode=disable"),
                    good.replace("test@", "%0A@"), good + "#fragment", ""):
            with self.subTest():
                with self.assertRaises(ValueError):
                    connection_parameters(bad, "neondb_owner")


if __name__ == "__main__":
    unittest.main()
