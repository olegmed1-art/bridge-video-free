"""Offline regression for the exact source pins, independent of checkout EOL.

These immutable blobs were verified at REVIEWED_COMMIT. They are also present
in the current tree, including CI's shallow checkout; no fetch or live API is
used. Missing fixture objects fail explicitly rather than falling back to a
possibly translated working-tree file. Repinning requires reviewed provenance.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/drive-metadata-inventory-manual.yml"
REVIEWED_COMMIT = "3a961557232aa3fad423c9958a9a34e86b6e041d"
ADAPTER = "universal_video/drive_adapter.py"
COLLECTOR = "universal_video/metadata_inventory.py"
SOURCE_BLOBS = {
    ADAPTER: ("a433da81c697b765fd7db53e7d16cb9577cbe727", 11804),
    COLLECTOR: ("f8267a7870478a6421649928ad9eda7d4fc08cb9", 17364),
}
OLD_CRLF_PIN = "b87f6dc7b9dac17c6ab45617014c5a2c93f6e82633447f734b9d3cc6de64b502"


def source_pins(text: str) -> dict[str, str]:
    manifest = text.split("sha256sum --check <<'PINS'\n", 1)[1].split("\n          PINS", 1)[0]
    pairs = re.findall(r"^\s*([a-f0-9]{64})  (\S+)$", manifest, re.M)
    assert len(pairs) == len(SOURCE_BLOBS), "exactly two source pins required"
    pins = {path: digest for digest, path in pairs}
    assert set(pins) == set(SOURCE_BLOBS), "source pin paths changed"
    return pins


def assert_exact_sources(text: str, sources: dict[str, bytes]) -> None:
    pins = source_pins(text)
    assert set(sources) == set(pins), "missing or unexpected source"
    for path, raw in sources.items():
        assert hashlib.sha256(raw).hexdigest() == pins[path], f"byte mismatch: {path}"


@pytest.fixture(scope="module")
def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def git_blob_sources() -> dict[str, bytes]:
    sources = {}
    for path, (oid, size) in SOURCE_BLOBS.items():
        raw = subprocess.check_output(["git", "cat-file", "blob", oid], cwd=ROOT)
        # Reconstruct Git's object identity as well as checking the byte length.
        assert hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == oid
        assert len(raw) == size and b"\r" not in raw
        sources[path] = raw
    return sources


def test_pinned_git_bytes_match_linux_checkout(workflow_text, git_blob_sources):
    assert f"REVIEWED_COMMIT: '{REVIEWED_COMMIT}'" in workflow_text
    assert_exact_sources(workflow_text, git_blob_sources)


def test_old_windows_pin_reproduces_actual_failure(workflow_text, git_blob_sources):
    raw = git_blob_sources[ADAPTER]
    windows_bytes = raw.replace(b"\n", b"\r\n")
    assert raw.count(b"\n") == 293 and len(windows_bytes) == 12097
    assert hashlib.sha256(windows_bytes).hexdigest() == OLD_CRLF_PIN
    canonical_pin = hashlib.sha256(raw).hexdigest()
    old_workflow = workflow_text.replace(canonical_pin, OLD_CRLF_PIN)
    with pytest.raises(AssertionError, match="byte mismatch: universal_video/drive_adapter.py"):
        assert_exact_sources(old_workflow, git_blob_sources)


@pytest.mark.parametrize("path", [ADAPTER, COLLECTOR])
@pytest.mark.parametrize("mutation", ["crlf", "one_byte", "missing"])
def test_noncanonical_byte_fixtures_fail_closed(workflow_text, git_blob_sources, path, mutation):
    sources = dict(git_blob_sources)
    raw = sources[path]
    if mutation == "crlf":
        sources[path] = raw.replace(b"\n", b"\r\n")
    elif mutation == "one_byte":
        sources[path] = bytes([raw[0] ^ 1]) + raw[1:]
    else:
        del sources[path]
    with pytest.raises(AssertionError, match="byte mismatch|missing or unexpected source"):
        assert_exact_sources(workflow_text, sources)


@pytest.mark.parametrize("path", [ADAPTER, COLLECTOR])
def test_wrong_digest_fixtures_fail_closed(workflow_text, git_blob_sources, path):
    digest = hashlib.sha256(git_blob_sources[path]).hexdigest()
    changed = workflow_text.replace(digest, "0" * 64)
    assert changed != workflow_text
    with pytest.raises(AssertionError, match="byte mismatch"):
        assert_exact_sources(changed, git_blob_sources)


def test_source_checks_remain_before_only_credential_step(workflow_text):
    gate = workflow_text.split("      - name: Require reviewed code", 1)[1].split("      - name: Capture metadata", 1)[0]
    assert "set -euo pipefail" in gate
    assert 'test "$GITHUB_SHA" = "$EXPECTED_MAIN_SHA"' in gate
    assert 'test "$(git rev-parse HEAD)" = "$REVIEWED_COMMIT"' in gate
    assert 'test "$(git rev-parse refs/remotes/origin/main)" = "$EXPECTED_MAIN_SHA"' in gate
    assert 'git merge-base --is-ancestor "$REVIEWED_COMMIT" "$EXPECTED_MAIN_SHA"' in gate
    assert "sha256sum --check <<'PINS'" in gate
    assert not any(bypass in gate for bypass in ("||", "continue-on-error", "dos2unix", "sed ", "tr "))
    assert "secrets." not in workflow_text.split("      - name: Capture metadata", 1)[0]
    assert workflow_text.count("${{ secrets.GOOGLE_DRIVE_OAUTH_JSON }}") == 1
    assert "continue-on-error:" not in workflow_text and "always()" not in workflow_text
    assert "permissions:\n  contents: read\n" in workflow_text
    assert "on:\n  workflow_dispatch:\n" in workflow_text
    triggers = workflow_text.split("on:\n", 1)[1].split("\npermissions:", 1)[0]
    assert not re.search(r"^  (?:push|pull_request|schedule):", triggers, re.M)
