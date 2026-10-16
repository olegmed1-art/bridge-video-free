"""The positive SQL rehearsal must reject unmarked or changed databases."""

import runpy
import unittest
from pathlib import Path


RUNNER = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "database/tests/402_video_canon_positive_synthetic_rehearsal.py"),
    run_name="canon_guard_test",
)
validate = RUNNER["_validate_disposable_target"]
PREFIX = RUNNER["DISPOSABLE_COMMENT_PREFIX"]
IDENTITY = "0123456789abcdef0123456789abcdef"
ARGS = {"host": "127.0.0.1", "dbname": "canon_positive_1015"}


class DisposableTargetTests(unittest.TestCase):
    def test_matching_marker_and_unchanged_template_clone(self):
        validate(ARGS, IDENTITY, "127.0.0.1", ARGS["dbname"], PREFIX + IDENTITY,
                 [], 0)

    def test_rejects_new_database_with_unlisted_data(self):
        with self.assertRaisesRegex(ValueError, "public.unlisted_data"):
            validate(ARGS, IDENTITY, "127.0.0.1", ARGS["dbname"], PREFIX + IDENTITY,
                     ["public.unlisted_data"], 0)

    def test_rejects_missing_or_different_marker(self):
        for marker in (None, PREFIX + "f" * 32):
            with self.subTest(marker=marker), self.assertRaisesRegex(ValueError, "identity marker"):
                validate(ARGS, IDENTITY, "127.0.0.1", ARGS["dbname"], marker, [], 0)

    def test_rejects_remote_or_overridden_connection(self):
        for args, server in ((ARGS, "192.0.2.1"),
                             ({**ARGS, "hostaddr": "192.0.2.1"}, "127.0.0.1")):
            with self.subTest(args=args, server=server), self.assertRaisesRegex(ValueError, "loopback"):
                validate(args, IDENTITY, server, ARGS["dbname"], PREFIX + IDENTITY,
                         [], 0)


if __name__ == "__main__":
    unittest.main()
