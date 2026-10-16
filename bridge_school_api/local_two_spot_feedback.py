"""Teacher-only feedback for the loopback synthetic two-position practice."""

from __future__ import annotations


FEEDBACK = {
    "spot.a7c2": {
        "first_play": "DA",
        "guaranteed": 1,
        "model": (
            "Из пяти видимых бубен четыре у защиты уже сыграны, ещё четыре по условию лежат 2–2. "
            "На ♦A и ♦K оба защитника дважды дают бубну; со стола уходят две из ♦7/♦6/♦5. "
            "Юг затем передаёт ход ♥2 к ♥A стола, и оставшаяся бубновая фоска берёт взятку. "
            "Если потратить этот вход первым, гарантии взятки фоской уже нет."
        ),
    },
    "spot.f3b8": {
        "first_play": "CA",
        "guaranteed": 0,
        "model": (
            "После ♣A и ♣K при данном 2–2 у защиты треф не останется, а у Севера "
            "сохранится одна из ♣7/♣6/♣5. Но ход остаётся у Юга. ♥A и ♦A находятся "
            "у Юга, а малые карты этих мастей — на столе: собственного входа к трефовой "
            "фоске нет. Возможную помощь защиты нельзя считать гарантией."
        ),
    },
}


def feedback_for_answer(value: object) -> dict[str, object]:
    if type(value) is not dict or set(value) != {"exercise_id", "first_play", "guaranteed", "explanation"}:
        raise ValueError("INVALID_TWO_SPOT_ANSWER")
    exercise_id = value["exercise_id"]
    if type(exercise_id) is not str or exercise_id not in FEEDBACK:
        raise ValueError("UNKNOWN_TWO_SPOT_EXERCISE")
    if (
        type(value["first_play"]) is not str
        or value["first_play"] not in ({"DA", "H2"} if exercise_id == "spot.a7c2" else {"CA", "HA"})
        or type(value["guaranteed"]) is not int
        or value["guaranteed"] not in (0, 1)
        or type(value["explanation"]) is not str
        or not value["explanation"].strip()
        or len(value["explanation"]) > 2000
    ):
        raise ValueError("INCOMPLETE_TWO_SPOT_ANSWER")
    key = FEEDBACK[exercise_id]
    return {
        "exercise_id": exercise_id,
        "choice_and_count_match": value["first_play"] == key["first_play"] and value["guaranteed"] == key["guaranteed"],
        "model": key["model"],
        "explanation_review": "Сравните своё объяснение с эталоном; понимание должен подтвердить преподаватель.",
        "mastery_recorded": False,
    }
