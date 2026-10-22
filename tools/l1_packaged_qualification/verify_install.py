"""Build-time wheel receipt check; imports no application or database adapter."""
import importlib.metadata
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit


def normalize(name):
    return re.sub('[-_.]+', '-', name).lower()


def verify(pins, report, installed_version):
    expected = {row['name']: row for row in pins['wheels']}
    assert len(expected) == len(pins['wheels']) == 21, 'WHEEL_COUNT'
    observed = {}
    for item in report['install']:
        name = normalize(item['metadata']['name'])
        assert name in expected and name not in observed, 'UNEXPECTED_DISTRIBUTION'
        row = expected[name]
        info = item['download_info']
        url = urlsplit(info['url'])
        assert url.scheme == 'https' and url.hostname == 'files.pythonhosted.org', 'WHEEL_ORIGIN'
        assert unquote(url.path.rsplit('/', 1)[-1]) == row['filename'], 'WHEEL_FILENAME'
        assert info['archive_info']['hashes']['sha256'] == row['sha256'], 'WHEEL_DIGEST'
        assert item['metadata']['version'] == installed_version(name) == row['version'], 'WHEEL_VERSION'
        observed[name] = {'version': row['version'], 'filename': row['filename'], 'sha256': row['sha256']}
    assert set(observed) == set(expected), 'MISSING_WHEEL'
    return observed


if __name__ == '__main__':
    pins, report = [json.loads(Path(p).read_text('utf-8')) for p in sys.argv[1:]]
    print('L1_BUILD_WHEELS ' + json.dumps(verify(pins, report, importlib.metadata.version), sort_keys=True))
