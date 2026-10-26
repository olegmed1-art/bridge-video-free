"""Build-only three-wheel provenance; no application or DB import."""
import json,re,sys,importlib.metadata
from pathlib import Path
from urllib.parse import urlsplit,unquote
def verify(pins,report):
    expected={w['name']:w for w in pins['wheels']};assert len(expected)==3
    seen=set()
    for item in report['install']:
        assert item.get('is_yanked',False) is False,'YANKED_WHEEL'
        name=re.sub('[-_.]+','-',item['metadata']['name']).lower();assert name in expected and name not in seen
        w=expected[name];u=urlsplit(item['download_info']['url']);assert u.scheme=='https' and u.hostname=='files.pythonhosted.org'
        assert unquote(u.path.rsplit('/',1)[-1])==w['filename']
        assert item['download_info']['archive_info']['hashes']['sha256']==w['sha256']
        assert item['metadata']['version']==w['version']==importlib.metadata.version(name);seen.add(name)
    assert seen==set(expected)
if __name__=='__main__':verify(*[json.loads(Path(n).read_bytes()) for n in sys.argv[1:]])
