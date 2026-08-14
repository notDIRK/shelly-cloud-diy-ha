"""The device picker must stay submittable when a saved device vanishes (#25).

Reported by @Frido1980. The picker's permitted values came purely from the
current ``/device/all_status`` response, while the pre-ticked selection came
from what the user saved earlier. When a saved device was missing from that
response, its id was no longer an allowed value — so Home Assistant rejected
the ENTIRE form with ``value must be one of [...]``, and a newly bought Shelly
sitting right there in the list could not be added either.

Being merely offline does not cause this: offline devices are still listed, with
a ⚠ prefix. The device has to disappear from the cloud listing altogether, which
is what happened to the reporter (his device had also gone offline in the Shelly
app itself).

These tests drive the real option builder and the real voluptuous schema, so the
selector's validation is what decides — not a reimplementation of it.
"""
from __future__ import annotations

from typing import Any

import pytest
import voluptuous as vol
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from custom_components.shelly_cloud_diy.config_flow import _build_device_options

PRESENT = "aabbccddee01"
GONE = "aabbccddee99"
NEW = "aabbccddee02"

DEVICES: dict[str, dict[str, Any]] = {
    PRESENT: {"code": "SNSW-001X16EU", "cloud": {"connected": True}},
    NEW: {"code": "SNSW-001X16EU", "cloud": {"connected": True}},
}
NAMES = {PRESENT: "Hallway", NEW: "Garage", GONE: "Shed Pump"}


def _validate(options, selection):
    """Run a selection through the same selector the form uses."""
    schema = vol.Schema(
        {
            vol.Optional("enabled_devices"): SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            )
        }
    )
    return schema({"enabled_devices": selection})


def test_reproduces_the_reported_failure_without_keep_ids():
    """Without the fix the saved-but-missing id is rejected — the actual bug."""
    options = _build_device_options(DEVICES, NAMES)
    with pytest.raises(vol.Invalid):
        _validate(options, [PRESENT, GONE])


def test_form_stays_submittable_with_keep_ids():
    """With the saved ids passed in, the same selection validates."""
    options = _build_device_options(DEVICES, NAMES, keep_ids=[PRESENT, GONE])
    result = _validate(options, [PRESENT, GONE])
    assert set(result["enabled_devices"]) == {PRESENT, GONE}


def test_the_new_device_can_now_be_added():
    """The reporter's actual goal: add a new device while a saved one is gone."""
    options = _build_device_options(DEVICES, NAMES, keep_ids=[PRESENT, GONE])
    result = _validate(options, [PRESENT, GONE, NEW])
    assert NEW in result["enabled_devices"]


def test_missing_device_can_be_deliberately_dropped():
    """Unticking it must be allowed — that is how the user gets rid of it."""
    options = _build_device_options(DEVICES, NAMES, keep_ids=[PRESENT, GONE])
    result = _validate(options, [PRESENT])
    assert result["enabled_devices"] == [PRESENT]


def test_missing_device_is_labelled_and_sorted_last():
    options = _build_device_options(DEVICES, NAMES, keep_ids=[PRESENT, GONE])
    assert options[-1]["value"] == GONE
    label = options[-1]["label"]
    assert "Shed Pump" in label          # keep the name the user knows
    assert GONE in label                 # and the id, for identification
    assert "not in the current cloud listing" in label
    assert "untick to remove" in label   # say what to do about it


def test_present_devices_are_untouched():
    """The normal fleet must render exactly as before — no duplicates, no churn."""
    plain = _build_device_options(DEVICES, NAMES)
    kept = _build_device_options(DEVICES, NAMES, keep_ids=[PRESENT, GONE])
    assert kept[: len(plain)] == plain
    assert len(kept) == len(plain) + 1


def test_no_duplicate_when_saved_device_is_present():
    """A saved id that IS in the listing must not be added a second time."""
    options = _build_device_options(DEVICES, NAMES, keep_ids=[PRESENT, NEW])
    values = [o["value"] for o in options]
    assert len(values) == len(set(values)) == 2


def test_keep_ids_tolerates_junk():
    """Stored options are user-editable JSON; malformed entries must not raise."""
    options = _build_device_options(
        DEVICES, NAMES, keep_ids=[PRESENT, None, 42, "", GONE]  # type: ignore[list-item]
    )
    values = [o["value"] for o in options]
    assert GONE in values
    assert None not in values and 42 not in values


def test_unnamed_missing_device_still_gets_a_label():
    """No stored name (never renamed in the app) must not produce a blank label."""
    options = _build_device_options(DEVICES, {}, keep_ids=[GONE])
    assert GONE in options[-1]["label"]
    assert options[-1]["label"].strip() != ""
