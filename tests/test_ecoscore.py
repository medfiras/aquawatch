"""tests/test_ecoscore.py"""

import pytest

from custom_components.aquawatch.ecoscore import compute_eco_score


def test_excellent_consumption_scores_100_grade_a() -> None:
    score, grade, tip = compute_eco_score(avg_liters_per_day=80.0, household_size=1)
    assert score == 100
    assert grade == "A"
    assert tip


def test_average_french_consumption_scores_50_grade_d() -> None:
    score, grade, tip = compute_eco_score(avg_liters_per_day=150.0, household_size=1)
    assert score == 50
    assert grade == "D"


def test_double_average_consumption_scores_0_grade_e() -> None:
    score, grade, tip = compute_eco_score(avg_liters_per_day=300.0, household_size=1)
    assert score == 0
    assert grade == "E"


def test_intermediate_consumption_scores_75_grade_b() -> None:
    score, grade, tip = compute_eco_score(avg_liters_per_day=115.0, household_size=1)
    assert score == 75
    assert grade == "B"


def test_household_size_divides_consumption() -> None:
    # 320 L/day for 4 people = 80 L/person/day = excellent
    score, grade, tip = compute_eco_score(avg_liters_per_day=320.0, household_size=4)
    assert score == 100
    assert grade == "A"


def test_zero_household_size_defaults_to_one_person() -> None:
    score, grade, tip = compute_eco_score(avg_liters_per_day=80.0, household_size=0)
    assert score == 100
    assert grade == "A"


@pytest.mark.parametrize(
    ("liters", "expected_grade"),
    [(80.0, "A"), (115.0, "B"), (150.0, "D"), (300.0, "E")],
)
def test_tip_matches_grade(liters: float, expected_grade: str) -> None:
    _, grade, tip = compute_eco_score(avg_liters_per_day=liters, household_size=1)
    assert grade == expected_grade
    assert isinstance(tip, str) and tip
