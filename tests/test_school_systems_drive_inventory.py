from __future__ import annotations

import json
from pathlib import Path

import pytest

from steward.drive_inventory import (
    DriveInventoryError,
    DriveListClient,
    build_drive_inventory,
    write_inventory_outputs,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)


def item(
    item_id,
    name,
    mime,
    *,
    parents,
    size=None,
    md5=None,
    shortcut=None,
    video=None,
):
    result = {
        "id": item_id,
        "name": name,
        "mimeType": mime,
        "parents": parents,
    }
    if size is not None:
        result["size"] = str(size)
    if md5 is not None:
        result["md5Checksum"] = md5
    if shortcut is not None:
        result["shortcutDetails"] = shortcut
    if video is not None:
        result["videoMediaMetadata"] = video
    return result


def test_client_proves_pagination_and_uses_get_only():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "files": [
                        {
                            "id": "file_abcdefghij",
                            "name": "a",
                            "mimeType": "text/plain",
                        }
                    ],
                    "nextPageToken": "p2",
                }
            ),
            FakeResponse(
                {
                    "files": [
                        {
                            "id": "file_klmnopqrst",
                            "name": "b",
                            "mimeType": "text/plain",
                        }
                    ]
                }
            ),
        ]
    )
    client = DriveListClient(
        token_provider=lambda: "secret-token",
        session=session,
        page_size=2,
    )
    results = client.list_children("folder_abcdefghij")
    assert [row["name"] for row in results] == ["a", "b"]
    assert len(session.calls) == 2
    assert all(call[0].endswith("/files") for call in session.calls)
    assert (
        session.calls[0][1]["headers"]["Authorization"]
        == "Bearer secret-token"
    )
    assert "pageToken" not in session.calls[0][1]["params"]
    assert session.calls[1][1]["params"]["pageToken"] == "p2"


def test_client_rejects_repeated_page_token():
    session = FakeSession(
        [
            FakeResponse({"files": [], "nextPageToken": "same"}),
            FakeResponse({"files": [], "nextPageToken": "same"}),
        ]
    )
    client = DriveListClient(token_provider=lambda: "token", session=session)
    with pytest.raises(
        DriveInventoryError,
        match="DRIVE_LIST_REPEATED_PAGE_TOKEN",
    ):
        client.list_children("folder_abcdefghij")


def test_recursive_inventory_discovers_nested_videos_and_paths():
    root = "root_abcdefghijk"
    course = "course_abcdefgh"
    pages = {
        root: [
            item(
                course,
                "Диана",
                "application/vnd.google-apps.folder",
                parents=[root],
            ),
            item(
                "file_abcdefghij",
                "readme.txt",
                "text/plain",
                parents=[root],
                size=4,
            ),
        ],
        course: [
            item(
                "video_abcdefghij",
                "Диана 229.mp4",
                "video/mp4",
                parents=[course],
                size=100,
                md5="a" * 32,
                video={
                    "durationMillis": "1234",
                    "width": 1920,
                    "height": 1080,
                },
            ),
            item(
                "video_klmnopqrst",
                "legacy.MKV",
                "application/octet-stream",
                parents=[course],
                size=200,
            ),
        ],
    }

    class Client:
        def list_children(self, folder_id):
            return pages[folder_id]

    manifest = build_drive_inventory(
        Client(),
        root_folder_id=root,
        root_name="School",
    )
    assert manifest["status"] == "COMPLETE"
    assert manifest["counts"]["folders"] == 2
    assert manifest["counts"]["files"] == 3
    assert manifest["counts"]["videos"] == 2
    assert manifest["counts"]["extension_detected_videos"] == 1
    assert [video["path"] for video in manifest["videos"]] == [
        "School/Диана/legacy.MKV",
        "School/Диана/Диана 229.mp4",
    ]
    root_record = next(
        folder for folder in manifest["folders"] if folder["id"] == root
    )
    assert root_record["recursive_file_count"] == 3
    assert root_record["recursive_size_bytes"] == 304


def test_duplicate_folder_reference_fails_closed():
    root = "root_abcdefghijk"
    folder = "folder_abcdefghij"

    class Client:
        def list_children(self, folder_id):
            if folder_id == root:
                return [
                    item(
                        folder,
                        "one",
                        "application/vnd.google-apps.folder",
                        parents=[root],
                    ),
                    item(
                        folder,
                        "two",
                        "application/vnd.google-apps.folder",
                        parents=[root],
                    ),
                ]
            return []

    with pytest.raises(
        DriveInventoryError,
        match="DRIVE_DUPLICATE_FOLDER_REFERENCE",
    ):
        build_drive_inventory(
            Client(),
            root_folder_id=root,
            root_name="School",
        )


def test_shortcut_alias_does_not_double_count_folder_contents():
    root = "root_abcdefghijk"
    folder = "folder_abcdefghij"
    shortcut = "shortcut_abcdefgh"

    class Client:
        def list_children(self, folder_id):
            if folder_id == root:
                return [
                    item(
                        folder,
                        "real",
                        "application/vnd.google-apps.folder",
                        parents=[root],
                    ),
                    item(
                        shortcut,
                        "alias",
                        "application/vnd.google-apps.shortcut",
                        parents=[root],
                        shortcut={
                            "targetId": folder,
                            "targetMimeType": (
                                "application/vnd.google-apps.folder"
                            ),
                        },
                    ),
                ]
            return [
                item(
                    "file_abcdefghij",
                    "x.mp4",
                    "video/mp4",
                    parents=[folder],
                    size=9,
                )
            ]

    manifest = build_drive_inventory(
        Client(),
        root_folder_id=root,
        root_name="School",
    )
    assert manifest["counts"]["folders"] == 2
    assert manifest["counts"]["videos"] == 1
    record = next(
        folder_row
        for folder_row in manifest["folders"]
        if folder_row["id"] == folder
    )
    assert record["alias_paths"] == ["School/alias"]


def test_duplicate_groups_are_exact_or_probable_not_silently_merged():
    root = "root_abcdefghijk"

    class Client:
        def list_children(self, folder_id):
            return [
                item(
                    "file_abcdefghij",
                    "Lesson.mp4",
                    "video/mp4",
                    parents=[root],
                    size=10,
                    md5="b" * 32,
                ),
                item(
                    "file_klmnopqrst",
                    "Copy of Lesson.mp4",
                    "video/mp4",
                    parents=[root],
                    size=10,
                    md5="b" * 32,
                ),
                item(
                    "file_uvwxabcdef",
                    "Other (1).mp4",
                    "video/mp4",
                    parents=[root],
                    size=11,
                    md5="c" * 32,
                ),
                item(
                    "file_ghijklmnop",
                    "Other.mp4",
                    "video/mp4",
                    parents=[root],
                    size=11,
                    md5="d" * 32,
                ),
            ]

    manifest = build_drive_inventory(
        Client(),
        root_folder_id=root,
        root_name="School",
    )
    assert manifest["counts"]["exact_duplicate_groups"] == 1
    assert manifest["duplicates"]["exact"][0]["file_ids"] == [
        "file_klmnopqrst",
        "file_abcdefghij",
    ]
    assert manifest["counts"]["probable_duplicate_groups"] == 1
    assert (
        manifest["duplicates"]["probable"][0]["classification"]
        == "PROBABLE_ONLY"
    )


def test_inventory_checksum_is_deterministic_despite_generated_time():
    root = "root_abcdefghijk"

    class Client:
        def list_children(self, folder_id):
            return [
                item(
                    "file_abcdefghij",
                    "a.txt",
                    "text/plain",
                    parents=[root],
                    size=1,
                )
            ]

    one = build_drive_inventory(
        Client(),
        root_folder_id=root,
        root_name="School",
    )
    two = build_drive_inventory(
        Client(),
        root_folder_id=root,
        root_name="School",
    )
    assert one["inventory_sha256"] == two["inventory_sha256"]


def test_no_complete_outputs_are_written_after_traversal_failure(tmp_path: Path):
    root = "root_abcdefghijk"

    class Client:
        def list_children(self, folder_id):
            raise DriveInventoryError("DRIVE_LIST_HTTP_403")

    with pytest.raises(DriveInventoryError):
        manifest = build_drive_inventory(
            Client(),
            root_folder_id=root,
            root_name="School",
        )
        write_inventory_outputs(manifest, tmp_path)
    assert not (tmp_path / "drive-inventory.json").exists()


def test_outputs_are_machine_readable_and_complete(tmp_path: Path):
    root = "root_abcdefghijk"

    class Client:
        def list_children(self, folder_id):
            return [
                item(
                    "video_abcdefghij",
                    "a.mp4",
                    "video/mp4",
                    parents=[root],
                    size=5,
                )
            ]

    manifest = build_drive_inventory(
        Client(),
        root_folder_id=root,
        root_name="School",
    )
    outputs = write_inventory_outputs(manifest, tmp_path)
    loaded = json.loads(outputs["manifest"].read_text(encoding="utf-8"))
    assert loaded["status"] == "COMPLETE"
    assert loaded["counts"]["videos"] == 1
    assert outputs["videos_csv"].read_text(
        encoding="utf-8"
    ).splitlines()[0].startswith("id,name,path")
