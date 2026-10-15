"""Offline revision compatibility with real bundles, generators and validators.

Only transport and OS-specific filesystem boundaries are substituted. No private
snapshot, SSH key, provider, archive or service is consulted by these tests.
"""
import base64
import contextlib
import copy
from dataclasses import FrozenInstanceError, is_dataclass
import hashlib
import io
import json
from pathlib import Path
import shlex
import sys
import tempfile
import types
import unittest
from unittest import mock
import zlib

from ops import ibm_school_compute_receipt as e
from ops import ibm_school_worlds_probe as p
from ops import ibm_worlds_validator as v
from tests.test_ibm_school_worlds_probe import B, chain, encoded, receipt


ROOT = Path(__file__).resolve().parents[1]
SCHOOL_ACK = "OWNER_APPROVED_IBM_SCHOOL_WORLDS_10MIN_10USD"


def _controller_module():
    # Only import-time USER_AGENT labels are substituted. Windows uname may
    # otherwise spawn a shell; this test does not evaluate that diagnostic text.
    with mock.patch("platform.system", return_value=sys.platform), \
         mock.patch("platform.machine", return_value="offline-test"):
        from ops import ibm_trial_executor
    return ibm_trial_executor


def _unpack(record):
    name, size, sha, compressed_size, compressed_sha, encoded = record
    compressed = base64.b64decode(encoded, validate=True)
    assert len(compressed) == compressed_size
    assert hashlib.sha256(compressed).hexdigest() == compressed_sha
    source = zlib.decompress(compressed)
    assert len(source) == size and hashlib.sha256(source).hexdigest() == sha
    return name, source


def _restored_bundle(root, revision):
    spec = p.bundle_revision(revision)
    for record in spec.source_records:
        name, source = _unpack(record)
        path = root / spec.module_paths[name]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source)
    return root


def _packed_generator(revision):
    record = next(row for row in p.bundle_revision(revision).source_records
                  if row[0] == "ibm_worlds_generator")
    _, source = _unpack(record)
    module = types.ModuleType("revision_test_generator")
    # dataclasses require a registered module during execution. The actual host
    # bundle is never executed, so this cannot reach service or archive code.
    with mock.patch.dict(sys.modules, {module.__name__: module}):
        exec(compile(source, "reviewed-packed-generator", "exec"), module.__dict__)
    return module


def _revision_receipt(revision):
    """Synthetic successful guest receipt; no actual IBM execution is implied."""
    value = receipt()
    value["generator_sha256"] = p.bundle_revision(revision).source_pins["ibm_worlds_generator"]
    return value


def _files(revision, result):
    row = _revision_receipt(revision)
    checked = v.validate_result(result, expected_result_sha256=v.EXPECTED_RESULT_SHA256)
    files = {
        "receipt.json": v.canonical_json_bytes(p.safe_receipt(row)),
        "input.json": v.INPUT_BYTES,
        "worlds.json": v.canonical_json_bytes(result),
        "worlds.pbn": v.render_pbn(result),
    }
    manifest = p.export_manifest(row, B, files, checked, revision=revision)
    files["manifest.json"] = v.canonical_json_bytes(manifest)
    return row, files, manifest


def _lifecycle_rows(row, directory, binding=None):
    """Synthesized provider observations test parser behavior, not live readiness."""
    binding = B if binding is None else binding
    at = {"at_utc": "2026-10-13T07:00:00.000001+00:00"}
    return [
        dict(event="SCHOOL_WORLDS_ROUTE_PREPARED", **binding, **at, guest_network_requests=0),
        dict(event="START_SUBMITTING", mode="trial", max_window_seconds=600, running_window_seconds=None),
        dict(event="START_ACCEPTED"), dict(event="TRIAL_STATE", status="running"),
        dict(event="SCHOOL_WORLDS_STARTED", **binding, max_seconds=378, seconds_since_start=5.1,
             stop_submit_by_seconds=420, confirmation_reserve_seconds=180),
        dict(event="SCHOOL_WORLDS_CAPTURED", **binding, **at, remote_exit=0, stdout_bytes=24000,
             stdout_sha256="a" * 64, stderr_bytes=0, stderr_sha256=e.EMPTY_SHA, raw_output_exported=False),
        dict(event="SCHOOL_WORLDS_VERIFIED_RECEIPT", **binding, **at,
             receipt=p.safe_receipt(row), artifact_path=str(directory)),
        dict(event="SCHOOL_WORLDS_RESULT", generated=True),
        dict(event="TRIAL_CONTAINMENT", reason="school_worlds_complete"),
        dict(event="STOP_STATE", status="running", startable=False, action_queue=e.QUEUE),
        dict(event="ORDINARY_STOP_SUBMITTING"), dict(event="ORDINARY_STOP_ACCEPTED"),
        *[dict(event="STOP_STATE", status="stopped", startable=True, action_queue=e.QUEUE) for _ in range(3)],
        dict(event="STOPPED_OBSERVED", action_queue=e.QUEUE, stop_post_unknown=False),
    ]


class BundleRevisionTests(unittest.TestCase):
    def setUp(self):
        self.guard = contextlib.ExitStack()
        self.addCleanup(self.guard.close)
        for target in ("socket.socket", "subprocess.Popen", "subprocess.run", "subprocess.check_output"):
            self.guard.enter_context(mock.patch(target, side_effect=AssertionError("LIVE_TRANSPORT_FORBIDDEN")))

    def test_legacy_defaults_are_frozen_and_revision_views_are_immutable(self):
        legacy = p.bundle_revision()
        current = p.bundle_revision(p.CONSTRAINT_KEYS_REVISION)
        self.assertEqual("legacy-v1", legacy.name)
        self.assertEqual("normalized-constraints-v2", current.name)
        self.assertEqual(p.SOURCE_PINS, dict(legacy.source_pins))
        self.assertEqual(p.SOURCE_RECORDS, legacy.source_records)
        self.assertEqual(p.MODULE_PATHS, dict(legacy.module_paths))
        self.assertEqual(p.HOST_SOURCE_SHA, legacy.host_source_sha)
        for spec in (legacy, current):
            self.assertTrue(is_dataclass(spec))
            self.assertIsInstance(spec.source_records, tuple)
            self.assertTrue(all(type(row) is tuple for row in spec.source_records))
            with self.assertRaises(FrozenInstanceError):
                spec.name = "changed"
            for mapping in (spec.source_pins, spec.module_paths):
                with self.assertRaises(TypeError):
                    mapping["ibm_worlds_generator"] = "changed"

    def test_only_generator_and_host_records_change(self):
        old = p.bundle_revision()
        new = p.bundle_revision(p.CONSTRAINT_KEYS_REVISION)
        self.assertEqual(set(old.source_pins), set(new.source_pins))
        changed_pins = {name for name in old.source_pins if old.source_pins[name] != new.source_pins[name]}
        self.assertEqual({"ibm_worlds_generator", "ibm_school_worlds"}, changed_pins)
        old_records = {row[0]: row for row in old.source_records}
        new_records = {row[0]: row for row in new.source_records}
        self.assertEqual(changed_pins, {name for name in old_records if old_records[name] != new_records[name]})
        self.assertEqual(dict(p.MODULE_PATHS, ibm_school_worlds="ops/ibm_school_worlds_v2.py"), dict(new.module_paths))
        old_host = _unpack(old_records["ibm_school_worlds"])[1]
        new_host = _unpack(new_records["ibm_school_worlds"])[1]
        self.assertEqual(old_host.replace(old.source_pins["ibm_worlds_generator"].encode(),
                                         new.source_pins["ibm_worlds_generator"].encode()), new_host)
        self.assertEqual(v.GENERATOR_SOURCE_SHA256, old.source_pins["ibm_worlds_generator"])

    def test_real_v2_builder_uses_exact_checkout_and_never_recompresses(self):
        revision = p.CONSTRAINT_KEYS_REVISION
        spec = p.bundle_revision(revision)
        with mock.patch.object(p.zlib, "compress", side_effect=AssertionError("RUNTIME_RECOMPRESSION_FORBIDDEN")):
            bundle = p.bundled_host_source(root=ROOT, revision=revision)
        self.assertEqual(spec.host_source_sha, hashlib.sha256(bundle.encode()).hexdigest())
        for record in spec.source_records:
            name, source = _unpack(record)
            self.assertEqual(source, (ROOT / spec.module_paths[name]).read_bytes())
        compile(bundle, "reviewed-v2-bundle", "exec")

    def test_legacy_builder_works_with_real_restored_legacy_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = _restored_bundle(Path(temporary), p.LEGACY_BUNDLE_REVISION)
            default = p.bundled_host_source(root=root)
            explicit = p.bundled_host_source(root=root, revision=p.LEGACY_BUNDLE_REVISION)
        self.assertEqual(default, explicit)
        self.assertEqual(p.HOST_SOURCE_SHA, hashlib.sha256(default.encode()).hexdigest())
        with self.assertRaisesRegex(p.WorldsError, "^source_pin$"):
            p.bundled_host_source(root=ROOT)

    def test_source_drift_and_mixed_host_fail_builder(self):
        revision = p.CONSTRAINT_KEYS_REVISION
        for name in ("ibm_worlds_generator", "ibm_school_worlds", "ibm_worlds_validator", "ibm_unit_isolation"):
            with self.subTest(module=name), tempfile.TemporaryDirectory() as temporary:
                root = _restored_bundle(Path(temporary), revision)
                path = root / p.bundle_revision(revision).module_paths[name]
                path.write_bytes(path.read_bytes() + b"\n# drift\n")
                with self.assertRaisesRegex(p.WorldsError, "^source_pin$"):
                    p.bundled_host_source(root=root, revision=revision)
        with tempfile.TemporaryDirectory() as temporary:
            root = _restored_bundle(Path(temporary), revision)
            old_host = next(row for row in p.SOURCE_RECORDS if row[0] == "ibm_school_worlds")
            (root / "ops/ibm_school_worlds_v2.py").write_bytes(_unpack(old_host)[1])
            with self.assertRaisesRegex(p.WorldsError, "^source_pin$"):
                p.bundled_host_source(root=root, revision=revision)

    def test_unknown_revision_fails_before_key_pop_or_transport(self):
        for revision in ("unknown", "", "LEGACY-V1", None, True, 2):
            environment = {"ORACLE_SSH_PRIVATE_KEY": "INERT_TEST_SENTINEL"}
            with self.subTest(revision=revision), mock.patch.object(p, "context", return_value=dict(B)), \
                 mock.patch.object(p.os, "environ", environment), \
                 mock.patch.object(p, "bounded_process") as transport, \
                 mock.patch.object(p.read_only, "memory") as memory:
                with self.assertRaisesRegex(p.WorldsError, "^bundle_revision$"):
                    p.prepare(revision=revision)
                self.assertEqual({"ORACLE_SSH_PRIVATE_KEY": "INERT_TEST_SENTINEL"}, environment)
                memory.assert_not_called()
                transport.assert_not_called()

    def test_source_drift_fails_before_key_pop_or_transport(self):
        original = Path.read_bytes
        def drift(path):
            raw = original(path)
            return raw + b"\n# drift\n" if path.name == "ai_worlds.py" else raw
        environment = {"ORACLE_SSH_PRIVATE_KEY": "INERT_TEST_SENTINEL"}
        with mock.patch.object(p, "context", return_value=dict(B)), mock.patch.object(Path, "read_bytes", drift), \
             mock.patch.object(p.os, "environ", environment), \
             mock.patch.object(p, "bounded_process") as transport, mock.patch.object(p.read_only, "memory") as memory:
            with self.assertRaisesRegex(p.WorldsError, "^source_pin$"):
                p.prepare(revision=p.CONSTRAINT_KEYS_REVISION)
            self.assertEqual({"ORACLE_SSH_PRIVATE_KEY": "INERT_TEST_SENTINEL"}, environment)
            memory.assert_not_called()
            transport.assert_not_called()

    def test_v2_prepare_passes_real_source_gate_and_stops_at_missing_key(self):
        with mock.patch.object(p, "context", return_value=dict(B)), mock.patch.object(p.os, "environ", {}), \
             mock.patch.object(p, "bounded_process") as transport, mock.patch.object(p.read_only, "memory") as memory:
            with self.assertRaisesRegex(p.WorldsError, "^existing_owner_key_missing$"):
                p.prepare(revision=p.CONSTRAINT_KEYS_REVISION)
            memory.assert_not_called()
            transport.assert_not_called()

    def test_tampered_public_manifest_fails_before_key_pop_or_transport(self):
        original = Path.open
        manifest = ROOT / "ops/ibm_school_worlds_bundle_v2.json"
        raw = manifest.read_bytes()
        def changed(path, *args, **kwargs):
            if path.name == manifest.name:
                return io.BytesIO(raw + b" ")
            return original(path, *args, **kwargs)
        environment = {"ORACLE_SSH_PRIVATE_KEY": "INERT_TEST_SENTINEL"}
        with mock.patch.object(p, "context", return_value=dict(B)), mock.patch.object(Path, "open", changed), \
             mock.patch.object(p.os, "environ", environment), \
             mock.patch.object(p, "bounded_process") as transport, mock.patch.object(p.read_only, "memory") as memory:
            with self.assertRaisesRegex(p.WorldsError, "^bundle_manifest_pin$"):
                p.prepare(revision=p.CONSTRAINT_KEYS_REVISION)
            self.assertEqual({"ORACLE_SSH_PRIVATE_KEY": "INERT_TEST_SENTINEL"}, environment)
            memory.assert_not_called()
            transport.assert_not_called()

    def test_packed_v2_generator_matches_golden_and_rejects_duplicate_keys(self):
        module = _packed_generator(p.CONSTRAINT_KEYS_REVISION)
        result = module.generate_worlds(**copy.deepcopy(v.INPUT))
        self.assertEqual(v.EXPECTED_RESULT_SHA256, hashlib.sha256(v.canonical_json_bytes(result)).hexdigest())
        self.assertTrue(v.validate_result(result, expected_result_sha256=v.EXPECTED_RESULT_SHA256)["valid"])
        for seat_items in (("S", "s"), ("s", "S")):
            args = copy.deepcopy(v.INPUT)
            args["constraints"] = {"seats": {seat: {"hcp": [0, 37]} for seat in seat_items}}
            with self.subTest(seats=seat_items), self.assertRaisesRegex(module.WorldGenerationError, "duplicate constraint seat: S"):
                module.generate_worlds(**args)
        for suit_items in (("H", "h"), ("h", "H")):
            args = copy.deepcopy(v.INPUT)
            args["constraints"] = {"seats": {"S": {"suits": {suit: [0, 13] for suit in suit_items}}}}
            with self.subTest(suits=suit_items), self.assertRaisesRegex(module.WorldGenerationError, "duplicate constraint suit: S.H"):
                module.generate_worlds(**args)

    def test_prepared_transport_binds_selected_host_and_remote_hash(self):
        for revision in (p.LEGACY_BUNDLE_REVISION, p.CONSTRAINT_KEYS_REVISION):
            with self.subTest(revision=revision), tempfile.TemporaryDirectory() as temporary:
                root = _restored_bundle(Path(temporary), revision)
                bundle = p.bundled_host_source(root=root, revision=revision)
                spec = p.bundle_revision(revision)
                prepared = p.PreparedWorlds(dict(B), (ROOT / "ops/ibm_ssh_probe_safe.py").read_text(),
                                            bundle, 71, 72, revision=revision)
                prepared.bind_identity(dict(p.IDENTITY))
                with mock.patch.object(p, "bounded_process", return_value=(0, b"", b"")) as process:
                    prepared.call("generate_worlds", 373)
                payload = process.call_args.kwargs["payload"]
                self.assertLessEqual(len(payload), p.INPUT_BYTES)
                document = json.loads(payload)
                self.assertEqual(spec.host_source_sha, document["host_sha"])
                self.assertEqual(bundle, document["host_source"])
                self.assertEqual(spec.host_source_sha, hashlib.sha256(document["host_source"].encode()).hexdigest())
                remote = shlex.split(process.call_args.args[0][-1])[-1]
                self.assertEqual(p.remote_source(revision=revision), remote)
                self.assertIn(spec.host_source_sha, remote)
                self.assertNotIn("@HOST_SHA@", remote)
                compile(remote, "reviewed-remote-source", "exec")

    def controller_source_gate(self, options):
        controller = _controller_module()
        observed = []
        actual_prepare = p.prepare
        def traced_prepare(**kwargs):
            # Observe the exception without replacing any admission logic.
            try:
                return actual_prepare(**kwargs)
            except p.WorldsError as error:
                observed.append((kwargs["revision"], str(error)))
                raise
        class EmptyCredentials(dict):
            def pop(self, key, default=None):
                reads.append(key)
                return super().pop(key, default)
        reads = []
        # This context substitution tests CLI routing, not wall-clock timing.
        with mock.patch.object(controller, "wall_deadline", return_value=contextlib.nullcontext()), \
             mock.patch.object(p, "prepare", side_effect=traced_prepare), \
             mock.patch.object(p, "context", return_value=dict(B)), \
             mock.patch.object(p.os, "environ", EmptyCredentials()), \
             mock.patch.object(p.read_only, "memory") as memory, \
             mock.patch.object(p, "bounded_process") as transport, \
             mock.patch.object(controller, "authenticate") as authenticate, \
             mock.patch.object(controller, "trial") as trial, contextlib.redirect_stdout(io.StringIO()):
            code = controller.main(["school_worlds", "--ack", SCHOOL_ACK, *options])
        self.assertEqual(4, code)
        self.assertEqual(1, len(observed))
        memory.assert_not_called()
        transport.assert_not_called()
        authenticate.assert_not_called()
        trial.assert_not_called()
        return observed[0], reads

    def test_controller_default_legacy_and_explicit_revisions_use_real_source_gate(self):
        for options, revision, reason, reads in (
            ([], p.LEGACY_BUNDLE_REVISION, "source_pin", []),
            (["--revision", p.LEGACY_BUNDLE_REVISION], p.LEGACY_BUNDLE_REVISION, "source_pin", []),
            (["--revision", p.CONSTRAINT_KEYS_REVISION], p.CONSTRAINT_KEYS_REVISION,
             "existing_owner_key_missing", ["ORACLE_SSH_PRIVATE_KEY"]),
        ):
            with self.subTest(options=options):
                self.assertEqual(((revision, reason), reads), self.controller_source_gate(options))

    def test_controller_rejects_unknown_revision_before_context_or_iam(self):
        controller = _controller_module()
        with mock.patch.object(p, "context") as context, \
             mock.patch.object(controller, "authenticate") as authenticate, \
             mock.patch.object(controller, "trial") as trial, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as failure:
                controller.main(["school_worlds", "--ack", SCHOOL_ACK, "--revision", "future-v3"])
        self.assertEqual(2, failure.exception.code)
        context.assert_not_called()
        authenticate.assert_not_called()
        trial.assert_not_called()

    def test_controller_rejects_revision_for_other_modes_before_preparation_or_iam(self):
        controller = _controller_module()
        modes = {"trial": "OWNER_APPROVED_10MIN_10USD",
                 "manual_console_trial": "OWNER_APPROVED_MANUAL_CONSOLE_10MIN_10USD",
                 "stop": "ORDINARY_STOP_EXACT_IBM",
                 "isolate_units": "OWNER_APPROVED_IBM_FOUR_UNIT_ISOLATION_10MIN_10USD"}
        for mode, ack in modes.items():
            for revision in (p.LEGACY_BUNDLE_REVISION, p.CONSTRAINT_KEYS_REVISION):
                with self.subTest(mode=mode, revision=revision), mock.patch.object(p, "context") as context, \
                     mock.patch.object(p.isolation, "prepare") as prepare_isolation, \
                     mock.patch.object(p.read_only, "prepare") as prepare_guest, \
                     mock.patch.object(controller, "authenticate") as authenticate, \
                     mock.patch.object(controller, "trial") as trial, \
                     mock.patch.object(controller, "ordinary_stop") as stop, \
                     contextlib.redirect_stdout(io.StringIO()) as output:
                    code = controller.main([mode, "--ack", ack, "--revision", revision])
                self.assertEqual(3, code)
                self.assertEqual("revision_mode_invalid", json.loads(output.getvalue())["reason"])
                context.assert_not_called()
                prepare_isolation.assert_not_called()
                prepare_guest.assert_not_called()
                authenticate.assert_not_called()
                trial.assert_not_called()
                stop.assert_not_called()

    def test_controller_v2_source_and_manifest_drift_precede_key_iam_and_start(self):
        original_read = Path.read_bytes
        def changed_source(path):
            raw = original_read(path)
            return raw + b"\n# drift\n" if path.name == "ai_worlds.py" else raw
        original_open = Path.open
        raw_manifest = (ROOT / "ops/ibm_school_worlds_bundle_v2.json").read_bytes()
        def changed_manifest(path, *args, **kwargs):
            if path.name == "ibm_school_worlds_bundle_v2.json":
                return io.BytesIO(raw_manifest + b" ")
            return original_open(path, *args, **kwargs)
        for method, changed, reason in (("read_bytes", changed_source, "source_pin"),
                                        ("open", changed_manifest, "bundle_manifest_pin")):
            with self.subTest(reason=reason), mock.patch.object(Path, method, changed):
                observed = self.controller_source_gate(["--revision", p.CONSTRAINT_KEYS_REVISION])
            self.assertEqual(((p.CONSTRAINT_KEYS_REVISION, reason), []), observed)


class RevisionReceiptAndLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = {revision: _packed_generator(revision).generate_worlds(**copy.deepcopy(v.INPUT))
                       for revision in (p.LEGACY_BUNDLE_REVISION, p.CONSTRAINT_KEYS_REVISION)}

    def memory_files(self, files, directory):
        stack = contextlib.ExitStack()
        def read(path, limit=e.MAX_FILE):
            self.assertEqual(directory, path.parent)
            raw = files[path.name]
            self.assertLessEqual(len(raw), limit)
            return raw
        stack.enter_context(mock.patch.object(e, "read_file", side_effect=read))
        stack.enter_context(mock.patch.object(e, "owned_directory"))
        return stack

    def receipt_cli(self, row, files, options):
        """Exercise real CLI/validators; model only filesystem bytes in memory.

        Successful output here is a test artifact. It proves no native file
        ownership, permissions, provider state or actual IBM containment.
        """
        parent = ROOT / "in-memory-runner"
        directory = parent / "ibm-school-worlds"
        control = parent / "ibm-school-compute-control"
        destination = parent / "ibm-school-compute-artifact"
        log = b"".join(v.canonical_json_bytes(item) for item in _lifecycle_rows(row, directory))
        inputs = {directory / name: raw for name, raw in files.items()}
        inputs[control / "controller.jsonl"] = log
        outputs, descriptors = {}, {}

        def read(path, limit=e.MAX_FILE):
            self.assertIn(path, inputs)
            raw = inputs[path]
            self.assertLessEqual(len(raw), limit)
            return raw

        def owned(path):
            self.assertIn(path, (parent, control, directory))

        def open_output(path, flags, mode):
            self.assertEqual(destination, path.parent)
            self.assertEqual(0o600, mode)
            self.assertTrue(flags & e.os.O_WRONLY and flags & e.os.O_CREAT and flags & e.os.O_EXCL)
            descriptor = len(descriptors) + 100
            descriptors[descriptor] = path.name
            return descriptor

        class MemoryOutput(io.BytesIO):
            def __init__(self, descriptor):
                super().__init__()
                self.descriptor = descriptor
            def fileno(self):
                return self.descriptor
            def close(self):
                if not self.closed:
                    outputs[descriptors[self.descriptor]] = self.getvalue()
                super().close()

        def fdopen(descriptor, mode):
            self.assertEqual("wb", mode)
            self.assertIn(descriptor, descriptors)
            return MemoryOutput(descriptor)

        # O_NOFOLLOW is inert because open_output never touches the filesystem.
        # Native behavior remains covered by the separately skipped Linux test.
        with mock.patch.object(p, "context", return_value=dict(B)), \
             mock.patch.object(e.os, "environ", {"RUNNER_TEMP": str(parent)}), \
             mock.patch.object(e, "owned_directory", side_effect=owned), \
             mock.patch.object(e, "read_file", side_effect=read), \
             mock.patch.object(e.os, "mkdir") as mkdir, \
             mock.patch.object(e.os, "O_NOFOLLOW", getattr(e.os, "O_NOFOLLOW", 0), create=True), \
             mock.patch.object(e.os, "open", side_effect=open_output), \
             mock.patch.object(e.os, "fdopen", side_effect=fdopen), \
             mock.patch.object(e.os, "fsync"), contextlib.redirect_stdout(io.StringIO()) as output:
            code = e.main(["--controller-exit", "0", "--capture-exit", "0", *options])
        mkdir.assert_called_once_with(destination, 0o700)
        self.assertIn("lifecycle.json", outputs)
        lifecycle = json.loads(outputs["lifecycle.json"])
        self.assertIn("IBM_SCHOOL_COMPUTE_LIFECYCLE=" + lifecycle["result"], output.getvalue())
        return code, lifecycle, outputs

    def test_receipt_cli_default_and_explicit_revisions_validate_real_artifacts(self):
        for actual, result in self.results.items():
            row, files, _ = _files(actual, result)
            for options, requested in (([], p.LEGACY_BUNDLE_REVISION),
                                       (["--revision", p.LEGACY_BUNDLE_REVISION], p.LEGACY_BUNDLE_REVISION),
                                       (["--revision", p.CONSTRAINT_KEYS_REVISION], p.CONSTRAINT_KEYS_REVISION)):
                with self.subTest(actual=actual, options=options):
                    code, lifecycle, output = self.receipt_cli(row, files, options)
                    if actual == requested:
                        self.assertEqual(0, code)
                        self.assertEqual(("COMPUTE_CONTAINED", "OK"), (lifecycle["result"], lifecycle["reason"]))
                        self.assertEqual(set(files) | {"lifecycle.json"}, set(output))
                        for name, raw in files.items():
                            self.assertEqual(raw, output[name])
                    else:
                        self.assertEqual(3, code)
                        self.assertEqual(("INCOMPLETE", "EVIDENCE_INVALID"), (lifecycle["result"], lifecycle["reason"]))
                        self.assertEqual({"lifecycle.json"}, set(output))
                        self.assertEqual({}, lifecycle["files"])
                        self.assertIsNone(lifecycle["provider_stop"])

    def test_receipt_cli_rejects_unknown_revision_before_context_or_files(self):
        with mock.patch.object(p, "context") as context, \
             mock.patch.object(e, "read_file") as read, \
             mock.patch.object(e.os, "mkdir") as mkdir, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as failure:
                e.main(["--revision", "future-v3"])
        self.assertEqual(2, failure.exception.code)
        context.assert_not_called()
        read.assert_not_called()
        mkdir.assert_not_called()

    def test_receipt_cli_rejects_mixed_receipt_and_export_manifest(self):
        old, new = p.LEGACY_BUNDLE_REVISION, p.CONSTRAINT_KEYS_REVISION
        row, files, _ = _files(old, self.results[old])
        _, other_files, _ = _files(new, self.results[new])
        mixed = dict(files, **{"manifest.json": other_files["manifest.json"]})
        for options in ([], ["--revision", old], ["--revision", new]):
            with self.subTest(options=options):
                code, lifecycle, output = self.receipt_cli(row, mixed, options)
                self.assertEqual(3, code)
                self.assertEqual(("INCOMPLETE", "EVIDENCE_INVALID"), (lifecycle["result"], lifecycle["reason"]))
                self.assertEqual({"lifecycle.json"}, set(output))
                self.assertEqual({}, lifecycle["files"])

    def test_receipts_accept_selected_revision_and_cross_reject(self):
        old, new = p.LEGACY_BUNDLE_REVISION, p.CONSTRAINT_KEYS_REVISION
        for actual, requested in ((old, new), (new, old)):
            row = _revision_receipt(actual)
            self.assertEqual(row, p.validate_receipt(row, B, revision=actual))
            with self.assertRaisesRegex(p.WorldsError, "^receipt_source_binding$"):
                p.validate_receipt(row, B, revision=requested)
        self.assertEqual(receipt(), p.validate_receipt(receipt(), B))

    def test_run_once_validates_v2_and_forwards_revision_to_persistence(self):
        revision = p.CONSTRAINT_KEYS_REVISION
        result = self.results[revision]
        worlds = v.canonical_json_bytes(result)
        checked = v.validate_result(result, expected_result_sha256=v.EXPECTED_RESULT_SHA256)
        row = _revision_receipt(revision)
        row["artifact"] = dict(name="worlds.json", bytes=len(worlds), sha256=v.EXPECTED_RESULT_SHA256,
                               encoding="zlib+base64", data=base64.b64encode(zlib.compress(worlds, 9)).decode())
        capture = encoded(chain(row))
        destination = str(ROOT / "in-memory-worlds")
        for selected in (revision, p.LEGACY_BUNDLE_REVISION):
            prepared = p.PreparedWorlds(dict(B), "synthetic safe source", "synthetic host source", 71, 72,
                                        revision=selected)
            prepared.ready = True
            prepared.bind_identity(dict(p.IDENTITY))
            # Captured transport and persistence are boundaries; receipt,
            # artifact decompression and independent validators remain real.
            prepared.call = mock.Mock(return_value=(0, capture, b""))
            with self.subTest(revision=selected), \
                 mock.patch.object(p, "persist_receipt", return_value=destination) as persist, \
                 contextlib.redirect_stdout(io.StringIO()):
                if selected == revision:
                    self.assertIs(True, prepared.run_once())
                    persist.assert_called_once_with(row, B, worlds=worlds, result=result,
                                                    independent=checked, revision=revision)
                    self.assertEqual(destination, prepared.output_path)
                else:
                    with self.assertRaisesRegex(p.WorldsError, "^receipt_source_binding$"):
                        prepared.run_once()
                    persist.assert_not_called()
                    self.assertIsNone(prepared.output_path)
                prepared.call.assert_called_once_with("generate_worlds", p.MAINTENANCE_SECONDS - 5)

    def test_export_and_full_lifecycle_validate_real_worlds_in_both_revisions(self):
        directory = ROOT / "in-memory-worlds"
        for revision, result in self.results.items():
            with self.subTest(revision=revision):
                row, files, manifest = _files(revision, result)
                spec = p.bundle_revision(revision)
                self.assertEqual("bridge.ibm.school-worlds.export.v1", manifest["schema"])
                self.assertEqual(dict(spec.source_pins), manifest["reviewed_sources"])
                self.assertEqual(spec.host_source_sha, manifest["host_bundle_sha256"])
                self.assertEqual(spec.source_pins["ibm_worlds_generator"], manifest["generator_sha256"])
                raw = b"".join(v.canonical_json_bytes(item) for item in _lifecycle_rows(row, directory))
                with self.memory_files(files, directory):
                    checked = e.validate_artifacts(directory, B, p.safe_receipt(row), revision=revision)
                    validated = e.validate_lifecycle(raw, B, directory, revision=revision)
                    lifecycle = e.build_receipt(raw, B, directory, 0, 0, revision=revision)
                self.assertEqual(set(files), set(checked))
                self.assertEqual(checked, validated["files"])
                self.assertEqual("COMPUTE_CONTAINED", lifecycle["result"])
                self.assertEqual("OK", lifecycle["reason"])
                self.assertEqual(checked, lifecycle["files"])
                self.assertEqual(3, lifecycle["provider_stop"]["consecutive_stopped_startable"])

    def test_export_and_lifecycle_defaults_remain_legacy(self):
        revision = p.LEGACY_BUNDLE_REVISION
        directory = ROOT / "in-memory-worlds"
        row, files, expected = _files(revision, self.results[revision])
        payload = {name: raw for name, raw in files.items() if name != "manifest.json"}
        checked = v.validate_result(self.results[revision], expected_result_sha256=v.EXPECTED_RESULT_SHA256)
        self.assertEqual(expected, p.export_manifest(row, B, payload, checked))
        raw = b"".join(v.canonical_json_bytes(item) for item in _lifecycle_rows(row, directory))
        with self.memory_files(files, directory):
            artifacts = e.validate_artifacts(directory, B, p.safe_receipt(row))
            validated = e.validate_lifecycle(raw, B, directory)
            lifecycle = e.build_receipt(raw, B, directory, 0, 0)
        self.assertEqual(artifacts, validated["files"])
        self.assertEqual(artifacts, lifecycle["files"])
        self.assertEqual("COMPUTE_CONTAINED", lifecycle["result"])

    def test_export_manifest_tampering_cannot_pass_real_artifact_validator(self):
        revision = p.CONSTRAINT_KEYS_REVISION
        directory = ROOT / "in-memory-worlds"
        row, files, manifest = _files(revision, self.results[revision])
        mutations = [dict(generator_sha256=p.SOURCE_PINS["ibm_worlds_generator"]),
                     dict(host_bundle_sha256=p.HOST_SOURCE_SHA), dict(reviewed_sources=p.SOURCE_PINS),
                     dict(source_commit="b" * 40), dict(attempt=True), dict(files={}),
                     dict(service_changes=["restart"]), dict(extra="unapproved")]
        for edit in mutations:
            modified = dict(files, **{"manifest.json": v.canonical_json_bytes(dict(manifest, **edit))})
            with self.subTest(edit=edit), self.memory_files(modified, directory):
                with self.assertRaisesRegex(e.EvidenceError, "^EVIDENCE_INVALID$"):
                    e.validate_artifacts(directory, B, p.safe_receipt(row), revision=revision)
        modified = dict(files, **{"manifest.json": files["manifest.json"] + b" "})
        with self.memory_files(modified, directory), self.assertRaisesRegex(e.EvidenceError, "^EVIDENCE_INVALID$"):
            e.validate_artifacts(directory, B, p.safe_receipt(row), revision=revision)

    def test_mixed_revision_lifecycle_is_incomplete_without_files(self):
        directory = ROOT / "in-memory-worlds"
        for actual, requested in ((p.LEGACY_BUNDLE_REVISION, p.CONSTRAINT_KEYS_REVISION),
                                  (p.CONSTRAINT_KEYS_REVISION, p.LEGACY_BUNDLE_REVISION)):
            row, files, _ = _files(actual, self.results[actual])
            raw = b"".join(v.canonical_json_bytes(item) for item in _lifecycle_rows(row, directory))
            with self.subTest(actual=actual), self.memory_files(files, directory):
                lifecycle = e.build_receipt(raw, B, directory, 0, 0, revision=requested)
            self.assertEqual("INCOMPLETE", lifecycle["result"])
            self.assertEqual("EVIDENCE_INVALID", lifecycle["reason"])
            self.assertEqual({}, lifecycle["files"])
            self.assertIsNone(lifecycle["provider_stop"])

    @unittest.skipUnless(sys.platform == "linux", "native owner/mode filesystem checks require Linux")
    def test_native_v2_host_export_and_lifecycle_with_synthetic_archive(self):
        from ops import ibm_school_worlds_v2 as host
        from tests import test_ibm_school_worlds as host_tests
        revision = p.CONSTRAINT_KEYS_REVISION
        spec = p.bundle_revision(revision)
        # The existing harness creates its own historical archive and supplies
        # fake read-only systemd observations. The V2 host, generator, source
        # identity, archive checks and persistence below are real code paths.
        with mock.patch.object(host_tests, "worlds", host):
            harness = host_tests.FixedSchoolWorldsTests(methodName="runTest")
            self.addCleanup(harness.doCleanups)
            harness.setUp()
            row = harness.execute()
            harness.assert_immutable_evidence()
            self.assertEqual(("WORLD_GENERATED", "OK", "COMPLETE"),
                             (row["result"], row["reason"], row["stage"]), row)
            self.assertEqual(spec.source_pins["ibm_worlds_generator"], row["generator_sha256"])
            guest_manifest = json.loads((harness.scope_path / "manifest.json").read_bytes())
            self.assertEqual(spec.source_pins["ibm_worlds_generator"], guest_manifest["generator_sha256"])
            binding = {key: row[key] for key in B}
            with mock.patch.object(p, "RESUME_SNAPSHOT_SHA256", harness.fixture_sha):
                p.validate_receipt(row, binding, revision=revision)
                worlds, result, checked = p.artifact_result(row["artifact"])
                self.assertEqual(worlds, (harness.scope_path / "worlds.json").read_bytes())
                with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(p.os.environ, RUNNER_TEMP=temporary):
                    directory = Path(p.persist_receipt(row, binding, worlds=worlds,
                                                      result=result, independent=checked, revision=revision))
                    exported = json.loads((directory / "manifest.json").read_bytes())
                    self.assertEqual(spec.source_pins["ibm_worlds_generator"], exported["generator_sha256"])
                    raw = b"".join(v.canonical_json_bytes(item) for item in _lifecycle_rows(row, directory, binding))
                    lifecycle = e.build_receipt(raw, binding, directory, 0, 0, revision=revision)
                    self.assertEqual("COMPUTE_CONTAINED", lifecycle["result"])
                    self.assertEqual(0o700, directory.stat().st_mode & 0o777)
                    self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in directory.iterdir()))


if __name__ == "__main__":
    unittest.main()
