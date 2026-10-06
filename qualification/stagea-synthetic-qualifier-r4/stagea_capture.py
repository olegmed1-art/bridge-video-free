"""Private local capture. Public receipt contains only fixed status and digest/size."""
import hashlib, json, os, re, stat, sys
from stagea_synthetic_core import Refused, need, MAX_OUTPUT, unique

def validate(raw, expected_runtime_id):
    need(type(raw) is bytes and 0 < len(raw) <= MAX_OUTPUT)
    need(type(expected_runtime_id) is str and re.fullmatch("[0-9a-f]{64}", expected_runtime_id))
    value = json.loads(raw, object_pairs_hook=unique)
    need(type(value) is dict and set(value) == {"stage","catalog_sha256","relations","functions","triggers",
         "production_mutations","stage_b_allowed","ddl_window_proven","runtime_id",
         "service_hold_unchanged","database_admission_observed"})
    need(value["runtime_id"] == expected_runtime_id)
    need(type(value) is dict and value.get("stage") == "A"
         and value.get("service_hold_unchanged") is True
         and value.get("database_admission_observed") is False
         and value.get("production_mutations") is False
         and value.get("stage_b_allowed") is False and value.get("ddl_window_proven") is False)
    need(re.fullmatch("[0-9a-f]{64}", value.get("runtime_id", ""))
         and re.fullmatch("[0-9a-f]{64}", value.get("catalog_sha256", "")))
    need(type(value.get("relations")) is list and len(value["relations"]) == 9
         and type(value.get("functions")) is list and len(value["functions"]) == 5
         and type(value.get("triggers")) is list)
    from database.pr1994_stage_a import RELATIONS, FUNCTIONS
    need({r["name"] for r in value["relations"]} == set(RELATIONS)
         and {r["name"] for r in value["functions"]} == set(FUNCTIONS))
    return raw

def capture(directory_fd, raw, expected_runtime_id):
    """Caller supplies an existing private directory FD; never guesses/creates a host path."""
    descriptor = None
    name = None
    committed = False
    created = False
    identity = None
    try:
        need(sys.platform == 'linux')
        validate(raw, expected_runtime_id)
        st = os.fstat(directory_fd)
        need(stat.S_ISDIR(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o700
             and st.st_uid == os.getuid())
        digest = hashlib.sha256(raw).hexdigest()
        name = digest + ".json"
        descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory_fd)
        created = True
        st = os.fstat(descriptor)
        need(stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o600 and st.st_nlink == 1
             and st.st_uid == os.getuid())
        identity = (st.st_dev, st.st_ino)
        offset = 0
        while offset < len(raw):
            count = os.write(descriptor, raw[offset:])
            need(count > 0)
            offset += count
        os.fsync(descriptor)
        os.fsync(directory_fd)
        final = os.fstat(descriptor)
        named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        need(stat.S_ISREG(final.st_mode) and stat.S_IMODE(final.st_mode) == 0o600
             and final.st_nlink == 1 and final.st_uid == os.getuid()
             and final.st_size == len(raw) and (final.st_dev, final.st_ino) == identity
             and stat.S_ISREG(named.st_mode) and (named.st_dev, named.st_ino) == identity)
        os.lseek(descriptor, 0, os.SEEK_SET)
        reread = bytearray()
        while len(reread) <= MAX_OUTPUT:
            part = os.read(descriptor, min(4096, MAX_OUTPUT + 1 - len(reread)))
            if not part:
                break
            reread.extend(part)
        need(bytes(reread) == raw)
        stable = os.fstat(descriptor)
        named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        need((stable.st_dev, stable.st_ino, stable.st_size, stable.st_mtime_ns, stable.st_ctime_ns)
             == (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns, final.st_ctime_ns)
             and stat.S_ISREG(named.st_mode) and (named.st_dev, named.st_ino) == identity)
        os.close(descriptor)
        descriptor = None
        committed = True
        return {"status": "PRIVATE_STAGE_A_CAPTURED", "sha256": digest, "bytes": len(raw),
                "production_mutations": False, "stage_b_allowed": False}
    except BaseException:
        raise Refused("PRIVATE_CAPTURE_REFUSED") from None
    finally:
        try:
            if descriptor is not None:
                os.close(descriptor)
            if name is not None and not committed and created:
                current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                need(identity is not None and stat.S_ISREG(current.st_mode)
                     and (current.st_dev, current.st_ino) == identity)
                os.unlink(name, dir_fd=directory_fd)
                os.fsync(directory_fd)
        except BaseException:
            raise Refused("PRIVATE_CAPTURE_CLEANUP_REFUSED") from None

def public_receipt(receipt):
    need(type(receipt) is dict and set(receipt) ==
         {"status", "sha256", "bytes", "production_mutations", "stage_b_allowed"})
    need(receipt["status"] == "PRIVATE_STAGE_A_CAPTURED" and
         re.fullmatch("[0-9a-f]{64}", receipt["sha256"]) and
         type(receipt["bytes"]) is int and 0 < receipt["bytes"] <= MAX_OUTPUT and
         receipt["production_mutations"] is False and receipt["stage_b_allowed"] is False)
    return json.dumps(receipt, sort_keys=True, separators=(",", ":"))
