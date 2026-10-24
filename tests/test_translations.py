"""All supported languages expose the same form, action and error messages."""

import json
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1] / "custom_components/homeassistantedupage"


def _messages(data, prefix=""):
    result = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(_messages(value, path))
        else:
            result[path] = value
    return result


@pytest.mark.parametrize("language", ["cs", "de", "en", "es", "pl", "sk"])
def test_supported_language_keys_and_placeholders(language):
    reference = _messages(json.loads((ROOT / "strings.json").read_text()))
    actual = _messages(json.loads((ROOT / f"translations/{language}.json").read_text()))
    assert actual.keys() == reference.keys()
    for key, value in reference.items():
        assert actual[key].strip(), key
        assert set(re.findall(r"\{[^{}]+\}", value)) == set(
            re.findall(r"\{[^{}]+\}", actual[key])
        ), key


@pytest.mark.parametrize("language", ["cs", "de", "en", "es", "pl", "sk"])
def test_conversion_option_explains_weighting_numeric_grades(language):
    expected = {
        "cs": "písmenné i číselné známky",
        "de": "Buchstaben- und Zahlennoten",
        "en": "both letter and numeric grades",
        "es": "calificaciones con letras y las numéricas",
        "pl": "ocen literowych, jak i liczbowych",
        "sk": "písmenové aj číselné známky",
    }
    data = json.loads((ROOT / f"translations/{language}.json").read_text())
    hint = data["options"]["step"]["init"]["data_description"]["convert_letter_grades"]
    assert expected[language] in hint
