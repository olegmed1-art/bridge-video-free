from bridge_vision.evidence_fusion import fuse_card_evidence

RANKS = "AKQJT98765432"


def declaration(
    card,
    seat,
    *,
    confidence=0.97,
    segment=1,
    role="TEACHER",
    speaker_confidence=0.98,
    verified=True,
):
    return {
        "card": card,
        "seat": seat,
        "confidence": confidence,
        "speaker_role": role,
        "speaker_id": "teacher-0" if role == "TEACHER" else "student-0",
        "speaker_identity_verified": verified,
        "speaker_assignment_confidence": speaker_confidence,
        "evidence_locator": f"transcript.jsonl#segment={segment}",
        "start": float(segment),
        "end": float(segment) + 1.0,
    }


def test_teacher_named_card_completes_third_hand_then_derives_fourth():
    visual = {
        "N": [f"{rank}S" for rank in RANKS],
        "E": [f"{rank}H" for rank in RANKS],
        "S": [f"{rank}D" for rank in RANKS[:-1]],
    }
    result = fuse_card_evidence(visual, [declaration("2D", "S")])

    assert result["status"] == "PASS"
    assert result["accepted_declarations"][0]["card"] == "2D"
    assert len(result["deal"]["hands"]["W"]["cards"]) == 13
    assert result["deal"]["derivations"][0]["provenance_class"] == "DERIVED"
    spoken = next(
        row for row in result["observed_card_evidence"] if row["seat"] == "S" and row["card"] == "2D"
    )
    assert spoken["evidence"][0]["source"] == "TEACHER_SPEECH"


def test_teacher_names_exposed_card_from_hidden_hand_and_other_twelve_are_derived():
    visual = {
        "N": [f"{rank}S" for rank in RANKS],
        "E": [f"{rank}H" for rank in RANKS],
        "S": [f"{rank}D" for rank in RANKS],
    }
    result = fuse_card_evidence(visual, [declaration("AC", "W")])

    derivation = result["deal"]["derivations"][0]
    assert derivation["observed_cards_preserved"] == ["AC"]
    assert len(derivation["computed_cards"]) == 12
    assert result["deal"]["card_provenance"]["W"]["observed_cards"] == ["AC"]


def test_low_confidence_or_unverified_speaker_never_becomes_card_fact():
    result = fuse_card_evidence(
        {"N": ["AS"]},
        [
            declaration("KH", "E", confidence=0.5),
            declaration("QD", "S", role="STUDENT", segment=2, verified=False),
        ],
    )
    assert result["status"] == "REVIEW"
    assert result["deal"]["hands"]["E"]["cards"] == []
    assert result["deal"]["hands"]["S"]["cards"] == []
    assert {row["reason"] for row in result["rejected_declarations"]} == {
        "LOW_CONFIDENCE",
        "UNVERIFIED_SPEAKER",
    }


def test_low_speaker_assignment_confidence_is_rejected():
    result = fuse_card_evidence(
        {},
        [declaration("AS", "N", speaker_confidence=0.60)],
    )

    assert result["status"] == "REVIEW"
    assert result["deal"]["hands"]["N"]["cards"] == []
    assert result["rejected_declarations"][0]["reason"] == "LOW_SPEAKER_CONFIDENCE"


def test_speech_visual_cross_seat_conflict_fails_closed():
    result = fuse_card_evidence({"N": ["AS"]}, [declaration("AS", "W")])
    assert result["status"] == "CONFLICT"
    assert result["deal"] is None
    assert result["conflicts"] == [
        {
            "card": "AS",
            "visual_or_prior_seat": "N",
            "declared_seat": "W",
            "evidence_locator": "transcript.jsonl#segment=1",
        }
    ]
    assert result["canonical_promotion_allowed"] is False


def test_rank_only_teacher_claim_resolves_only_from_complete_canonical_hand():
    visual = {
        "N": [f"{rank}S" for rank in RANKS],
        "E": [f"{rank}H" for rank in RANKS],
        "S": [f"{rank}D" for rank in RANKS],
    }
    result = fuse_card_evidence(visual, [declaration("A", "W")])

    assert result["status"] == "PASS"
    assert result["resolved_partial_declarations"][0]["resolved_card"] == "AC"
    assert result["constraint_evidence"][0]["resolution"] == "UNIQUE_WITHIN_CANONICAL_HAND"
    assert result["deal"]["card_provenance"]["W"]["derived_cards"] == [f"{rank}C" for rank in RANKS]
    assert not any(
        row["seat"] == "W" and row["card"] == "AC"
        for row in result["observed_card_evidence"]
    )


def test_suit_only_teacher_claim_remains_ambiguous_without_exact_card():
    result = fuse_card_evidence({"W": ["AC", "KC"]}, [declaration("C", "W")])

    assert result["status"] == "REVIEW"
    unresolved = result["unresolved_partial_declarations"][0]
    assert unresolved["reason"] == "PARTIAL_CARD_AMBIGUOUS"
    assert unresolved["candidate_cards"] == ["AC", "KC"]
    assert result["constraint_evidence"] == []


def test_partial_teacher_claim_conflicting_with_complete_hand_fails_closed():
    visual = {"N": [f"{rank}S" for rank in RANKS]}
    result = fuse_card_evidence(visual, [declaration("C", "N")])

    assert result["status"] == "CONFLICT"
    assert result["deal"] is None
    assert result["conflicts"][0]["reason"] == "PARTIAL_CARD_CONTRADICTS_COMPLETE_HAND"


def test_exact_teacher_claim_cannot_overfill_a_complete_hand():
    visual = {"N": [f"{rank}S" for rank in RANKS]}
    result = fuse_card_evidence(visual, [declaration("AC", "N")])

    assert result["status"] == "CONFLICT"
    assert result["deal"] is None
    assert result["conflicts"][0]["reason"] == "EXACT_CARD_CONTRADICTS_COMPLETE_HAND"


def test_partial_rank_and_suit_fields_become_exact_card_declaration():
    claim = declaration(None, "E")
    claim.pop("card")
    claim.update({"rank": "10", "suit": "♥"})
    result = fuse_card_evidence({}, [claim])

    assert result["status"] == "PASS"
    assert result["accepted_declarations"][0]["card"] == "TH"
    assert result["deal"]["hands"]["E"]["cards"] == ["TH"]


def test_exact_student_declaration_is_retained_but_never_becomes_observation():
    result = fuse_card_evidence(
        {"N": ["AS"]},
        [declaration("KH", "E", role="STUDENT")],
    )

    assert result["status"] == "REVIEW"
    assert result["deal"]["hands"]["E"]["cards"] == []
    assert result["accepted_declarations"] == []
    suggestion = result["student_speech_suggestions"][0]
    assert suggestion["resolution"] == "UNCONFIRMED_STUDENT_SUGGESTION"
    assert suggestion["provenance_class"] == "STUDENT_SPEECH_SUGGESTION"
    assert suggestion["accepted_as_observation"] is False


def test_student_declaration_cannot_trigger_fourth_hand_derivation():
    visual = {
        "N": [f"{rank}S" for rank in RANKS],
        "E": [f"{rank}H" for rank in RANKS],
        "S": [f"{rank}D" for rank in RANKS[:-1]],
    }
    result = fuse_card_evidence(
        visual,
        [declaration("2D", "S", role="STUDENT")],
    )

    assert result["status"] == "REVIEW"
    assert result["deal"]["hands"]["S"]["unknown_count"] == 1
    assert result["deal"]["hands"]["W"]["unknown_count"] == 13
    assert result["deal"]["derivations"] == []


def test_student_and_layout_may_corroborate_but_still_do_not_add_card():
    layout = {
        "seat": "W",
        "suggested_card": "QD",
        "resolution": "LAYOUT_UNIQUE_SUGGESTION",
        "provenance_class": "LAYOUT_SUGGESTION",
        "accepted_as_observation": False,
    }
    result = fuse_card_evidence(
        {},
        [declaration("QD", "W", role="STUDENT")],
        layout_suggestions=[layout],
    )

    assert result["deal"]["hands"]["W"]["cards"] == []
    assert result["student_speech_suggestions"][0]["resolution"] == (
        "CORROBORATES_LAYOUT_SUGGESTION"
    )
    assert result["speech_layout_corroborations"] == [
        {
            "seat": "W",
            "card": "QD",
            "speech_source": "STUDENT_SPEECH_SUGGESTION",
            "speech_evidence_locator": "transcript.jsonl#segment=1",
            "layout_index": 0,
            "accepted_as_observation": False,
            "speech_declaration_accepted_as_observation": False,
            "layout_accepted_as_observation": False,
        }
    ]


def test_student_contradiction_is_review_signal_not_hard_conflict():
    result = fuse_card_evidence(
        {"N": ["AS"]},
        [declaration("AS", "W", role="STUDENT")],
    )

    assert result["status"] == "REVIEW"
    assert result["conflicts"] == []
    assert result["deal"]["hands"]["N"]["cards"] == ["AS"]
    assert result["student_speech_suggestions"][0]["resolution"] == (
        "CONTRADICTS_ACCEPTED_EVIDENCE"
    )


def test_student_declaration_remains_auditable_when_teacher_causes_hard_conflict():
    result = fuse_card_evidence(
        {"N": ["AS"]},
        [
            declaration("AS", "W"),
            declaration("KH", "E", role="STUDENT", segment=2),
        ],
    )

    assert result["status"] == "CONFLICT"
    suggestion = result["student_speech_suggestions"][0]
    assert suggestion["card"] == "KH"
    assert suggestion["resolution"] == "NOT_EVALUATED_DUE_TO_HARD_CONFLICT"
    assert suggestion["accepted_as_observation"] is False


def test_teacher_speech_and_layout_corroboration_remains_attributable():
    layout = {
        "seat": "W",
        "suggested_card": "QD",
        "resolution": "LAYOUT_UNIQUE_SUGGESTION",
        "provenance_class": "LAYOUT_SUGGESTION",
        "accepted_as_observation": False,
    }
    result = fuse_card_evidence(
        {},
        [declaration("QD", "W")],
        layout_suggestions=[layout],
    )

    assert result["status"] == "PASS"
    assert result["deal"]["hands"]["W"]["cards"] == ["QD"]
    assert result["speech_layout_corroborations"][0]["speech_source"] == "TEACHER_SPEECH"
