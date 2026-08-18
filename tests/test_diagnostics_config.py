"""Unit tests for the configuration block of the config-entry diagnostics.

The block exists to answer one support question without shell access to the
user's machine: *why does this device have no entities?* That answer lives in
the options, not in the cloud data.

Its hard constraint is that a diagnostics file is something users paste into
public issues, so the tests below pin what must NEVER appear in it: the
``auth_key`` (or anything else out of ``entry.data``) and raw device ids.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from custom_components.shelly_cloud_diy.diagnostics import _config_diagnostics
from custom_components.shelly_cloud_diy.services.fleet_map import fingerprint

# Deliberately NOT named after the option it stands for: the pre-push secret
# scanner matches that identifier followed by "=", and it should keep doing
# so. This value is a plant — the tests assert it can never reach the output.
PLANTED_SECRET = "NOTAREALKEY-0123456789abcdef-shouldneverappear"
DEV_A = "5432044e9768"
DEV_B = "84fce63f8338"


class _Entry:
    """Stands in for a ConfigEntry: only data/options are read."""

    def __init__(self, options: dict[str, Any]) -> None:
        self.entry_id = "01TESTENTRY"
        self.data = {"auth_key": PLANTED_SECRET, "server_uri": "https://example"}
        self.options = options


class _Coordinator:
    """Minimal coordinator with the gating properties diagnostics reads."""

    def __init__(self, entry: _Entry, devices: list[str]) -> None:
        self._entry = entry
        self.devices = {d: {} for d in devices}

    @property
    def _options(self) -> dict[str, Any]:
        return dict(self._entry.options)

    @property
    def create_all_initially(self) -> bool:
        return bool(self._options.get("create_all_initially", False))

    @property
    def offline_after_s(self) -> float:
        return float(self._options.get("offline_after_minutes", 30)) * 60.0

    @property
    def relay_fault_detection(self) -> bool:
        return bool(self._options.get("relay_fault_detection", True))

    def is_enabled(self, device_id: str) -> bool:
        if self.create_all_initially:
            return True
        raw = self._options.get("enabled_devices")
        return device_id in raw if isinstance(raw, list) else True


def _curated() -> tuple[_Entry, _Coordinator]:
    entry = _Entry(
        {
            "poll_interval": 5,
            "offline_after_minutes": 30,
            "create_all_initially": False,
            "enabled_devices": [DEV_B],
            "local_gateway_url": "",
            "relay_fault_detection": True,
        }
    )
    return entry, _Coordinator(entry, [DEV_A, DEV_B])


# ── The constraint that matters most ──────────────────────────────────


def test_no_credential_ever_reaches_the_output() -> None:
    """Nothing from ``entry.data`` may appear, whatever the options say."""
    entry, coord = _curated()
    entry.options["enabled_devices"] = [DEV_A, DEV_B]
    dumped = json.dumps(_config_diagnostics(entry, coord))
    assert PLANTED_SECRET not in dumped
    assert "auth_key" not in dumped


def test_raw_device_ids_are_fingerprinted() -> None:
    """Enabled devices are reported by fingerprint, never by MAC."""
    entry, coord = _curated()
    out = _config_diagnostics(entry, coord)
    dumped = json.dumps(out)
    assert DEV_B not in dumped
    assert out["options"]["enabled_devices"]["fingerprints"] == [
        fingerprint(DEV_B)
    ]


def test_fingerprints_match_the_fleet_map_helper() -> None:
    """Cross-referencing an enabled device to its fleet-map row must work."""
    entry, coord = _curated()
    out = _config_diagnostics(entry, coord)
    assert out["options"]["enabled_devices"]["fingerprints"] == sorted(
        fingerprint(d) for d in [DEV_B]
    )


# ── The support answer ────────────────────────────────────────────────


def test_gated_out_devices_are_counted() -> None:
    """The one number that explains a missing device: it is gated out."""
    entry, coord = _curated()
    out = _config_diagnostics(entry, coord)
    assert out["devices"] == {"in_snapshot": 2, "enabled": 1, "gated_out": 1}


def test_create_all_reports_mode_all_and_no_fingerprints() -> None:
    """With "create all" on, the selection list is not the operative fact."""
    entry, coord = _curated()
    entry.options["create_all_initially"] = True
    out = _config_diagnostics(entry, coord)
    assert out["options"]["enabled_devices"]["mode"] == "all"
    assert out["options"]["enabled_devices"]["fingerprints"] is None
    assert out["devices"]["gated_out"] == 0


def test_effective_values_come_from_the_coordinator() -> None:
    """Reported values are the ones in force, defaults applied."""
    entry, coord = _curated()
    entry.options["offline_after_minutes"] = 10
    out = _config_diagnostics(entry, coord)
    assert out["options"]["offline_after_s"] == 600.0
    assert out["options"]["relay_fault_detection"] is True


# ── Degenerate inputs ─────────────────────────────────────────────────


def test_works_without_a_coordinator() -> None:
    """An entry that never came up is when this is needed most."""
    entry, _ = _curated()
    out = _config_diagnostics(entry, None)
    assert out["devices"] == {"note": "coordinator not ready"}
    assert out["options"]["poll_interval_s"] == 5
    assert out["options"]["offline_after_s"] == 1800
    assert PLANTED_SECRET not in json.dumps(out)


def test_empty_options_fall_back_to_defaults() -> None:
    """A fresh entry carries no options at all."""
    entry = _Entry({})
    out = _config_diagnostics(entry, _Coordinator(entry, [DEV_A]))
    assert out["options"]["poll_interval_s"] == 5
    assert out["options"]["relay_fault_detection"] is True
    assert out["options"]["enabled_devices"]["mode"] == "selection"
    assert out["options"]["enabled_devices"]["selected"] is None
    # No explicit selection means "all" at the gate, so nothing is gated out.
    assert out["devices"]["gated_out"] == 0


@pytest.mark.parametrize(
    ("stored", "expected"),
    [("", False), ("   ", False), ("http://10.0.0.5", True), (None, False), (7, False)],
)
def test_gateway_url_is_reduced_to_presence(stored: Any, expected: bool) -> None:
    """The URL can carry an internal hostname — only its presence is told."""
    entry, coord = _curated()
    entry.options["local_gateway_url"] = stored
    out = _config_diagnostics(entry, coord)
    assert out["options"]["local_gateway_url_set"] is expected
    assert "10.0.0.5" not in json.dumps(out)


def test_unknown_options_are_listed_by_name_only() -> None:
    """A future option must not start leaking values through diagnostics."""
    entry, coord = _curated()
    entry.options["some_future_secret"] = "sensitive-value"
    out = _config_diagnostics(entry, coord)
    assert out["options"]["other_option_keys"] == ["some_future_secret"]
    assert "sensitive-value" not in json.dumps(out)


def test_garbage_in_the_selection_is_survived() -> None:
    """Options are user-editable storage; non-strings must not crash it."""
    entry, coord = _curated()
    entry.options["enabled_devices"] = [DEV_B, None, 42, {"id": DEV_A}]
    out = _config_diagnostics(entry, coord)
    assert out["options"]["enabled_devices"]["selected"] == 1
    assert out["options"]["enabled_devices"]["fingerprints"] == [
        fingerprint(DEV_B)
    ]
