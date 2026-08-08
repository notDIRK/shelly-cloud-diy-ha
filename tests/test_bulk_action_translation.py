"""Regression guard for the bulk-action selector translation (issue #18).

@elad-eyal reported the device picker showing German radio-button labels in an
otherwise English UI on first install. Cause: the three options were built with
hard-coded ``label=`` literals in ``config_flow.py``, which bypasses Home
Assistant's translation lookup entirely — every user saw German regardless of
their language.

The fix routes the labels through ``translation_key="bulk_action"`` and the
``selector`` block of the translation files. These tests pin both halves: that
the source no longer carries literal labels, and that every translation file
actually supplies the keys the selector will ask for.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "shelly_cloud_diy"
CONFIG_FLOW = COMPONENT / "config_flow.py"

TRANSLATION_FILES = [
    COMPONENT / "strings.json",
    COMPONENT / "translations" / "en.json",
    COMPONENT / "translations" / "de.json",
]

# The values the selector submits. Every translation file must cover exactly
# these — no more (dead entry), no fewer (untranslated option).
EXPECTED_OPTIONS = {"manual", "all", "none"}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", TRANSLATION_FILES, ids=lambda p: p.name)
def test_selector_block_covers_every_option(path: Path):
    data = _load(path)
    options = data["selector"]["bulk_action"]["options"]
    assert set(options) == EXPECTED_OPTIONS
    for key, text in options.items():
        assert text.strip(), f"{path.name}: option {key!r} is empty"


def test_en_and_de_agree_on_the_key_set():
    """A language added to one file only reintroduces the bug for the other."""
    en = _load(COMPONENT / "translations" / "en.json")["selector"]
    de = _load(COMPONENT / "translations" / "de.json")["selector"]
    assert en.keys() == de.keys()
    for selector_key in en:
        assert en[selector_key]["options"].keys() == de[selector_key]["options"].keys()


def test_strings_json_matches_english_translation():
    """strings.json is the source of truth HA falls back to — keep it in sync."""
    strings = _load(COMPONENT / "strings.json")["selector"]
    en = _load(COMPONENT / "translations" / "en.json")["selector"]
    assert strings == en


def test_english_and_german_actually_differ():
    """Guards against copying the German text into en.json to 'fix' the report."""
    en = _load(COMPONENT / "translations" / "en.json")["selector"]["bulk_action"]["options"]
    de = _load(COMPONENT / "translations" / "de.json")["selector"]["bulk_action"]["options"]
    for key in EXPECTED_OPTIONS:
        assert en[key] != de[key], f"option {key!r} is identical in en and de"


def test_config_flow_carries_no_hardcoded_labels():
    source = CONFIG_FLOW.read_text(encoding="utf-8")
    for literal in ("Manuelle Auswahl", "Alle Geräte anhaken", "Alle Geräte abwählen"):
        assert literal not in source, f"hard-coded label {literal!r} is back"


def test_both_flows_use_the_translation_key():
    """The config flow and the options flow each render this picker."""
    source = CONFIG_FLOW.read_text(encoding="utf-8")
    assert source.count('translation_key="bulk_action"') == 2
