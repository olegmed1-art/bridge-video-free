"""Public-safe validation and source checks for the synthetic qualifier only."""
import hashlib,json,re
from pathlib import Path
from database import pr1994_stage_a as engine
ROOT=Path(__file__).resolve().parent
SOURCE="c36d581c22ca2ba5457e8db5019c5cf697542be4"
ENGINE_SHA="9fb8a403b5fd043f2d3272214848f6a565816c7a4a6d4d177edde93b863ad6f2"
MAX_OUTPUT=engine.MAX_OUTPUT
RUNTIME_FILES=frozenset(("stagea_synthetic_core.py","stagea_watchdog.py","stagea_control.py",
 "stagea_fixture_worker.py","stagea_capture.py","database/pr1994_stage_a.py",
 "database/__init__.py","ops/native_maintenance_lifetime.py","ops/__init__.py"))
class Refused(RuntimeError):pass
def need(value):
 if not value:raise Refused("SYNTHETIC_STAGE_A_REFUSED")
def unique(rows):
 result={}
 for key,value in rows:
  need(key not in result);result[key]=value
 return result
def source_guard(manifest_pin,root=ROOT):
 need(type(manifest_pin) is str and re.fullmatch("[0-9a-f]{64}",manifest_pin))
 need(root.is_dir() and not root.is_symlink() and all(not x.is_symlink() for x in root.parents))
 manifest_path=root/"runtime-manifest.json"
 need(manifest_path.is_file() and not manifest_path.is_symlink())
 raw=manifest_path.read_bytes();need(hashlib.sha256(raw).hexdigest()==manifest_pin)
 manifest=json.loads(raw,object_pairs_hook=unique)
 need(type(manifest) is dict and set(manifest)=={"source_sha","files"})
 need(manifest["source_sha"]==SOURCE and type(manifest["files"]) is dict and set(manifest["files"])==RUNTIME_FILES)
 for name,pin in manifest["files"].items():
  relative=Path(name)
  need(not relative.is_absolute() and ".." not in relative.parts and relative.as_posix()==name)
  need(type(pin) is str and re.fullmatch("[0-9a-f]{64}",pin))
  path=root/relative
  need(path.is_file() and not path.is_symlink() and all(not x.is_symlink() for x in path.parents))
  need(hashlib.sha256(path.read_bytes()).hexdigest()==pin)
 need(Path(engine.__file__).resolve()==(root/"database/pr1994_stage_a.py").resolve())
 need(manifest["files"]["database/pr1994_stage_a.py"]==ENGINE_SHA)
 return manifest
