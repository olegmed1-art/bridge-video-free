"""Public hash-pinned artifact staging only; no pip, imports of wheels or secrets."""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
BASE = "https://raw.githubusercontent.com/olegmed1-art/bridge-video-free/"
PREFIX = "scripts/diagnostics/psycopg_once/"
BASE_MAIN = "d49feb4cb333b041928a5d37efb56a9f97dd1822"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("redirect")


def download(url, expected_hash, expected_size):
    with urllib.request.build_opener(NoRedirect()).open(url, timeout=5) as response:
        if response.status != 200 or response.geturl() != url:
            raise ValueError("response")
        data = response.read(expected_size + 1)
    if len(data) != expected_size or hashlib.sha256(data).hexdigest() != expected_hash:
        raise ValueError("artifact_integrity")
    return data


def extract_wheel(data, directory):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 2000 or sum(x.file_size for x in entries) > 33554432:
            raise ValueError("archive_budget")
        for item in entries:
            path = PurePosixPath(item.filename)
            mode = (item.external_attr >> 16) & 0xFFFF
            if (path.is_absolute() or ".." in path.parts or "\\" in item.filename or ":" in item.filename or
                    stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR))):
                raise ValueError("archive_path")
            target = directory.joinpath(*path.parts)
            if not target.resolve().is_relative_to(directory.resolve()):
                raise ValueError("archive_escape")
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as output:
                    output.write(archive.read(item))


def stage(env=os.environ, root=ROOT, fetch=download):
    sha = env.get("GITHUB_SHA", "")
    if (not re.fullmatch("[0-9a-f]{40}", sha) or env.get("GITHUB_WORKFLOW_SHA") != sha or
            env.get("GITHUB_REPOSITORY") != "olegmed1-art/bridge-video-free" or
            env.get("GITHUB_EVENT_NAME") != "create" or env.get("GITHUB_RUN_ATTEMPT") != "1" or
            env.get("BRIDGE_APP_DATABASE_URL")):
        raise ValueError("bootstrap_context")
    branches = {"codex-incident-1911-psycopg-preflight-20261012": "1",
                "codex-incident-1911-psycopg-live-20261012": "2"}
    branch = env.get("GITHUB_REF_NAME")
    if branch not in branches or env.get("GITHUB_RUN_NUMBER") != branches[branch] or env.get("GITHUB_REF") != "refs/heads/" + branch:
        raise ValueError("bootstrap_branch")
    event = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text())
    if event.get("ref_type") != "branch" or event.get("ref") != branch or event.get("repository", {}).get("full_name") != env["GITHUB_REPOSITORY"]:
        raise ValueError("bootstrap_event")
    lock = json.loads((root / "package-lock.json").read_text())
    if lock.get("base_main") != BASE_MAIN:
        raise ValueError("base")
    for name, info in lock["files"].items():
        if not re.fullmatch(r"[a-z_]+\.py", name):
            raise ValueError("filename")
        (root / name).write_bytes(fetch(BASE + sha + "/" + PREFIX + name, info["sha256"], info["bytes"]))
    info = lock["db_contract"]
    (root / "db_contract.py").write_bytes(fetch(BASE + BASE_MAIN + "/bridge_school_api/db.py", info["sha256"], info["bytes"]))
    directory = root / "runtime"
    directory.mkdir()
    for wheel in lock["wheels"]:
        if not wheel["url"].startswith("https://files.pythonhosted.org/packages/"):
            raise ValueError("wheel_origin")
        extract_wheel(fetch(wheel["url"], wheel["sha256"], wheel["bytes"]), directory)
    # No third-party code is imported during download/extraction.
    (root / "bootstrap-receipt.json").write_text(json.dumps({"schema": "incident-1911-staged-v2", "head_sha": sha,
                       "public_wheels": len(lock["wheels"]), "client_imported": False}), encoding="ascii")


if __name__ == "__main__":
    try:
        stage()
        print('{"status":"public_artifacts_staged"}')
    except BaseException:
        print('{"status":"bootstrap_failed"}')
        raise SystemExit(1)
