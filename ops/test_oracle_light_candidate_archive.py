import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parent))
import oracle_light_candidate_backup as target


class ArchiveSafety(unittest.TestCase):
    def make(self,path,mode='valid'):
        files={name:b'PGDMPsynthetic' for name in ('autopilot.dump','ledger.dump','health.dump')}
        files['acl.sql']=b'synthetic test ACL'
        aclhash=hashlib.sha256(files['acl.sql']).hexdigest()
        manifest=dict(format=1,database=target.candidate.DATABASE,snapshot_scope='FENCED_CANDIDATE_ONLY',
                      roles_nologin=list(target.candidate.ROLES),locale='C.UTF-8',
                      files={k:hashlib.sha256(v).hexdigest() for k,v in files.items()},
                      data={'function_definitions':[97,'hash'],'effective_acl':[291,'hash']})
        if mode=='wrong_locale': manifest['locale']='en_US'
        if mode=='corrupt_hash': manifest['files']['autopilot.dump']='0'*64
        files['manifest.json']=json.dumps(manifest).encode()
        if mode=='extra_path': files['../escape']=b'bad'
        with tarfile.open(path,'w:gz') as archive:
            for name,data in files.items():
                entry=tarfile.TarInfo(name)
                entry.size=len(data)
                if mode=='symlink' and name=='acl.sql':
                    entry.type=tarfile.SYMTYPE
                    entry.linkname='/etc/passwd'
                    entry.size=0
                    archive.addfile(entry)
                else:
                    archive.addfile(entry,io.BytesIO(data))
        return aclhash

    def test_valid_bounded_archive(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'backup.tar.gz'
            digest=self.make(path)
            with patch.dict(target.candidate.FILES,{'rehearsal-only-acl.sql':digest}):
                payloads,manifest=target.validate_archive(path)
                self.assertEqual(set(payloads),target.MEMBERS)

    def test_unsafe_archives_rejected(self):
        for mode in ('wrong_locale','corrupt_hash','extra_path','symlink'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                path=Path(d)/'backup.tar.gz'
                digest=self.make(path,mode)
                with patch.dict(target.candidate.FILES,{'rehearsal-only-acl.sql':digest}):
                    with self.assertRaises(AssertionError):
                        target.validate_archive(path)


if __name__=='__main__':
    unittest.main()
