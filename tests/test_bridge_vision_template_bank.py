from bridge_vision.template_bank import build_template_bank


def test_bank_accepts_stable_label_and_omits_unstable_label():
    a = [[1, 0], [1, 1]]
    bad = [[0, 1], [0, 0]]
    templates, evidence = build_template_bank({"A": [a, bad, a], "Q": [a, bad]}, min_pair_iou=.90)
    assert templates == {"A": [[True, False], [True, True]]}
    assert evidence["support"]["A"] == 2
    assert evidence["rejected_labels"]["Q"] == "UNSTABLE"


def test_bank_never_derives_unlabelled_templates():
    templates, evidence = build_template_bank({"HEART": [[[1]], [[1]]]}, min_pair_iou=1.0)
    assert set(templates) == {"HEART"}
    assert evidence["accepted_labels"] == ["HEART"]
