#!/usr/bin/env python3
from bridge_runtime_hardening_r5 import REVISION, asr_hallucination_risk, install
import bridge_worker_3_1_free as core
import run_drive_3_1_free as io
import run_master_3_1_free as base


def main():
    assert REVISION == '3.1-free-master-analysis-r5'
    assert not asr_hallucination_risk('Стейман, один без козыря, пятнадцать очков, затем три без козыря.')
    assert not asr_hallucination_risk('[Аплодисменты] Хорошая заявка. [Аплодисменты] Продолжаем.')
    bad = ' '.join(['[Аплодисменты]'] * 12)
    assert asr_hallucination_risk(bad)

    issued = []
    def token_func():
        token = f'token-{len(issued)+1}'
        issued.append(token)
        return token

    seen = []
    old_upload, old_perms, old_add, old_download = io.upload_file, io.perms, io.add_perm, io.download
    try:
        io.upload_file = lambda token, parent, path, mime: seen.append(('upload', token)) or {'id':'x'}
        io.perms = lambda token, fid: seen.append(('perms', token)) or []
        io.add_perm = lambda token, fid, p: seen.append(('perm', token))
        io.download = lambda token, fid, output: seen.append(('download', token))
        install(token_func)
        assert core.ALGORITHM_REVISION == REVISION
        assert base.ALGORITHM_REVISION == REVISION
        ok, _ = base._qc_match(bad, bad)
        assert ok is False
        io.upload_file('expired-token', 'parent', __file__, 'text/plain')
        io.perms('expired-token', 'x')
        io.add_perm('expired-token', 'x', {'role':'reader'})
        io.download('expired-token', 'x', __file__)
        assert [x[1] for x in seen] == ['token-1','token-2','token-3','token-4']
    finally:
        io.upload_file, io.perms, io.add_perm, io.download = old_upload, old_perms, old_add, old_download

    print('r5 runtime hardening selftest: PASS')


if __name__ == '__main__':
    main()
