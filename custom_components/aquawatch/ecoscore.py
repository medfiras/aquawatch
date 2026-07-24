"""Eco-score calculation for AquaWatch, based on ADEME reference thresholds."""

from __future__ import annotations

_EXCELLENT_L_PER_DAY_PER_PERSON = 80.0
_AVERAGE_L_PER_DAY_PER_PERSON = 150.0

_GRADE_THRESHOLDS: tuple[tuple[int, str], ...] = (
    (90, "A"),
    (75, "B"),
    (55, "C"),
    (35, "D"),
    (0, "E"),
)

_TIPS_BY_GRADE = {
    "A": "Excellent ! Continuez ainsi.",
    "B": "Bonne consommation, encore quelques efforts pour l'excellence.",
    "C": "Consommation moyenne : pensez aux mousseurs de robinet et à limiter les bains.",
    "D": "Consommation élevée : vérifiez les fuites et privilégiez les douches courtes.",
    "E": "Consommation très élevée : faites vérifier votre installation pour d'éventuelles fuites.",
}


def compute_eco_score(
    avg_liters_per_day: float, household_size: int
) -> tuple[int, str, str]:
    """Return (score 0-100, grade A-E, tip) for a household's daily consumption."""
    effective_household_size = household_size if household_size > 0 else 1
    liters_per_person = avg_liters_per_day / effective_household_size

    if liters_per_person <= _EXCELLENT_L_PER_DAY_PER_PERSON:
        score = 100
    elif liters_per_person >= _AVERAGE_L_PER_DAY_PER_PERSON:
        overage = liters_per_person - _AVERAGE_L_PER_DAY_PER_PERSON
        score = max(0, 50 - round(overage / _AVERAGE_L_PER_DAY_PER_PERSON * 50))
    else:
        span = _AVERAGE_L_PER_DAY_PER_PERSON - _EXCELLENT_L_PER_DAY_PER_PERSON
        position = liters_per_person - _EXCELLENT_L_PER_DAY_PER_PERSON
        score = round(100 - (position / span) * 50)

    grade = next(g for threshold, g in _GRADE_THRESHOLDS if score >= threshold)
    return score, grade, _TIPS_BY_GRADE[grade]
