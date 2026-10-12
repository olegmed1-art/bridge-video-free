"""Exercise the production change guard with real local Git objects; no network."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.verify_video_checkpoint_baseline import PROTECTED_PATHS, verify_baseline


class BaselineGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Local contract test")
        self.git("config", "user.email", "contract@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.git("config", "commit.gpgsign", "false")
        self.write(PROTECTED_PATHS[0], 'revision: "invented-future-version"\n')
        self.write(PROTECTED_PATHS[1], "import invented_future_runtime\n")
        self.base = self.commit("reviewed baseline")
        self.write("checkpoint.txt", "candidate-only observation\n")
        self.commit("checkpoint candidate")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, text=True).strip()

    def write(self, name, content):
        p = self.repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")

    def commit(self, message):
        self.git("add", ".")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def test_unchanged_future_baseline_and_unrelated_candidate_pass(self):
        verify_baseline(self.repo, self.base)

    def test_production_revision_change_fails_even_with_old_label_in_comment(self):
        self.write(PROTECTED_PATHS[0], '# revision: "invented-future-version"\nrevision: "changed"\n')
        self.commit("production mutation")
        with self.assertRaises(ValueError):
            verify_baseline(self.repo, self.base)

    def test_runtime_import_change_fails_even_with_old_marker_in_comment(self):
        self.write(PROTECTED_PATHS[1], "# import invented_future_runtime\nimport different_runtime\n")
        self.commit("runtime mutation")
        with self.assertRaises(ValueError):
            verify_baseline(self.repo, self.base)

    def test_missing_candidate_file_fails(self):
        self.git("rm", "--", PROTECTED_PATHS[1])
        self.commit("missing candidate file")
        with self.assertRaises(ValueError):
            verify_baseline(self.repo, self.base)

    def test_missing_in_both_revisions_fails(self):
        self.git("rm", "--", PROTECTED_PATHS[1])
        base = self.commit("incomplete baseline")
        self.write("checkpoint.txt", "next\n")
        self.commit("candidate from incomplete baseline")
        with self.assertRaises(ValueError):
            verify_baseline(self.repo, base)

    def test_file_mode_change_fails(self):
        self.git("update-index", "--chmod=+x", "--", PROTECTED_PATHS[1])
        self.git("commit", "-qm", "mode mutation")
        with self.assertRaises(ValueError):
            verify_baseline(self.repo, self.base)

    def test_uncommitted_production_mutation_fails(self):
        self.write(PROTECTED_PATHS[1], "import uncommitted_runtime\n")
        with self.assertRaises(ValueError):
            verify_baseline(self.repo, self.base)

    def test_symlink_tree_entry_fails(self):
        blob = self.git("rev-parse", "HEAD:" + PROTECTED_PATHS[1])
        self.git("update-index", "--cacheinfo", "120000", blob, PROTECTED_PATHS[1])
        self.git("commit", "-qm", "symlink mutation")
        with self.assertRaises(ValueError):
            verify_baseline(self.repo, self.base)

    def test_absent_symbolic_or_unknown_baseline_fails(self):
        for baseline in ("", "main", "--help", "0" * 40):
            with self.subTest(baseline=baseline), self.assertRaises(ValueError):
                verify_baseline(self.repo, baseline)

    def test_blob_cannot_serve_as_commit_baseline(self):
        blob = self.git("rev-parse", "HEAD:" + PROTECTED_PATHS[0])
        with self.assertRaises(ValueError):
            verify_baseline(self.repo, blob)

    def test_candidate_cannot_serve_as_own_baseline(self):
        with self.assertRaises(ValueError):
            verify_baseline(self.repo, self.git("rev-parse", "HEAD"))


if __name__ == "__main__":
    unittest.main()
