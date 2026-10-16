"""Pure-Python fail-closed checks for the future PG18 ACL regression."""

from contextlib import contextmanager
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import one_job_0060_default_acl_pg18 as acl


ROOT = Path(__file__).resolve().parents[1]
SIXTY_FILE = '0060_fixed_subset_admission.sql'


def _selected_0060(root: Path) -> Path:
    directories = [root / 'database' / part for part in ('migrations', 'candidates')]
    possible = [directory / SIXTY_FILE for directory in directories]
    matches = [path for path in possible if path.is_file()]
    other_0060 = [path for directory in directories
                  for path in directory.glob('0060_*.sql') if path.name != SIXTY_FILE]
    if len(matches) != 1 or other_0060:
        raise RuntimeError('ONE_JOB_0060_LOCATION_INVALID')
    acl.selected_candidate(matches[0])
    return matches[0]


@contextmanager
def _scratch():
    root = Path(tempfile.mkdtemp(prefix='one-job-0060-location-', dir=ROOT))
    try:
        yield root
    finally:
        if not root.resolve().is_relative_to(ROOT.resolve()):
            raise RuntimeError('ONE_JOB_0060_SCRATCH_OUTSIDE_WORKSPACE')
        shutil.rmtree(root)


def _declarations(path: Path) -> dict[str, str]:
    text = path.read_text(encoding='utf-8')
    found = {}
    for match in re.finditer(
            r'CREATE(?: OR REPLACE)? FUNCTION video_queue\.([a-z0-9_]+)'
            r'\s*\((.*?)\)\s*RETURNS', text, re.I | re.S):
        name, arguments = match.groups()
        types = [part.strip().split()[1] for part in arguments.split(',')
                 if part.strip()]
        found[name] = f'video_queue.{name}({",".join(types)})'
    return found


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0]


class _Catalog:
    signature = 'video_queue.sample(integer)'

    def __init__(self, rows, resolved_oid=11):
        self.rows = rows
        self.resolved_oid = resolved_oid

    def execute(self, query, params):
        if 'FROM pg_proc AS p' in query:
            assert params == (['sample'],)
            return _Cursor(self.rows)
        if 'to_regprocedure' in query:
            assert params == (self.signature,)
            return _Cursor([{'oid': self.resolved_oid}])
        raise AssertionError(query)


class ExactFunctionIdentityTest(unittest.TestCase):
    signature = {'sample': _Catalog.signature}

    def row(self, **changes):
        row = dict(proname='sample', oid=11, prokind='f', owner='postgres',
                   public_execute=False)
        row.update(changes)
        return row

    def test_exact_function_is_accepted(self):
        self.assertEqual(acl._functions(_Catalog([self.row()]), self.signature,
                                         'postgres'), {'sample': 11})

    def test_changed_signature_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, 'sample_signature'):
            acl._functions(_Catalog([self.row(oid=12)]), self.signature,
                           'postgres')

    def test_missing_expected_signature_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, 'sample_signature'):
            acl._functions(_Catalog([self.row(oid=12)], resolved_oid=None),
                           self.signature, 'postgres')

    def test_changed_prokind_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, 'sample_prokind'):
            acl._functions(_Catalog([self.row(prokind='p')]), self.signature,
                           'postgres')

    def test_extra_overload_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, 'function_inventory'):
            acl._functions(_Catalog([self.row(), self.row(oid=12)]),
                           self.signature, 'postgres')

    def test_reviewed_signatures_match_sql_declarations(self):
        self.assertEqual(acl.SIXTY_SIGNATURES, _declarations(_selected_0060(ROOT)))
        proposals = _declarations(ROOT / 'database/proposals/0061_one_job_staging_admission_v1.sql')
        proposals.update(_declarations(
            ROOT / 'database/proposals/0062_one_job_private_result_gate_v1.sql'))
        self.assertEqual(acl.PROPOSAL_SIGNATURES, proposals)

    def test_unchanged_0060_can_move_between_approved_directories(self):
        source = _selected_0060(ROOT)
        destination_dir = 'candidates' if source.parent.name == 'migrations' else 'migrations'
        with _scratch() as root:
            destination = root / 'database' / destination_dir / SIXTY_FILE
            destination.parent.mkdir(parents=True)
            shutil.copyfile(source, destination)
            self.assertEqual(destination.read_bytes(), source.read_bytes())
            self.assertEqual(_selected_0060(root), destination)
            self.assertEqual(acl.SIXTY_SIGNATURES, _declarations(destination))

    def test_two_0060_copies_are_rejected(self):
        source = _selected_0060(ROOT)
        with _scratch() as root:
            for directory in ('migrations', 'candidates'):
                destination = root / 'database' / directory / SIXTY_FILE
                destination.parent.mkdir(parents=True)
                shutil.copyfile(source, destination)
            with self.assertRaisesRegex(RuntimeError, 'ONE_JOB_0060_LOCATION_INVALID'):
                _selected_0060(root)

    def test_unreviewed_0060_bytes_are_rejected(self):
        source = _selected_0060(ROOT)
        with _scratch() as root:
            destination = root / 'database' / 'candidates' / SIXTY_FILE
            destination.parent.mkdir(parents=True)
            destination.write_bytes(source.read_bytes() + b' ')
            with self.assertRaisesRegex(RuntimeError, 'ONE_JOB_0060_ACL_VARIANT_UNREVIEWED'):
                _selected_0060(root)

    def test_missing_0060_is_rejected(self):
        with _scratch() as root:
            with self.assertRaisesRegex(RuntimeError, 'ONE_JOB_0060_LOCATION_INVALID'):
                _selected_0060(root)


if __name__ == '__main__':
    unittest.main()
