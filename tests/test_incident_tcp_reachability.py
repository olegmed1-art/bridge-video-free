"""Offline tests: real resolver and socket access are denied throughout."""
import contextlib
import ast
import errno
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import socket
import textwrap
import unittest
from unittest import mock


HELPER = Path(__file__).resolve().parents[1] / "scripts/diagnostics/incident_tcp_reachability.py"
with mock.patch("socket.socket", side_effect=AssertionError("real socket forbidden")), \
     mock.patch("socket.getaddrinfo", side_effect=AssertionError("real DNS forbidden")):
    spec = importlib.util.spec_from_file_location("tcp_diagnostic", HELPER)
    diagnostic = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(diagnostic)

SYNTHETIC_HOST = "ep-example-pooler.c-5.eu-central-1.aws.neon.tech"


def row(family, address, port=5432):
    sockaddress = (address, port) if family == socket.AF_INET else (address, port, 0, 0)
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddress)


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.dns = self.enterContext(mock.patch.object(diagnostic.socket, "getaddrinfo",
                                  side_effect=AssertionError("real DNS forbidden")))
        self.sockets = self.enterContext(mock.patch.object(diagnostic.socket, "socket",
                                      side_effect=AssertionError("real socket forbidden")))

    def config(self, text):
        path = mock.Mock(spec=Path)
        raw = text.encode()
        path.read_bytes.return_value = raw
        self.enterContext(mock.patch.object(diagnostic, "CONFIG_SHA256", hashlib.sha256(raw).hexdigest()))
        return path

    def source(self):
        return self.config("PRODUCTION_HOST = " + repr(SYNTHETIC_HOST) + "\n")

    def observe_rows(self, rows, statuses=None):
        resolve = self.enterContext(mock.patch.object(diagnostic, "resolve", return_value=(rows, "resolved", 1)))
        attempt = self.enterContext(mock.patch.object(diagnostic, "tcp_attempt",
                        side_effect=statuses or (lambda *_: {"status": "connected_no_payload", "elapsed_ms": 1})))
        result = diagnostic.observe(self.source())
        return result, resolve, attempt

    def test_import_has_no_network_side_effect(self):
        self.dns.assert_not_called()
        self.sockets.assert_not_called()

    def test_explicit_flag_required_before_configuration_or_network(self):
        with mock.patch.object(diagnostic, "observe") as observe, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(diagnostic.main([]), 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "explicit_run_flag_required")
        observe.assert_not_called()

    def test_literal_target_from_pinned_configuration(self):
        self.assertEqual(diagnostic.read_target(self.source()), SYNTHETIC_HOST)

    def test_config_hash_drift_prevents_all_network(self):
        path = self.source()
        path.read_bytes.return_value = b"changed"
        self.assertEqual(diagnostic.observe(path)["status"], "configuration_rejected")
        self.dns.assert_not_called()
        self.sockets.assert_not_called()

    def test_duplicate_target_is_rejected(self):
        path = self.config(f"PRODUCTION_HOST = {SYNTHETIC_HOST!r}\nPRODUCTION_HOST = {SYNTHETIC_HOST!r}\n")
        with self.assertRaises(ValueError):
            diagnostic.read_target(path)

    def test_dynamic_target_is_not_executed(self):
        path = self.config("PRODUCTION_HOST = dangerous_function()\n")
        with self.assertRaises(ValueError):
            diagnostic.read_target(path)

    def test_wrong_target_suffix_is_rejected(self):
        path = self.config("PRODUCTION_HOST = 'unapproved.invalid'\n")
        with self.assertRaises(ValueError):
            diagnostic.read_target(path)

    def test_fixed_error_categories(self):
        cases = [(errno.ECONNREFUSED, "refused"), (errno.ENETUNREACH, "network_unreachable"),
                 (errno.EHOSTUNREACH, "host_unreachable"), (errno.ECONNRESET, "reset"),
                 (errno.EACCES, "local_permission_denied"), (errno.EAFNOSUPPORT, "family_unsupported"),
                 (errno.EADDRNOTAVAIL, "local_address_unavailable"), (errno.ETIMEDOUT, "timeout")]
        for code, expected in cases:
            with self.subTest(category=expected):
                self.assertEqual(diagnostic.error_category(OSError(code, "private error")), expected)

    def test_unknown_error_text_is_never_used(self):
        class PrivateError(Exception):
            def __str__(self):
                raise AssertionError("must not stringify")
        self.assertEqual(diagnostic.error_category(PrivateError()), "unknown")

    def test_resolver_is_one_system_call_with_fixed_arguments(self):
        self.dns.side_effect = None
        self.dns.return_value = [row(socket.AF_INET, "1.1.1.1")]
        rows, status, _ = diagnostic.resolve(SYNTHETIC_HOST, 5432)
        self.assertEqual(status, "resolved")
        self.assertEqual(len(rows), 1)
        self.dns.assert_called_once_with(SYNTHETIC_HOST, 5432, family=socket.AF_UNSPEC,
                                        type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)

    def test_resolver_error_is_fixed_category(self):
        self.dns.side_effect = socket.gaierror(socket.EAI_AGAIN, "private address")
        rows, status, _ = diagnostic.resolve(SYNTHETIC_HOST, 5432)
        self.assertEqual((rows, status), ([], "dns_temporary_failure"))

    def test_resolver_deadline_is_fixed_and_thread_is_daemon(self):
        q = mock.Mock()
        q.get.side_effect = diagnostic.queue.Empty
        with mock.patch.object(diagnostic.queue, "Queue", return_value=q), \
             mock.patch.object(diagnostic.threading, "Thread") as thread:
            rows, status, _ = diagnostic.resolve(SYNTHETIC_HOST, 5432)
        self.assertEqual((rows, status), ([], "dns_deadline_exceeded"))
        q.get.assert_called_once_with(timeout=5)
        self.assertTrue(thread.call_args.kwargs["daemon"])
        self.dns.assert_not_called()

    def test_tcp_success_closes_socket_and_sends_no_payload(self):
        sock = mock.MagicMock()
        sock.__enter__.return_value = sock
        self.sockets.side_effect = None
        self.sockets.return_value = sock
        result = diagnostic.tcp_attempt(socket.AF_INET, ("1.1.1.1", 5432))
        self.assertEqual(result["status"], "connected_no_payload")
        sock.settimeout.assert_called_once_with(3)
        sock.connect.assert_called_once_with(("1.1.1.1", 5432))
        sock.__exit__.assert_called_once()
        for method in (sock.send, sock.sendall, sock.recv, sock.makefile):
            method.assert_not_called()

    def test_tcp_failure_closes_and_redacts_error(self):
        sock = mock.MagicMock()
        sock.__enter__.return_value = sock
        sock.connect.side_effect = OSError(errno.ECONNREFUSED, SYNTHETIC_HOST)
        self.sockets.side_effect = None
        self.sockets.return_value = sock
        result = diagnostic.tcp_attempt(socket.AF_INET, ("1.1.1.1", 5432))
        self.assertEqual(result["status"], "refused")
        sock.__exit__.assert_called_once()
        self.assertNotIn(SYNTHETIC_HOST, json.dumps(result))

    def test_both_families_tested_independently(self):
        result, _, attempt = self.observe_rows([row(socket.AF_INET, "1.1.1.1"), row(socket.AF_INET6, "2606:4700:4700::1111")])
        self.assertEqual(attempt.call_count, 2)
        self.assertEqual([x["family"] for x in result["families"]], ["ipv4", "ipv6"])
        self.assertTrue(all(x["attempts"][0]["status"] == "connected_no_payload" for x in result["families"]))

    def test_duplicates_do_not_consume_attempts(self):
        result, _, attempt = self.observe_rows([row(socket.AF_INET, "1.1.1.1")] * 4)
        self.assertEqual(result["families"][0]["resolved_address_count"], 1)
        self.assertEqual(attempt.call_count, 1)

    def test_at_most_two_addresses_per_family(self):
        rows = [row(socket.AF_INET, f"1.1.1.{n}") for n in range(1, 6)]
        rows += [row(socket.AF_INET6, f"2606:4700:4700::{n}") for n in range(1, 6)]
        result, _, attempt = self.observe_rows(rows, lambda *_: {"status": "timeout", "elapsed_ms": 3000})
        self.assertEqual(attempt.call_count, 4)
        self.assertEqual([len(x["attempts"]) for x in result["families"]], [2, 2])

    def test_success_stops_family_but_not_other_family(self):
        rows = [row(socket.AF_INET, "1.1.1.1"), row(socket.AF_INET, "1.1.1.2"), row(socket.AF_INET6, "2606:4700:4700::1111")]
        _, _, attempt = self.observe_rows(rows)
        self.assertEqual(attempt.call_count, 2)

    def test_permission_denial_stops_family(self):
        result, _, attempt = self.observe_rows([row(socket.AF_INET, "1.1.1.1"), row(socket.AF_INET, "1.1.1.2")],
                           lambda *_: {"status": "local_permission_denied", "elapsed_ms": 0})
        self.assertEqual(attempt.call_count, 1)
        self.assertEqual(len(result["families"][0]["attempts"]), 1)

    def test_nonpublic_or_mismatched_dns_results_are_not_connected(self):
        rows = [row(socket.AF_INET, "127.0.0.1"), row(socket.AF_INET, "10.0.0.1"),
                row(socket.AF_INET, "1.1.1.1", 443), row(socket.AF_INET6, "::1")]
        result, _, attempt = self.observe_rows(rows)
        attempt.assert_not_called()
        self.assertEqual([x["rejected_address_count"] for x in result["families"]], [3, 1])

    def test_dns_failure_uses_only_dns_control_and_no_tcp(self):
        with mock.patch.object(diagnostic, "resolve", side_effect=[([], "dns_name_or_record_absent", 2),
                  ([row(socket.AF_INET, "1.1.1.1", 443)], "resolved", 3)]) as resolve:
            result = diagnostic.observe(self.source())
        self.assertEqual(resolve.call_args_list, [mock.call(SYNTHETIC_HOST, 5432), mock.call("github.com", 443)])
        self.assertEqual(result["control_dns"]["status"], "resolved")
        self.assertFalse(result["control_dns"]["tcp_attempted"])
        self.sockets.assert_not_called()

    def test_successful_target_dns_does_not_call_control(self):
        result, resolve, _ = self.observe_rows([row(socket.AF_INET, "1.1.1.1")])
        resolve.assert_called_once()
        self.assertEqual(result["control_dns"], {"status": "not_needed"})

    def test_report_contains_no_host_ip_or_private_exception(self):
        result, _, _ = self.observe_rows([row(socket.AF_INET, "1.1.1.1"), row(socket.AF_INET6, "2606:4700:4700::1111")])
        value = json.dumps(result)
        for private in [SYNTHETIC_HOST, "github.com", "1.1.1.1", "2606:4700:4700::1111"]:
            self.assertNotIn(private, value)
        self.assertFalse(result["credentials_used"])
        self.assertEqual(result["protocol_bytes_sent"], 0)

    def test_unexpected_failure_main_is_redacted(self):
        with mock.patch.object(diagnostic, "observe", side_effect=RuntimeError("private DSN")), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(diagnostic.main(["--run-once"]), 1)
        self.assertEqual(json.loads(output.getvalue()), {"schema": diagnostic.SCHEMA, "status": "diagnostic_failed"})


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch("socket.socket", side_effect=AssertionError("real socket forbidden")))
        self.enterContext(mock.patch("socket.getaddrinfo", side_effect=AssertionError("real DNS forbidden")))
        workflow = HELPER.parents[2] / ".github/workflows/incident-tcp-reachability-once.yml"
        source = workflow.read_text(encoding="utf-8")
        code = textwrap.dedent(source.split("<<'PY' 2>/dev/null\n", 1)[1].split("\n          PY\n", 1)[0])
        tree = ast.parse(code)
        tree.body = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef))]
        self.ns = {}
        exec(compile(tree, "reviewed_bootstrap", "exec"), self.ns)
        self.env = {"META_EVENT": "create", "META_REPOSITORY": "olegmed1-art/bridge-video-free",
                    "META_REF_TYPE": "branch", "META_BRANCH": "codex-incident-1911-tcp-once-20261012",
                    "META_ATTEMPT": "1", "META_RUN_NUMBER": "1", "META_SHA": "a" * 40,
                    "META_WORKFLOW_SHA": "a" * 40}
        self.enterContext(mock.patch.dict(self.ns["os"].environ, self.env, clear=True))
        self.fetch = self.enterContext(mock.patch.object(self.ns["urllib"].request, "urlopen"))
        response = self.fetch.return_value.__enter__.return_value
        response.read.side_effect = [b"helper", b"config"]
        hashes = [hashlib.sha256(HELPER.read_bytes().replace(b"\r\n", b"\n")).hexdigest(), diagnostic.CONFIG_SHA256]
        self.hash = self.enterContext(mock.patch.object(self.ns["hashlib"], "sha256"))
        self.hash.return_value.hexdigest.side_effect = hashes
        folder = self.enterContext(mock.patch.object(self.ns["tempfile"], "TemporaryDirectory"))
        folder.return_value.__enter__.return_value = "synthetic-private-folder"
        self.enterContext(mock.patch.object(Path, "mkdir"))
        self.enterContext(mock.patch.object(Path, "write_bytes"))
        self.run = self.enterContext(mock.patch.object(self.ns["subprocess"], "run"))
        self.run.return_value = mock.Mock(returncode=0, stdout=b'{"schema":"incident-1911-tcp-once-v1","status":"observed"}')

    def call(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = self.ns["main"]()
        return code, output.getvalue()

    def test_scope_rejects_before_download(self):
        self.ns["os"].environ["META_EVENT"] = "push"
        code, output = self.call()
        self.assertEqual(code, 1)
        self.assertIn("scope_rejected", output)
        self.fetch.assert_not_called()
        self.run.assert_not_called()

    def test_scope_comparison_is_case_sensitive(self):
        self.ns["os"].environ["META_BRANCH"] = self.env["META_BRANCH"].upper()
        self.assertEqual(self.call()[0], 1)
        self.fetch.assert_not_called()

    def test_repeat_attempt_or_run_number_is_rejected(self):
        for key in ("META_ATTEMPT", "META_RUN_NUMBER"):
            with self.subTest(key=key):
                self.ns["os"].environ[key] = "2"
                self.assertEqual(self.call()[0], 1)
                self.ns["os"].environ[key] = "1"
        self.fetch.assert_not_called()

    def test_different_workflow_sha_is_rejected(self):
        self.ns["os"].environ["META_WORKFLOW_SHA"] = "b" * 40
        self.assertEqual(self.call()[0], 1)
        self.fetch.assert_not_called()

    def test_bad_commit_syntax_is_rejected(self):
        self.ns["os"].environ["META_SHA"] = "../other"
        self.assertEqual(self.call()[0], 1)
        self.fetch.assert_not_called()

    def test_no_credentials_and_fixed_subprocess_deadline(self):
        self.assertEqual(self.call()[0], 0)
        self.assertEqual(self.fetch.call_count, 2)
        self.assertTrue(all(c.kwargs == {"timeout": 5} for c in self.fetch.call_args_list))
        self.run.assert_called_once()
        self.assertEqual(self.run.call_args.kwargs["env"], {})
        self.assertEqual(self.run.call_args.kwargs["timeout"], 25)
        self.assertEqual(self.run.call_args.args[0][-1], "--run-once")

    def test_integrity_failure_prevents_diagnostic(self):
        self.hash.return_value.hexdigest.side_effect = ["0" * 64]
        code, output = self.call()
        self.assertEqual(code, 1)
        self.assertIn("source_integrity_rejected", output)
        self.run.assert_not_called()

    def test_source_failure_is_redacted_and_not_retried(self):
        self.fetch.side_effect = RuntimeError("private source error")
        code, output = self.call()
        self.assertEqual(code, 1)
        self.assertNotIn("private source error", output)
        self.assertEqual(self.fetch.call_count, 1)
        self.run.assert_not_called()

    def test_child_timeout_is_redacted_and_not_retried(self):
        self.run.side_effect = self.ns["subprocess"].TimeoutExpired("private command", 25, output=b"private data")
        code, output = self.call()
        self.assertEqual(code, 1)
        self.assertIn("diagnostic_deadline_exceeded", output)
        self.assertNotIn("private", output)
        self.run.assert_called_once()

    def test_arbitrary_child_text_is_not_public(self):
        self.run.return_value.stdout = b"private raw exception and address"
        code, output = self.call()
        self.assertEqual(code, 1)
        self.assertIn("diagnostic_output_rejected", output)
        self.assertNotIn("private raw", output)

    def test_arbitrary_child_json_key_is_not_public(self):
        self.run.return_value.stdout = b'{"schema":"incident-1911-tcp-once-v1","status":"observed","private_hostname":"private"}'
        code, output = self.call()
        self.assertEqual(code, 1)
        self.assertNotIn("private", output)


if __name__ == "__main__":
    unittest.main()
