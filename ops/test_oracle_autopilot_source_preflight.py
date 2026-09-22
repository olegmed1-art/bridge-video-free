"""Contract tests use synthetic credentials only; never connect to a database."""
import unittest
from ops.oracle_autopilot_source_preflight import HOST, POOLER_HOST, connection_parameters


class Contract(unittest.TestCase):
    def test_fixed_readonly_tls_settings(self):
        p = connection_parameters(f"postgresql://neondb_owner:test@{HOST}/neondb?sslmode=require&channel_binding=require&options=unsafe", "neondb_owner")
        self.assertEqual(p["sslmode"], "verify-full")
        self.assertIn("default_transaction_read_only=on", p["options"])
        self.assertNotIn("unsafe", p["options"])

    def test_pinned_pooler_normalized_to_direct_tls(self):
        p = connection_parameters(f"postgresql://neondb_owner:test@{POOLER_HOST}/neondb?sslmode=require&channel_binding=require", "neondb_owner")
        self.assertEqual(p["host"], HOST)
        self.assertEqual(p["sslrootcert"], "/etc/ssl/certs/ca-certificates.crt")

    def test_existing_worker_normalization(self):
        raw = "'postgresql://bridge_school_worker_principal:test@legacy.neon.tech/neondb?sslmode=require&channel_binding=require'"
        p = connection_parameters(raw, "bridge_school_worker_principal")
        self.assertEqual(p["host"], HOST)
        with self.assertRaises(ValueError):
            connection_parameters(raw.replace("legacy.neon.tech", "example.com"), "bridge_school_worker_principal")

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
