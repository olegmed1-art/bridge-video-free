#!/usr/bin/env python3
"""Regression checks for the known Diana 9 ASR collapse."""
from pathlib import Path
import tempfile

import bridge_worker_3_1_free as core
import run_drive_3_1_free as io
import run_master_3_1_free as base
from bridge_runtime_hardening_r6 import REVISION, asr_hallucination_risk, install


def main():
    assert REVISION == '3.1-free-master-analysis-r6'
    assert core.ALGORITHM_VERSION == '3.1 FREE'

    normal = 'Стейман, один без козыря, пятнадцать очков, затем три без козыря.'
    diana9_collapse = ' '.join(['[Аплодисменты]'] * 40)
    assert not asr_hallucination_risk(normal)
    assert asr_hallucination_risk(diana9_collapse)

    # Reproduce the structural defect observed in Diana 9 run 18: 24 checked windows
    # pass but two remain failed. r5/base logic allowed that percentage; r6 must fail closed.
    original_qc = base.qc_transcript
    original_safe = io.safe
    events = []
    try:
        base.qc_transcript = lambda video, work, dur, segs: (
            [{'block': i, 'ok': i not in {21, 25}} for i in range(26)],
            True,
        )
        io.safe = lambda **kw: events.append(kw)
        install(lambda: 'fresh-token')
        qc, passed = base.qc_transcript(Path('video.mp4'), Path(tempfile.gettempdir()), 7800, [])
        assert len(qc) == 26
        assert sum(not x['ok'] for x in qc) == 2
        assert passed is False
        assert any(e.get('stage') == 'ASR_QC_FAIL_CLOSED' and e.get('exit_code') == 1 for e in events)
        assert core.ALGORITHM_REVISION == REVISION
    finally:
        base.qc_transcript = original_qc
        io.safe = original_safe

    print('r6 Diana 9 ASR fail-closed regression test: PASS')


if __name__ == '__main__':
    main()
