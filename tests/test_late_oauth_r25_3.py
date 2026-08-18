#!/usr/bin/env python3
import unittest

import bridge_worker_3_1_free as core
from bridge_runtime_hardening_r25_3 import REVISION, fresh_persistence_call


class LateOAuthR253Test(unittest.TestCase):
    def test_final_persistence_uses_fresh_token_not_stale_token(self):
        seen = []
        token_calls = []

        def token_func():
            token_calls.append(True)
            return "fresh-token"

        def persist(token):
            seen.append(token)
            return {"ok": True}

        result = fresh_persistence_call(persist, token_func, "expired-token")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(seen, ["fresh-token"])
        self.assertEqual(len(token_calls), 1)

    def test_missing_late_token_fails_closed(self):
        called = []

        def persist(token):
            called.append(token)

        with self.assertRaisesRegex(RuntimeError, "late Drive OAuth refresh unavailable"):
            fresh_persistence_call(persist, lambda: None, "expired-token")
        self.assertEqual(called, [])

    def test_public_name_unchanged(self):
        self.assertEqual(core.ALGORITHM_VERSION, "3.1 FREE")
        self.assertEqual(REVISION, "3.1-free-r25.3")


if __name__ == "__main__":
    unittest.main()
