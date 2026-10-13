"""Only hash-pinned public files/wheels. Extraction, no installation/import."""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
BRANCH = "codex-incident-1911-native-toolchain-path-20261013"
PREFIX = "scripts/diagnostics/native_phase_preflight/"
BASE = "https://raw.githubusercontent.com/olegmed1-art/bridge-video-free/"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("redirect")


def download(url, pin):
    with urllib.request.build_opener(NoRedirect()).open(url, timeout=4) as response:
        if response.status != 200 or response.geturl() != url:
            raise ValueError("response")
        data = response.read(pin["bytes"] + 1)
    if len(data) != pin["bytes"] or hashlib.sha256(data).hexdigest() != pin["sha256"]:
        raise ValueError("integrity")
    return data


def extract(data, root):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 2000 or sum(x.file_size for x in entries) > 33554432:
            raise ValueError("archive_budget")
        for entry in entries:
            if "\\" in entry.orig_filename or ":" in entry.orig_filename:
                raise ValueError("raw_archive_path")
            path = PurePosixPath(entry.filename)
            mode = (entry.external_attr >> 16) & 0xFFFF
            if (path.is_absolute() or ".." in path.parts or "\\" in entry.filename or ":" in entry.filename or
                    stat.S_ISLNK(mode) or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise ValueError("archive_path")
            target = root.joinpath(*path.parts)
            if not target.resolve().is_relative_to(root.resolve()):
                raise ValueError("archive_escape")
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as output:
                    output.write(archive.read(entry))


def stage():
    env = os.environ
    sha = env.get("GITHUB_SHA", "")
    if (not re.fullmatch("[0-9a-f]{40}", sha) or env.get("GITHUB_WORKFLOW_SHA") != sha or
            env.get("GITHUB_REPOSITORY") != "olegmed1-art/bridge-video-free" or
            env.get("GITHUB_EVENT_NAME") != "create" or env.get("GITHUB_REF_NAME") != BRANCH or
            env.get("GITHUB_REF") != "refs/heads/" + BRANCH or env.get("GITHUB_RUN_ATTEMPT") != "1" or
            env.get("GITHUB_RUN_NUMBER") != "1"):
        raise ValueError("context")
    event = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text())
    if event.get("ref") != BRANCH or event.get("ref_type") != "branch" or event.get("repository", {}).get("full_name") != env["GITHUB_REPOSITORY"]:
        raise ValueError("event")
    lock = json.loads((ROOT / "package-lock.json").read_text())
    if set(lock) != {"schema", "base_main", "files", "wheels"} or lock["schema"] != "native-phase-preflight-package-v1":
        raise ValueError("lock")
    for name, pin in lock["files"].items():
        if not re.fullmatch(r"[a-z_]+\.(py|c)", name):
            raise ValueError("filename")
        with (ROOT / name).open("xb") as output:
            output.write(download(BASE + sha + "/" + PREFIX + name, pin))
    runtime = ROOT / "runtime"
    runtime.mkdir()
    for wheel in lock["wheels"]:
        if not wheel["url"].startswith("https://files.pythonhosted.org/packages/"):
            raise ValueError("origin")
        extract(download(wheel["url"], wheel), runtime)


if __name__ == "__main__":
    try:
        stage()
        print('{"status":"public_artifacts_staged"}')
    except BaseException:
        print('{"status":"public_bootstrap_failed"}')
        raise SystemExit(1)
