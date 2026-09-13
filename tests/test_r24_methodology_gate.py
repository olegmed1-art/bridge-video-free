from bridge_worker_3_1_free import (
    attach_visual_evidence,
    course_link_candidates,
    master_analysis_payload,
    semantic_episode_plan,
    validate_r24_master,
)


def _segments():
    return [
        {"segment_id": "s1", "start": 0, "end": 4, "text": "Как ты думаешь, какой сделать первый ход?", "speaker": None},
        {"segment_id": "s2", "start": 6, "end": 10, "text": "Я не знаю, мне кажется, надо ходить в пику", "speaker": None},
        {"segment_id": "s3", "start": 12, "end": 17, "text": "Правильно, обрати внимание на длинную масть", "speaker": None},
        {"segment_id": "s4", "start": 19, "end": 23, "text": "Тогда первый ход в пику", "speaker": None},
    ]


def test_r24_builds_learning_cycles_and_nonempty_recommendations():
    episodes = semantic_episode_plan(_segments(), "job")
    shots = [
        {"evidence_id": f"f{i}", "time": i * 3.0, "sha256": f"h{i}"}
        for i in range(20)
    ]
    attach_visual_evidence(episodes, shots)
    links = course_link_candidates(episodes, "Первый ход следует выбирать с учетом длины масти.", "course")
    master = master_analysis_payload(
        job_id="job", passport={}, transcript=_segments(), transcript_qc={}, visual_qc={},
        episodes=episodes, course_links=links, screenshots=shots,
    )
    gate = validate_r24_master(master)
    assert gate["ok"], gate
    assert master["learning_interactions"]
    assert master["student_analysis"]["observations"]
    assert master["recommendations"]
    assert all("требуется уточнение по контексту" not in x["method"] for x in master["teacher_analysis"])


def test_report_screenshots_are_unique_and_bounded():
    segments = [
        {"segment_id": f"s{i}", "start": i * 40, "end": i * 40 + 5,
         "text": "Почему неправильно выбран первый ход?", "speaker": None}
        for i in range(25)
    ]
    episodes = semantic_episode_plan(segments, "job2")
    shots = [{"evidence_id": f"f{i}", "time": i * 10.0, "sha256": f"h{i}"} for i in range(120)]
    attach_visual_evidence(episodes, shots)
    refs = [ref for e in episodes for ref in e["visual_evidence"]]
    assert len(refs) <= 30
    assert len(refs) == len(set(refs))


def test_mojibake_is_repaired_before_canonical_linking():
    episodes = semantic_episode_plan([
        {"segment_id": "s", "start": 0, "end": 4, "text": "первый ход в масть", "speaker": None}
    ], "job3")
    broken = "Первый ход в длинную масть".encode("utf-8").decode("latin1")
    links = course_link_candidates(episodes, broken, "course")
    assert all("Ð" not in (x.get("canonical_excerpt") or "") for x in links)
