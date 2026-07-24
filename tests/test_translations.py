"""tests/test_translations.py"""

import json
from pathlib import Path

from custom_components.aquawatch.binary_sensor import BINARY_SENSOR_DESCRIPTIONS
from custom_components.aquawatch.const import (
    SERVICE_EXPORT_CSV,
    SERVICE_FORCE_REFRESH,
    SERVICE_RECALIBRATE_BASELINE,
)
from custom_components.aquawatch.sensor import SENSOR_DESCRIPTIONS

_COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "aquawatch"


def _load(name: str) -> dict:
    path = (
        _COMPONENT_DIR / "translations" / name
        if name != "strings.json"
        else _COMPONENT_DIR / name
    )
    return json.loads(path.read_text())


def test_strings_and_en_are_identical_keys() -> None:
    strings = _load("strings.json")
    en = _load("en.json")
    assert strings.keys() == en.keys()
    assert strings["entity"].keys() == en["entity"].keys()


def test_fr_has_same_top_level_keys_as_strings() -> None:
    strings = _load("strings.json")
    fr = _load("fr.json")
    assert strings.keys() == fr.keys()


def test_all_sensor_keys_have_translations() -> None:
    fr = _load("fr.json")
    for description in SENSOR_DESCRIPTIONS:
        assert description.translation_key in fr["entity"]["sensor"]


def test_all_binary_sensor_keys_have_translations() -> None:
    fr = _load("fr.json")
    for description in BINARY_SENSOR_DESCRIPTIONS:
        assert description.translation_key in fr["entity"]["binary_sensor"]


def test_all_services_have_translations() -> None:
    fr = _load("fr.json")
    for service in (
        SERVICE_FORCE_REFRESH,
        SERVICE_EXPORT_CSV,
        SERVICE_RECALIBRATE_BASELINE,
    ):
        assert service in fr["services"]


def test_config_flow_steps_have_translations() -> None:
    fr = _load("fr.json")
    for step in ("user", "credentials", "contract", "reauth_confirm"):
        assert step in fr["config"]["step"]
    for error in ("invalid_auth", "cannot_connect", "no_contracts", "provider_unavailable"):
        assert error in fr["config"]["error"]


def test_scraping_broken_issue_has_translation() -> None:
    fr = _load("fr.json")
    assert "scraping_broken" in fr["issues"]
