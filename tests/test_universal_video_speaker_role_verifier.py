from universal_video.speaker_role_verifier import verify_speaker_roles


def row(index, speaker=None, role="unknown", confidence=0.0, duration=1.0):
    value = {"segment_id": f"s{index}", "start": float(index), "end": float(index) + duration, "text": "x"}
    if speaker:
        value.update({"speaker": speaker, "speaker_role_candidate": role, "speaker_role_confidence": confidence})
    return value


REPORT = {
    "status": "DIARIZED_ROLE_MAPPED",
    "role_mapping_supported": True,
    "speaker_count_evidence": {
        "mode": "OPEN_SET",
        "selected_count": 2,
        "collapse_check": "PASS",
        "fragmentation_check": "PASS",
        "mixing_check": "PASS",
    },
}


def test_two_speakers_and_separate_roles_pass():
    rows = [row(i, "SPEAKER_A", "teacher", .9) for i in range(5)]
    rows += [row(i + 5, "SPEAKER_B", "student", .9) for i in range(5)]
    proof = verify_speaker_roles(rows, REPORT)
    assert proof["status"] == "PASS"
    assert proof["observed_speaker_count"] == 2
    assert proof["role_map"]["SPEAKER_A"]["status"] == "MAPPED"
    assert proof["role_map"]["SPEAKER_B"]["role"] == "student"


def test_one_speaker_is_collapse_not_mapped_pass():
    proof = verify_speaker_roles([row(i, "SPEAKER_A", "teacher", .9) for i in range(10)], REPORT)
    assert proof["status"] == "INCONCLUSIVE"
    assert "SPEAKER_COLLAPSE" in proof["blockers"]


def test_three_clusters_are_excessive_fragmentation():
    rows = [row(i, f"SPEAKER_{letter}", "teacher" if letter != "B" else "student", .9) for i, letter in enumerate("ABCABCABC")]
    proof = verify_speaker_roles(rows, REPORT)
    assert proof["status"] == "INCONCLUSIVE"
    assert "EXCESSIVE_FRAGMENTATION" in proof["blockers"]


def test_both_coverage_denominators_are_recomputed():
    rows = [row(i, "SPEAKER_A" if i % 2 else "SPEAKER_B", "teacher" if i % 2 else "student", .9, .25) for i in range(8)]
    rows += [row(8, duration=8.0), row(9, duration=8.0)]
    proof = verify_speaker_roles(rows, REPORT)
    assert proof["coverage"]["by_segments"] == .8
    assert proof["coverage"]["by_speech_duration"] < .8
    assert "DURATION_COVERAGE_BELOW_0_80" in proof["blockers"]


def test_cross_role_conflict_stays_unmapped():
    rows = [row(i, "SPEAKER_A", "teacher", .9) for i in range(4)]
    rows += [row(4, "SPEAKER_A", "student", .9)]
    rows += [row(i + 5, "SPEAKER_B", "student", .9) for i in range(5)]
    proof = verify_speaker_roles(rows, REPORT)
    assert proof["role_map"]["SPEAKER_A"]["status"] == "UNMAPPED"
    assert "UNMAPPED_ACTIVE_CLUSTER" in proof["blockers"]


def test_mapped_claim_without_producer_role_evidence_fails_closed():
    rows = [row(i, "SPEAKER_A", "teacher", .9) for i in range(5)]
    rows += [row(i + 5, "SPEAKER_B", "student", .9) for i in range(5)]
    proof = verify_speaker_roles(rows, {"status": "DIARIZED_UNMAPPED", "role_mapping_supported": False})
    assert proof["status"] == "INCONCLUSIVE"
    assert "PRODUCER_ROLE_EVIDENCE_UNAVAILABLE" in proof["blockers"]
    assert "PRODUCER_STATUS_NOT_MAPPED" in proof["blockers"]


def test_forced_two_cluster_output_does_not_prove_real_speaker_count():
    rows = [row(i, "SPEAKER_A", "teacher", .9) for i in range(5)]
    rows += [row(i + 5, "SPEAKER_B", "student", .9) for i in range(5)]
    proof = verify_speaker_roles(
        rows,
        {"status": "DIARIZED_ROLE_MAPPED", "role_mapping_supported": True},
    )
    assert proof["status"] == "INCONCLUSIVE"
    assert proof["speaker_count_status"] == "UNPROVED"
    assert "REAL_SPEAKER_COUNT_AND_MIXING_UNPROVED" in proof["blockers"]
