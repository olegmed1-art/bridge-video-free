"""Synthetic configuration boundary tests; never access host resources."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

if sys.platform != 'linux':
    sys.modules.setdefault('fcntl', types.ModuleType('fcntl'))
    sys.modules.setdefault('pwd', types.ModuleType('pwd'))
HERE = Path(__file__).resolve().parent
RAW = (HERE / 'synthetic-config.json').read_bytes()

def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()

def module(raw=RAW, digest=None, supplied=True):
    spec = importlib.util.spec_from_file_location('synthetic_candidate', HERE / 'collector.py')
    result = importlib.util.module_from_spec(spec)
    if supplied:
        result.PRIVATE_CONFIG_BYTES = raw
        result.PRIVATE_CONFIG_SHA256 = digest if digest is not None else hashlib.sha256(raw).hexdigest()
    spec.loader.exec_module(result)
    return result

class Configuration(unittest.TestCase):
    def refuses(self, *args, **kwargs):
        with mock.patch('sys.stdout', new_callable=io.StringIO) as output, mock.patch('os.open') as host_open, mock.patch('subprocess.run') as command:
            with self.assertRaises(SystemExit) as result:
                module(*args, **kwargs)
            self.assertEqual(result.exception.code, 2)
            self.assertEqual(json.loads(output.getvalue()), {'issue_allowed': False, 'stage': 'CONFIGURATION', 'state': 'REFUSED'})
            host_open.assert_not_called()
            command.assert_not_called()

    def test_missing_configuration(self):
        self.refuses(supplied=False)

    def test_hash_mismatch(self):
        self.refuses(digest='0' * 64)
        self.refuses(digest='not-a-digest')

    def test_noncanonical_configuration(self):
        self.refuses(RAW + b'\n')

    def test_duplicate_keys(self):
        self.refuses(b'{"target_number":73,' + RAW[1:])

    def test_missing_extra_keys(self):
        config = json.loads(RAW)
        config['extra'] = 'synthetic'
        self.refuses(encode(config))
        del config['extra']
        del config['target_number']
        self.refuses(encode(config))

    def test_wrong_types_and_invalid_scalar_values(self):
        for bad in (True, -1, 0, '73', None):
            config = json.loads(RAW)
            config['target_number'] = bad
            self.refuses(encode(config))
        for bad in ('', 'x' * 2049, 'private\0sentinel', []):
            config = json.loads(RAW)
            config['identity_00'] = bad
            self.refuses(encode(config))

    def test_oversize_configuration(self):
        self.refuses(b'x' * 32769)

    def test_configuration_frozen_and_valid(self):
        result = module()
        self.assertEqual(dict(result.CFG), json.loads(RAW))
        with self.assertRaises(TypeError):
            result.CFG['target_number'] = 1

if __name__ == '__main__':
    unittest.main(verbosity=2)
