"""DDS exception messages must not cross the API response boundary."""
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

import bridge_school_api.main as api


class DDSErrorBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.payload = api.DDS3TableRequest(pbn="N:synthetic")
        self.request = Request({"type": "http", "headers": []})

    def test_value_errors_are_sanitized_on_every_execution_path(self):
        marker = "synthetic-private-config=/private/key;token=do-not-return"
        for target, remote in [("solve_table", None), ("compute_remote", object()),
                               ("_remote_dds3_config", None)]:
            with self.subTest(target=target):
                with patch.object(api, "_remote_dds3_config", return_value=remote):
                    with patch.object(api, target, side_effect=ValueError(marker)):
                        with self.assertRaises(HTTPException) as caught:
                            api.dds3_table(self.payload, self.request)
                self.assertEqual(caught.exception.status_code, 422)
                self.assertEqual(caught.exception.detail, "DDS_REQUEST_INVALID")
                self.assertNotIn(marker, str(caught.exception.detail))

    def test_unavailable_remains_generic_503(self):
        with patch.object(api, "_remote_dds3_config", return_value=None):
            with patch.object(api, "solve_table", side_effect=api.DDSUnavailable("private-path")):
                with self.assertRaises(HTTPException) as caught:
                    api.dds3_table(self.payload, self.request)
        self.assertEqual((caught.exception.status_code, caught.exception.detail),
                         (503, "DDS_UNAVAILABLE"))

    def test_success_result_is_preserved(self):
        result = {"engine": "DDS3", "fallback_used": False}
        with patch.object(api, "_remote_dds3_config", return_value=None):
            with patch.object(api, "solve_table", return_value=result):
                self.assertIs(api.dds3_table(self.payload, self.request), result)


if __name__ == "__main__":
    unittest.main()
