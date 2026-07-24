"""tests/test_packaging.py"""

import json
from pathlib import Path

import yaml

_ROOT = Path(__file__).parent.parent


def _add_home_assistant_yaml_constructors() -> None:
    """Add custom YAML constructors for Home Assistant tags."""
    yaml.SafeLoader.add_constructor("!input", lambda loader, node: f"!input {node.value}")
    yaml.SafeLoader.add_constructor("!secret", lambda loader, node: f"!secret {node.value}")


def test_blueprint_is_valid_yaml_and_references_recalibrate_service() -> None:
    _add_home_assistant_yaml_constructors()
    path = _ROOT / "blueprints" / "automation" / "aquawatch" / "leak_notification.yaml"
    content = path.read_text()
    parsed = yaml.safe_load(content)
    assert parsed["blueprint"]["domain"] == "automation"
    assert "aquawatch.recalibrate_baseline" in content


def test_lovelace_example_is_valid_yaml() -> None:
    path = _ROOT / "examples" / "lovelace_dashboard.yaml"
    parsed = yaml.safe_load(path.read_text())
    assert parsed["views"][0]["cards"]


def test_readme_exists_and_mentions_sedif() -> None:
    path = _ROOT / "README.md"
    content = path.read_text()
    assert "SEDIF" in content
    assert "HACS" in content


def test_manifest_and_hacs_json_are_valid() -> None:
    manifest = json.loads(
        (_ROOT / "custom_components" / "aquawatch" / "manifest.json").read_text()
    )
    hacs = json.loads((_ROOT / "hacs.json").read_text())
    assert manifest["domain"] == "aquawatch"
    assert hacs["name"] == "AquaWatch"
