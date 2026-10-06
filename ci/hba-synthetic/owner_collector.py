"""Generic synthetic collector; live entrypoint DISABLED.

No DB/SQL, parser claim, reload, permissions change or output of original rules.
Private export files are intentional output writes, not source mutations.
"""
import base64
import json
import os
import stat
import sys
import time
import uuid
from pathlib import Path
from hba_helper import Refused, snapshot, same, exclusive_file, sha

def directory_binding(fd):
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode):
        raise Refused("Destination component is not a directory")
    # Kernel fdinfo ties mount identity to the held descriptor, not path lookup.
    with open("/proc/self/fdinfo/" + str(fd), "rb") as stream:
        data = stream.read(8193)
    if len(data) > 8192:
        raise Refused("fdinfo budget exceeded")
    mounts = [line.split(b":", 1)[1].strip() for line in data.splitlines()
              if line.startswith(b"mnt_id:")]
    if len(mounts) != 1 or not mounts[0].isdigit():
        raise Refused("Mount identity unavailable")
    attrs = {key: base64.b64encode(os.getxattr(fd, key)).decode()
             for key in os.listxattr(fd)}
    return {"dev": info.st_dev, "ino": info.st_ino, "uid": info.st_uid,
            "gid": info.st_gid, "mode": stat.S_IMODE(info.st_mode),
            "mount_id": int(mounts[0]),
            "xattrs_sha256": sha(json.dumps(attrs, sort_keys=True).encode())}


def bind_private_destination(path, approved, *, synthetic_parent_fd=None):
    """Hold each component; require externally approved exact bindings for live.
    
    Synthetic capture is only allowed beneath the already held marker fixture.
    O_NOFOLLOW applies at every component. Missing mount/xattr proof refuses.
    """
    path = str(path)
    parts = path.split("/")
    if not path.startswith("/") or any(p in ("", ".", "..") for p in parts[1:]):
        raise Refused("Canonical absolute destination path required")
    if synthetic_parent_fd is not None and (len(parts) != 4 or parts[1] != "tmp" or
            not parts[2].startswith("hba-synthetic-") or parts[3] != ".private-export"):
        raise Refused("Exact synthetic destination shape required")
    if approved is None:
        if synthetic_parent_fd is None:
            raise Refused("Approved destination bindings missing")
    elif (not isinstance(approved, dict) or approved.get("path") != path or
          not isinstance(approved.get("components"), list) or
          len(approved["components"]) != len(parts)):
        raise Refused("Approved destination contract mismatch")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    bindings = []
    try:
        for index, component in enumerate(parts):
            if index:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=fd)
                os.close(fd)
                fd = child
            actual = directory_binding(fd)
            entry = {"component": "/" if index == 0 else component, **actual}
            bindings.append(entry)
            if approved is not None and entry != approved["components"][index]:
                raise Refused("Destination identity/mount/ACL drift")
            # Writable ancestors only accepted for the explicit synthetic /tmp
            # sticky directory. Live contracts cannot grant this exception.
            synthetic_tmp = (synthetic_parent_fd is not None and index == 1 and component == "tmp" and
                             actual["uid"] == 0 and actual["mode"] & 0o1000)
            if (actual["uid"] not in (0, os.geteuid()) or
                    (actual["mode"] & 0o022 and not synthetic_tmp)):
                raise Refused("Untrusted destination ancestor")
            if synthetic_parent_fd is not None and index == len(parts) - 2:
                if actual != directory_binding(synthetic_parent_fd):
                    raise Refused("Synthetic parent binding changed")
        final = bindings[-1]
        if final["uid"] != os.geteuid() or final["mode"] != 0o700:
            raise Refused("Private destination must be owned and exactly 0700")
        return fd, {"path": path, "components": bindings}
    except BaseException:
        os.close(fd)
        raise


def private_projection(source_fd, private_fd):
    private_info = os.fstat(private_fd)
    if (not stat.S_ISDIR(private_info.st_mode) or private_info.st_uid != os.geteuid() or
            stat.S_IMODE(private_info.st_mode) != 0o700):
        raise Refused("Existing private output directory must be owned and mode 0700")
    info = snapshot(source_fd, "pg_hba.conf")
    try:
        lines = info["data"].decode("utf-8").splitlines()
        utf8 = True
    except UnicodeDecodeError:
        lines, utf8 = [], False
    active = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
    include_hint = any(line.split(maxsplit=1)[0] in ("include", "include_if_exists", "include_dir")
                       for line in active)
    opaque = "audit-" + uuid.uuid4().hex
    # The private metadata may contain binary ACL/label values. Never print it.
    metadata = {k: info[k] for k in ("sha256", "uid", "gid", "mode", "dev", "ino", "atime_ns", "mtime_ns", "ctime_ns", "inode_flags")}
    metadata["attrs"] = {k: base64.b64encode(v).decode() for k, v in info["attrs"].items()}
    exclusive_file(private_fd, opaque + ".bytes", info["data"])
    exclusive_file(private_fd, opaque + ".metadata.json", json.dumps(metadata).encode())
    os.fsync(private_fd)
    if not same(snapshot(source_fd, "pg_hba.conf"), info):
        # Output remains private, with no PASS verdict; no overwrite or deletion.
        raise Refused("Source changed during export; private snapshot requires review")
    return {"status": "METADATA_EXPORT_ONLY_POLICY_UNVERIFIED", "captured_at_ns": time.time_ns(),
            "sha256": info["sha256"], "bytes": len(info["data"]), "uid": info["uid"],
            "gid": info["gid"], "mode_octal": format(info["mode"], "04o"),
            "device": info["dev"], "inode": info["ino"], "mtime_ns": info["mtime_ns"],
            "xattr_count": len(info["attrs"]), "posix_acl_present": "system.posix_acl_access" in info["attrs"],
            "selinux_label_present": "security.selinux" in info["attrs"],
            "utf8_decodable": utf8, "line_count": len(lines), "noncomment_lines": len(active),
            "lexical_include_hint": include_hint, "lexical_only": True,
            "native_parser_verified": False, "loaded_rules_verified": False,
            "inode_flags": info["inode_flags"], "inode_flags_policy": "ZERO_OR_EXTENTS_ONLY",
            "private_export_id": opaque, "live": False}


def collect_synthetic(root, private_output):
    if sys.platform != "linux":
        raise Refused("Native Linux required")
    root = Path(root)
    private_output = Path(private_output)
    if root.parent != Path("/tmp") or not root.name.startswith("hba-synthetic-"):
        raise Refused("Synthetic source only")
    if private_output != root / ".private-export":
        raise Refused("Exact synthetic private output only")
    source_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    private_fd = None
    try:
        root_info = os.fstat(source_fd)
        if root_info.st_uid != os.geteuid() or stat.S_IMODE(root_info.st_mode) != 0o700:
            raise Refused("Unsafe synthetic source directory")
        if snapshot(source_fd, ".synthetic-hba")["data"] != b"HBA_SYNTHETIC_ONLY_V1\n":
            raise Refused("Missing synthetic marker")
        private_fd, _ = bind_private_destination(private_output, None, synthetic_parent_fd=source_fd)
        return private_projection(source_fd, private_fd)
    finally:
        if private_fd is not None:
            os.close(private_fd)
        os.close(source_fd)


def execute_live(*args, **kwargs):
    raise Refused("LIVE DISABLED: generic synthetic collector only")


if __name__ == "__main__":
    raise SystemExit("LIVE DISABLED")
