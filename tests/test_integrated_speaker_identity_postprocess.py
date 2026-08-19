from __future__ import annotations

import json
import os
from pathlib import Path
import unittest
from unittest import mock

import bridge_speaker_identity_postprocess as stage

JOB = "41daa4ca6e09d13e366c578b7c53ae31"
OUTPUT = "outputFolder123"
WORK = "workFolder123"
MASTER = "masterPdf123"


def done_payload():
    return {
        "status": "AI_DONE",
        "job_id": JOB,
        "algorithmRevision": stage.R25_REVISION,
        "masterPdf": {"driveId": MASTER},
    }


class IntegratedR29StageTests(unittest.TestCase):
    def setUp(self):
        self.old = dict(os.environ)
        os.environ.update({
            "BRIDGE_JOB_ID": JOB,
            "BRIDGE_OUTPUT_FOLDER_ID": OUTPUT,
            "BRIDGE_WORK_FOLDER_ID": WORK,
        })
        os.environ.pop("BRIDGE_R29_IDENTITY_EVIDENCE_DOC_ID", None)
        os.environ.pop("BRIDGE_R29_PARTICIPANT_REGISTRY_DOC_ID", None)
        os.environ.pop("BRIDGE_MASTER_PDF_DRIVE_ID", None)
        os.environ.pop("GITHUB_OUTPUT", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old)

    @staticmethod
    def fake_download(_token, _file_id, out):
        Path(out).write_text(json.dumps(done_payload()), encoding="utf-8")

    def test_no_private_identity_evidence_preserves_anonymous_transcript(self):
        uploaded = []
        with mock.patch.object(stage.io, "search", return_value=[{"id": "done1", "modifiedTime": "2026-01-01"}]), \
             mock.patch.object(stage.io, "download", side_effect=self.fake_download), \
             mock.patch.object(stage.io, "upload_json", side_effect=lambda t, p, n, o: uploaded.append((p, n, o)) or {"id": "receipt"}), \
             mock.patch.object(stage.r29, "main") as r29_main:
            result = stage.run("token")
        self.assertEqual(result["status"], "SPEAKER_MAPPING_ANONYMOUS_ONLY")
        self.assertFalse(result["named_attribution"])
        self.assertEqual(len(uploaded), 1)
        self.assertFalse(uploaded[0][2]["namedAttributionAllowed"])
        self.assertFalse(uploaded[0][2]["personSpecificWritesAllowed"])
        r29_main.assert_not_called()

    def test_partial_private_config_fails_closed(self):
        os.environ["BRIDGE_R29_IDENTITY_EVIDENCE_DOC_ID"] = "evidence123"
        uploaded = []
        with mock.patch.object(stage.io, "search", return_value=[{"id": "done1", "modifiedTime": "2026-01-01"}]), \
             mock.patch.object(stage.io, "download", side_effect=self.fake_download), \
             mock.patch.object(stage.io, "upload_json", side_effect=lambda t, p, n, o: uploaded.append(o) or {"id": "receipt"}):
            with self.assertRaisesRegex(RuntimeError, "R29_IDENTITY_CONFIG_INCOMPLETE"):
                stage.run("token")
        self.assertEqual(uploaded[0]["status"], "SPEAKER_MAPPING_BLOCKED")
        self.assertFalse(uploaded[0]["personSpecificWritesAllowed"])

    def test_complete_private_config_invokes_validated_r29_overlay(self):
        os.environ["BRIDGE_R29_IDENTITY_EVIDENCE_DOC_ID"] = "evidence123"
        os.environ["BRIDGE_R29_PARTICIPANT_REGISTRY_DOC_ID"] = "registry123"
        searches = [
            [{"id": "done1", "modifiedTime": "2026-01-01"}],
            [],
        ]
        with mock.patch.object(stage.io, "search", side_effect=searches), \
             mock.patch.object(stage.io, "download", side_effect=self.fake_download), \
             mock.patch.object(stage.r29, "main") as r29_main:
            result = stage.run("token")
        self.assertEqual(result["status"], "SPEAKER_MAPPING_OPERATIONAL")
        self.assertTrue(result["named_attribution"])
        self.assertEqual(os.environ["BRIDGE_MASTER_PDF_DRIVE_ID"], MASTER)
        r29_main.assert_called_once_with()

    def test_existing_operational_map_is_idempotent(self):
        os.environ["BRIDGE_R29_IDENTITY_EVIDENCE_DOC_ID"] = "evidence123"
        os.environ["BRIDGE_R29_PARTICIPANT_REGISTRY_DOC_ID"] = "registry123"
        receipt = {
            "status": "SPEAKER_MAPPING_OPERATIONAL",
            "algorithmRevision": stage.R29_REVISION,
            "job_id": JOB,
            "sourceMasterPdfDriveId": MASTER,
        }
        def download(_token, file_id, out):
            payload = done_payload() if file_id == "done1" else receipt
            Path(out).write_text(json.dumps(payload), encoding="utf-8")
        searches = [
            [{"id": "done1", "modifiedTime": "2026-01-01"}],
            [{"id": "r29receipt", "modifiedTime": "2026-01-02"}],
        ]
        with mock.patch.object(stage.io, "search", side_effect=searches), \
             mock.patch.object(stage.io, "download", side_effect=download), \
             mock.patch.object(stage.r29, "main") as r29_main:
            result = stage.run("token")
        self.assertEqual(result["status"], "SPEAKER_MAPPING_ALREADY_OPERATIONAL")
        r29_main.assert_not_called()


if __name__ == "__main__":
    unittest.main()
