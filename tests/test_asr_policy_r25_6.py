#!/usr/bin/env python3
import os

import bridge_runtime_hardening_r25_1 as asr
import bridge_runtime_hardening_r25_6 as r25_6


def main():
    isolated_zero = [{"ok": False, "similarity": 0.0}]

    # Simulate the rolled-back main branch, where a zero-overlap control window
    # was still a whole-job stop. r25.6 must restore the confirmed job-86 policy
    # during install instead of depending on pre-rollback module contents.
    asr.strict_qc_pass = lambda qc, base_passed, hallucination_blocks=0: False
    old_requested = os.environ.get("BRIDGE_REQUESTED_ALGORITHM_REVISION")
    os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = r25_6.REVISION
    try:
        r25_6.install(lambda: "test-token")
    finally:
        if old_requested is None:
            os.environ.pop("BRIDGE_REQUESTED_ALGORITHM_REVISION", None)
        else:
            os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = old_requested

    assert asr.strict_qc_pass(isolated_zero, True, hallucination_blocks=0)
    assert not asr.strict_qc_pass(isolated_zero, True, hallucination_blocks=1)
    assert not asr.strict_qc_pass(isolated_zero, False, hallucination_blocks=0)
    assert asr.pathological_nonspeech_hallucination(" ".join(["[Аплодисменты]"] * 20))
    assert not asr.pathological_nonspeech_hallucination("Спасибо [Аплодисменты] продолжаем")
    assert r25_6.REVISION == "3.1-free-r25.6"
    print("R25_6_ASR_POLICY: PASS isolated_zero=quarantine hallucination=hard-stop")


if __name__ == "__main__":
    main()
