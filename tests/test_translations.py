"""Every translation has the same keys and placeholders as the English one."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

DIR = Path(__file__).parent.parent / "custom_components" / "eberspaecher" / "translations"
LANGUAGES = sorted(p.stem for p in DIR.glob("*.json") if p.stem != "en")


def flatten(data, prefix=""):
    for key, value in data.items():
        if isinstance(value, dict):
            yield from flatten(value, f"{prefix}{key}.")
        else:
            yield f"{prefix}{key}", value


def load(code):
    return dict(flatten(json.loads((DIR / f"{code}.json").read_text(encoding="utf-8"))))


def test_the_expected_languages_exist():
    assert {"de", "sv", "fi"} <= set(LANGUAGES)


def test_strings_json_matches_english():
    strings = json.loads((DIR.parent / "strings.json").read_text(encoding="utf-8"))
    assert dict(flatten(strings)) == load("en")


@pytest.mark.parametrize("code", LANGUAGES)
def test_same_keys_and_placeholders(code):
    english, other = load("en"), load(code)
    assert other.keys() == english.keys()
    for key, text in english.items():
        assert other[key].strip(), f"{code}: {key} is empty"
        assert set(re.findall(r"{(\w+)}", other[key])) == set(re.findall(r"{(\w+)}", text)), (
            f"{code}: placeholders differ in {key}"
        )
