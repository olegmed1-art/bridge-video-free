#!/usr/bin/env python3
"""Manual administrator rollout: Light observer JSON fix, circuit retained."""
import ast
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import urllib.request

ROOT = Path("/opt/bridge-school/school-autopilot-online-observer")
UNIT = "school-autopilot-online-observer.service"
CONSUMER = "school-autopilot-shadow.service"
STATE = Path("/var/lib/school-autopilot-online-observer")
REVISION = "77e055081b65c461d7d0c06b84a67e3604cf8211"
EXPECTED = "6fc46df95ce087a12649c8a454b9c9028eb4a83f93e43157908e4d03d96c9da5"
OLD_BLOB = "d7abaa67eddb6349c3c3498abb9a123dc3034ec3"

def run(*args):
    return subprocess.check_output(args, text=True).strip()

def switch(target):
    temporary = ROOT / "current.datetime-next"
    if temporary.exists() or temporary.is_symlink():
        raise RuntimeError("Pending release switch exists")
    temporary.symlink_to(target)
    os.replace(temporary, ROOT / "current")

def main():
    if os.geteuid() != 0:
        raise RuntimeError("Run manually as administrator")
    if socket.gethostname() not in {"autopilot-lite-vnic", "bridge-school-autopilot-lite"}:
        raise RuntimeError("Not the approved Light host")
    with open("/run/lock/light-observer-datetime.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        old = (ROOT / "current").resolve(strict=True)
        if old.parent != ROOT / "releases":
            raise RuntimeError("Unexpected observer release location")
        source = old / "oracle_autopilot/online_observer.py"
        previous = source.read_bytes()
        blob = hashlib.sha1(b"blob " + str(len(previous)).encode() + b"\0" + previous).hexdigest()
        if blob != OLD_BLOB:
            raise RuntimeError("Deployed observer changed; stop for review")
        if run("systemctl", "is-active", UNIT) != "active":
            raise RuntimeError("Observer is not active")
        consumer_pid = run("systemctl", "show", CONSUMER, "-p", "MainPID", "--value")
        marker = STATE / "circuit-open.json"
        marker_before = marker.read_bytes()  # Require and preserve the existing safety latch.
        url = ("https://raw.githubusercontent.com/olegmed1-art/bridge-video-free/"
               + REVISION + "/oracle_autopilot/online_observer.py")
        with urllib.request.urlopen(url, timeout=20) as response:
            content = response.read(100000)
        if hashlib.sha256(content).hexdigest() != EXPECTED:
            raise RuntimeError("Downloaded source checksum mismatch")
        ast.parse(content)
        new = ROOT / "releases" / ("datetime-" + REVISION)
        if new.exists():
            raise RuntimeError("Target release already exists; stop for review")
        shutil.copytree(old, new, symlinks=True)
        target = new / "oracle_autopilot/online_observer.py"
        if target.is_symlink():
            raise RuntimeError("Unexpected source symlink")
        target.write_bytes(content)
        os.chmod(target, source.stat().st_mode & 0o777)
        (new / "DATETIME_HOTFIX.json").write_text(json.dumps({
            "patch_revision": REVISION, "source_sha256": EXPECTED,
            "previous_release": str(old),
        }) + "\n")
        # The untouched old release is the rollback target.
        started = time.time()
        switch(new)
        try:
            subprocess.run(["systemctl", "restart", UNIT], check=True, timeout=40)
            seen = set()
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                heartbeat = STATE / "heartbeat.json"
                if heartbeat.exists() and heartbeat.stat().st_mtime > started:
                    data = json.loads(heartbeat.read_text())
                    if data.get("runtime_mode") != "SHADOW_ONLY":
                        raise RuntimeError("Unexpected heartbeat mode")
                    seen.add(data["updated_at"])
                    if len(seen) >= 2:
                        break
                time.sleep(2)
            if len(seen) < 2:
                raise RuntimeError("Two fresh heartbeats were not observed")
            if marker.read_bytes() != marker_before:
                raise RuntimeError("Circuit marker changed")
            if run("systemctl", "show", CONSUMER, "-p", "MainPID", "--value") != consumer_pid:
                raise RuntimeError("Consumer PID changed")
            if run("systemctl", "is-active", UNIT) != "active":
                raise RuntimeError("Observer not active")
        except BaseException:
            switch(old)
            subprocess.run(["systemctl", "restart", UNIT], check=True, timeout=40)
            print("ROLLED_BACK_TO=" + str(old))
            raise
        print("LIGHT_OBSERVER_DATETIME_FIX=PASS")
        print("FRESH_HEARTBEATS=2")
        print("LOCAL_CIRCUIT=PRESERVED")
        print("CONSUMER_PID=UNCHANGED")
        print("ROLLBACK_RELEASE=" + str(old))

if __name__ == "__main__":
    main()
