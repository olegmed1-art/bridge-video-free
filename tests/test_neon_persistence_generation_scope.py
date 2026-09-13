#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

import bridge_neon_persistence as persistence


JOB = "41daa4ca6e09d13e366c578b7c53ae31"
FOLDER = "1GenerationFolder_Id-123"
REVISION = "3.1-free-r25.15"


class NeonPersistenceGenerationScopeTests(unittest.TestCase):
    def setUp(self):
        self.old_folder = os.environ.get("BRIDGE_OUTPUT_FOLDER_ID")
        self.old_revision = os.environ.get("BRIDGE_REQUESTED_ALGORITHM_REVISION")
        os.environ["BRIDGE_OUTPUT_FOLDER_ID"] = FOLDER
        os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = REVISION

    def tearDown(self):
        if self.old_folder is None:
            os.environ.pop("BRIDGE_OUTPUT_FOLDER_ID", None)
        else:
            os.environ["BRIDGE_OUTPUT_FOLDER_ID"] = self.old_folder
        if self.old_revision is None:
            os.environ.pop("BRIDGE_REQUESTED_ALGORITHM_REVISION", None)
        else:
            os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = self.old_revision

    def _download(self, payloads):
        def fake(_token, file_id, path):
            path.write_text(json.dumps(payloads[file_id]), encoding="utf-8")
        return fake

    def test_query_is_scoped_to_output_folder(self):
        good = {"job_id": JOB, "status": "AI_DONE", "algorithmRevision": REVISION}
        with patch.object(persistence.io, "search", return_value=[{"id": "good", "modifiedTime": "2026-08-19T00:00:00Z"}]) as search, patch.object(
            persistence.io, "download", side_effect=self._download({"good": good})
        ):
            self.assertEqual(persistence._load_done("token", JOB), good)
        query = search.call_args.args[1]
        self.assertIn(f"'{FOLDER}' in parents", query)
        self.assertIn(f"name='AI_DONE_{JOB}.json'", query)

    def test_wrong_revision_is_rejected_even_in_scoped_folder(self):
        wrong = {"job_id": JOB, "status": "AI_DONE", "algorithmRevision": "3.1-free-r25.14"}
        with patch.object(persistence.io, "search", return_value=[{"id": "wrong", "modifiedTime": "2026-08-19T00:00:00Z"}]), patch.object(
            persistence.io, "download", side_effect=self._download({"wrong": wrong})
        ):
            with self.assertRaisesRegex(RuntimeError, "DATABASE_PERSIST_DONE_NOT_FOUND_FOR_GENERATION"):
                persistence._load_done("token", JOB)

    def test_invalid_folder_id_fails_closed(self):
        os.environ["BRIDGE_OUTPUT_FOLDER_ID"] = "bad'folder"
        with self.assertRaisesRegex(RuntimeError, "DATABASE_PERSIST_INVALID_OUTPUT_FOLDER_ID"):
            persistence._load_done("token", JOB)

    def test_legacy_global_selection_remains_when_folder_absent(self):
        os.environ.pop("BRIDGE_OUTPUT_FOLDER_ID", None)
        good = {"job_id": JOB, "status": "AI_DONE", "algorithmRevision": REVISION}
        with patch.object(persistence.io, "search", return_value=[{"id": "good", "modifiedTime": "2026-08-19T00:00:00Z"}]) as search, patch.object(
            persistence.io, "download", side_effect=self._download({"good": good})
        ):
            persistence._load_done("token", JOB)
        self.assertNotIn(" in parents", search.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
