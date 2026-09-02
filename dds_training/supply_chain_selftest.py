from __future__ import annotations

import json
import re
from pathlib import Path

DDS_COMMIT = "37c8a79f4c67c55d1a309ccb66dd00cb58af464a"
ACTION_SHA_RE = re.compile(r"uses:\s*actions/(checkout|setup-python|cache(?:/restore|/save)?|upload-artifact)@([0-9a-f]{40})")
FLOATING_ACTION_RE = re.compile(r"uses:\s*actions/(checkout|setup-python|cache(?:/restore|/save)?|upload-artifact)@v\d+")


def main() -> None:
    root = Path(__file__).resolve().parent
    repo = root.parent
    bootstrap = (root / "bootstrap_linux.sh").read_text(encoding="utf-8")
    assert f'DDS_COMMIT="{DDS_COMMIT}"' in bootstrap
    assert "git -C \"$DDS_DIR\" fetch --quiet --depth 1 origin \"$DDS_COMMIT\"" in bootstrap
    assert "ACTUAL_DDS_COMMIT" in bootstrap
    assert "browser_download_url" in bootstrap and "asset.get('digest')" in bootstrap
    assert "Bazelisk SHA-256 mismatch" in bootstrap
    assert "dds3-wheel-cache-v1" in bootstrap
    assert "DDS_REQUIRE_WHEEL_CACHE" in bootstrap
    assert "pip install --upgrade" not in bootstrap
    assert "git clone --depth 1 --branch v3.0.0" not in bootstrap

    workflows = repo / ".github" / "workflows"
    checked = []
    floating = {}
    unpinned = {}
    for path in sorted(workflows.glob("dds*.yml")):
        text = path.read_text(encoding="utf-8")
        if FLOATING_ACTION_RE.search(text):
            floating[path.name] = FLOATING_ACTION_RE.findall(text)
        action_lines = [line.strip() for line in text.splitlines() if "uses: actions/" in line]
        bad = [line for line in action_lines if not ACTION_SHA_RE.search(line)]
        if bad:
            unpinned[path.name] = bad
        checked.append(path.name)
    assert not floating, floating
    assert not unpinned, unpinned

    independent = (workflows / "dds-training-local-smoke.yml").read_text(encoding="utf-8")
    assert "dc2d4df68d21822c18fc1688b9f6f183c927c11c" in independent
    assert "hashFiles('dds_training/bootstrap_linux.sh')" in independent
    assert "DDS_REQUIRE_WHEEL_CACHE=1" in independent

    print(json.dumps({
        "ok": True,
        "dds_commit": DDS_COMMIT,
        "bazelisk_digest_required": True,
        "verified_wheel_cache_required": True,
        "floating_github_actions": 0,
        "checked_workflows": checked,
        "independent_solver_pinned": True,
    }, indent=2))


if __name__ == "__main__":
    main()
