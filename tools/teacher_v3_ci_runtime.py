"""Pinned portable test-only Teacher/BML loader; no service activation."""
import sys
if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode) or sys.flags.optimize:
    raise RuntimeError("Required flags -I -S -B; assertions enabled")
import json
import os
from pathlib import Path
from hashlib import sha256
import types

ROOT = Path(__file__).absolute().parent.parent
TARGET = ROOT
MANIFEST_SHA = '428eda930af98800ebb0a43c91fa3afd4a99017f71045462f1f73b8b11585bd3'
ORDER = ("l1_canonical_registry", "l1_canonical_runtime", "l1_canonical_runtime_v2",
         "l1_canonical_runtime_v3", "l1_canonical_runtime_v4",
         "teacher_entry_blocking_adapter", "teacher_entry_blocking_reader_path",
         "teacher_v04_integration", "bml_ready_package_adapter")

def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)

def preflight():
    require(TARGET.resolve(strict=True) == TARGET and ROOT == TARGET, "Redirected local target")
    owner = ROOT
    require(ROOT.resolve(strict=True) == ROOT, "Redirected checkout root")
    manifest_path = (ROOT/"tests/teacher_v3_source_manifest.json").resolve(strict=True)
    require(manifest_path.parent == ROOT/"tests", "Redirected manifest")
    raw = manifest_path.read_bytes()
    require(sha256(raw).hexdigest() == MANIFEST_SHA, "Extended manifest differs")
    manifest = json.loads(raw)
    paths, payloads = {}, {}
    for f in manifest["files"]:
        relative = f["path"]
        p = (owner/relative).resolve(strict=True)
        require(p.is_relative_to(owner), "Source escapes owner-tree")
        data = p.read_bytes()
        require(sha256(data).hexdigest() == f["sha256"], "Source hash differs: "+relative)
        require(relative not in paths, "Duplicate source pin")
        paths[relative], payloads[relative] = p, data
    require(len(paths) == 23, "Expected23pinned sources")
    return manifest, owner, paths, payloads

def load_modules(paths, payloads):
    require("bridge_school_api" not in sys.modules, "Fresh isolated process required")
    package = types.ModuleType("bridge_school_api")
    package.__path__ = []
    sys.modules["bridge_school_api"] = package
    modules = {}
    for stem in ORDER:
        name, relative = "bridge_school_api."+stem, "bridge_school_api/"+stem+".py"
        module = types.ModuleType(name)
        module.__file__, module.__package__ = str(paths[relative]), "bridge_school_api"
        sys.modules[name] = module
        setattr(package, stem, module)
        exec(compile(payloads[relative], str(paths[relative]), "exec"), module.__dict__)
        modules[stem] = module
    return modules

class PinnedGuard:
    """Observational guard for these pinned sources; not an OS security boundary."""
    def __init__(self, allowed_paths, library_roots=()):
        self.allowed = {Path(p).resolve() for p in allowed_paths}
        self.roots = [Path(sys.base_prefix).resolve(), *map(lambda p:Path(p).resolve(), library_roots)]
        self.active, self.attempts = False, []
        sys.addaudithook(self.audit)
    def deny(self, event):
        self.attempts.append(event)
        raise RuntimeError("Pinned local execution denied external action: "+event)
    def audit(self,event,args):
        if not self.active:
            return
        if event == "os.chdir" and Path(args[0]).resolve() == ROOT:
            return  # Pytest restores the already-current directory on exit.
        if event.startswith(("socket.","subprocess.","urllib.","sqlite3.","os.exec","os.spawn")) or event in {
            "os.system","os.startfile","os.remove","os.rename","os.rmdir","os.mkdir",
            "os.chmod","os.chown","os.link","os.symlink","os.truncate","os.utime","os.chdir"}:
            self.deny(event)
        if event == "open":
            filename,mode,flags = args
            writing = ((isinstance(mode,str) and any(c in mode for c in "wax+")) or
                       (isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)))
            if writing:
                self.deny("file_write")
            if isinstance(filename,(str,bytes,os.PathLike)):
                p = Path(os.fsdecode(filename)).resolve()
                if "__pycache__" in p.parts and not p.exists():
                    return  # A missing cache may be probed; existing project bytecode stays denied.
                if p in self.allowed or any(p.is_relative_to(root) for root in self.roots):
                    return
            self.deny("unlisted_file_read")
    def __enter__(self):
        self.active = True
        return self
    def __exit__(self,*args):
        self.active = False

def post_hashes(paths):
    return {name:sha256(p.read_bytes()).hexdigest() for name,p in paths.items()}
