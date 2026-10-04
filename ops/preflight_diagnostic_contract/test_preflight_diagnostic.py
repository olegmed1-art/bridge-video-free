import unittest
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path
import diagnostic_guest as d
import guest


class DiagnosticTests(unittest.TestCase):
    def test_reports_multiple_units_and_properties(self):
        rows={u:{'Requires':'docker.service sysinit.target','Wants':'network-online.target'} for u in guest.TARGETS}
        self.assertFalse(any(x.get('unexpected') for x in d.dependency_report(rows,guest.TARGETS,guest.BASE)))
        rows[guest.TARGETS[0]]['Requires']+=' unexpected.mount'
        rows[guest.TARGETS[3]]['PartOf']='outside.service'
        bad=[x for x in d.dependency_report(rows,guest.TARGETS,guest.BASE) if x.get('unexpected')]
        self.assertEqual([(x['unit'],x['property'],x['unexpected']) for x in bad],
                         [(guest.TARGETS[0],'Requires',['unexpected.mount']),
                          (guest.TARGETS[3],'PartOf',['outside.service'])])

    def test_symlink_ancestor_blocks_descendant_scans(self):
        stub=SimpleNamespace(TARGETS=guest.TARGETS,bounded=lambda p:b'')
        g=SimpleNamespace(root='/nonexistent/synthetic-school/synthetic-lab-observer',command=lambda argv:'123')
        row={'MainPID':'123','ControlGroup':'/system.slice/synthetic-lab-observer.service'}
        with patch.object(Path,'is_symlink',lambda p:p.name==Path(g.root).parent.name), \
             patch.object(Path,'is_dir',side_effect=AssertionError('must not traverse')), \
             patch.object(Path,'rglob',return_value=iter(())), \
             patch.object(d.os,'scandir',side_effect=AssertionError('must not scan')):
            result=d.runtime_inventory(g,stub,guest.TARGETS[3],row)
        self.assertTrue(all(x=={'blocked':'SYMLINK_ANCESTOR'} for x in result['directories'].values()))

    def test_invalid_dependency_token_is_redacted(self):
        rows={u:{} for u in guest.TARGETS}
        rows[guest.TARGETS[0]]['Requires']='not/a/unit'
        bad=[x for x in d.dependency_report(rows,guest.TARGETS,guest.BASE) if x.get('error')]
        self.assertEqual(len(bad),1)
        self.assertNotIn('actual',bad[0])


if __name__=='__main__':unittest.main()
