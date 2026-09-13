#!/usr/bin/env python3
from unittest.mock import patch

from r29_identity_overlay_probe import _export_private_json


class FakeResponse:
    def __init__(self, *, content=b"", json_payload=None):
        self.content = content
        self._json_payload = json_payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._json_payload


def test_private_json_utf8_bom_is_accepted_from_raw_bytes():
    metadata = FakeResponse(json_payload={"mimeType": "text/plain", "trashed": False})
    payload = FakeResponse(content=b'\xef\xbb\xbf{"job_id":"abc","anchors":[]}')
    with patch("r29_identity_overlay_probe.requests.get", side_effect=[metadata, payload]):
        out = _export_private_json("token", "abcDEF_123")
    assert out == {"job_id": "abc", "anchors": []}


def test_private_json_without_bom_is_also_accepted():
    metadata = FakeResponse(json_payload={"mimeType": "text/plain", "trashed": False})
    payload = FakeResponse(content=b'{"job_id":"abc","anchors":[]}')
    with patch("r29_identity_overlay_probe.requests.get", side_effect=[metadata, payload]):
        out = _export_private_json("token", "abcDEF_123")
    assert out == {"job_id": "abc", "anchors": []}
