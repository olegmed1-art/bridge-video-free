"""Prospective one-shot Linux entry; staging and live execution are not authorized here."""
import hashlib
import json
import os
from pathlib import Path
import resource
import sys

EXPECTED = "9eaf3fb91a2ba30f49a50eaa250a4d5e62ebd3af2ddb608a999d47e17ec7d5d5"

try:
    assert len(sys.argv) == 1 and sys.platform == "linux" and sys.version_info[:2] == (3, 12)
    assert os.getpid() == 1 and os.getuid() != 0 and os.geteuid() == os.getuid()
    assert os.getgroups() == [] and dict(os.environ) == {"LC_ALL": "C.UTF-8"}
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    assert status["NoNewPrivs"].strip() == "1"
    assert all(int(status[key], 16) == 0 for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    probe = Path(__file__).resolve().with_name("probe.py")
    assert hashlib.sha256(probe.read_bytes()).hexdigest() == EXPECTED
    os.execv(sys.executable, [sys.executable, "-I", "-S", "-B", str(probe)])
except BaseException:
    print(json.dumps({"schema": "stdlib-tls-entry-v1", "status": "entry_rejected"}, separators=(",", ":")))
    raise SystemExit(2)

