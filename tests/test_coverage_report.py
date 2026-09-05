"""Unit tests for the payload-coverage block of the device diagnostics.

The block answers one question: *which parts of this device's payload produce
no entity at all?* It is builder-derived on purpose. A report built from the
description tables would have called ``energyReturned`` (#38), ``flood:<id>``
(#41), Gen1 ``flood`` / ``smoke`` (#42) and ``wifi.rssi`` covered — all four
had a finished description and no builder — so it would have found none of
the four bugs it exists to find.

Two properties matter more than the numbers:

* it must never raise. A user downloads this from the device page to attach
  to a bug report, and a broken coverage block must not take the raw status
  down with it.
* it must report key names and never values, because it sits directly beside
  a redacted dump.
"""
from __future__ import annotations

from typing import Any

from custom_components.shelly_cloud_diy import binary_sensor as binary_sensor_platform
from custom_components.shelly_cloud_diy import diagnostics
from custom_components.shelly_cloud_diy import sensor as sensor_platform
from custom_components.shelly_cloud_diy.diagnostics import (
    STRUCTURAL_STATUS_KEYS,
    _coverage_diagnostics,
)

DEVICE_ID = "a0dd6cffee01"


class _FakeCoordinator:
    """Minimal coordinator: entities only read ``.devices[id]['status']``."""

    def __init__(self, status: dict[str, Any]) -> None:
        self.devices = {
            DEVICE_ID: {"status": status, "device_code": "SNSW-001X16EU", "online": True}
        }
        self.data = self.devices
        self.last_update_success = True


def _covered_gen2_status() -> dict[str, Any]:
    """A Gen2 payload in which every non-structural key becomes an entity.

    ``switch:0`` via the power sensor, ``cloud`` via the Cloud binary sensor,
    ``wifi`` via the RSSI sensor wired alongside this report. The remaining
    keys are the status envelope and must land in ``ignored_keys``.
    """
    return {
        "switch:0": {"id": 0, "output": True, "apower": 12.3},
        "cloud": {"connected": True},
        "wifi": {"rssi": -55},
        "id": 0,
        "serial": 17,
        "ts": 1756000000.0,
        "code": "SNSW-001X16EU",
        "_updated": "2026-09-04 10:00:00",
        "_dev_info": {"gen": "G2"},
    }


def _ble_status() -> dict[str, Any]:
    """A gateway-bridged payload with three surfaced keys and two that nothing reads."""
    return {
        "_dev_info": {"gen": "GBLE"},
        "temperature:0": {"id": 0, "tC": 21.4},
        "devicepower:0": {"id": 0, "battery": {"percent": 100, "V": None}},
        "input:0": {"id": 0, "state": None, "percent": None, "errors": []},
        "reporter": {"id": "146221729481748", "rssi": -71},
        "packetid:0": {"id": 0, "packetid": 42},
    }


def _report(status: dict[str, Any]) -> dict[str, Any]:
    return _coverage_diagnostics(_FakeCoordinator(status), DEVICE_ID, status)


def test_unknown_component_type_is_reported() -> None:
    """A component we have no builder for must show up, not vanish silently.

    This is the case idea 3 of the neighbour-ideas plan is about: today an
    unrecognised ``<type>:<id>`` produces nothing and says nothing, so the
    only evidence is a device with fewer entities than the user expected.
    """
    status = _covered_gen2_status()
    status["quantum:0"] = {"id": 0, "spin": "up"}
    status["telemetry"] = {"whatever": 1}

    report = _report(status)

    assert "quantum:0" in report["uncovered_keys"]
    assert "telemetry" in report["uncovered_keys"]
    assert report["uncovered_count"] == 2


def test_fully_covered_device_reports_nothing_uncovered() -> None:
    """No false alarms: a payload we fully surface must come back empty."""
    report = _report(_covered_gen2_status())

    assert report["uncovered_keys"] == []
    assert report["uncovered_count"] == 0
    assert report["generation"] == "G2"
    assert set(report["covered_keys"]) == {"switch:0", "cloud", "wifi"}
    assert set(report["ignored_keys"]) == set(STRUCTURAL_STATUS_KEYS)


def test_wifi_rssi_counts_as_coverage() -> None:
    """Pins the join the report is meant to prove.

    Before the RSSI descriptions were wired, ``wifi`` was a key on 35 of 35
    Gen2+ devices that produced no entity — exactly what this block is for.
    """
    status = _covered_gen2_status()
    assert "wifi" in _report(status)["covered_keys"]

    del status["wifi"]["rssi"]
    assert "wifi" in _report(status)["uncovered_keys"]


def test_a_raising_builder_does_not_propagate(monkeypatch) -> None:
    """One broken builder must cost its half of the report, nothing more."""

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("builder exploded")

    monkeypatch.setattr(sensor_platform, "_create_rpc_sensors", boom)

    report = _report(_covered_gen2_status())

    assert "error" not in report
    assert report["builder_errors"] == ["sensor.rpc: RuntimeError"]
    # The binary-sensor half still ran, so its keys are still accounted for.
    assert "cloud" in report["covered_keys"]


def test_both_builders_raising_still_returns_a_report(monkeypatch) -> None:
    """The degenerate case: nothing built, and still a usable answer."""

    def boom(*args: Any, **kwargs: Any) -> None:
        raise ValueError("nope")

    monkeypatch.setattr(sensor_platform, "_create_rpc_sensors", boom)
    monkeypatch.setattr(binary_sensor_platform, "_create_rpc_sensors", boom)

    report = _report(_covered_gen2_status())

    assert report["entities_built"] == 0
    assert len(report["builder_errors"]) == 2
    assert "switch:0" in report["uncovered_keys"]


def test_a_failure_outside_the_builders_degrades_to_an_error(monkeypatch) -> None:
    """Anything unforeseen must still leave the rest of diagnostics intact."""

    def boom(_status: dict[str, Any]) -> str:
        raise TypeError("classification exploded")

    monkeypatch.setattr(diagnostics, "device_gen", boom)

    report = _report(_covered_gen2_status())

    assert report == {"error": "TypeError: classification exploded"}


def test_empty_status_is_not_an_error() -> None:
    """A device we have never had a payload for is a note, not a failure."""
    assert _report({}) == {"note": "no status in the current snapshot"}


def test_ble_device_is_measured_against_the_ble_builders() -> None:
    """BLU devices take the other dispatch branch and must be judged there.

    ``input:0`` on a BLU button is a real, previously invisible gap: it is
    absent from the BLE tables, so no entity is created for it — which is
    precisely what the report should say.
    """
    status = _ble_status()

    report = _report(status)

    assert report["generation"] == "GBLE"
    assert set(report["covered_keys"]) == {
        "temperature:0",
        "devicepower:0",
        "reporter",
    }
    assert report["uncovered_keys"] == ["input:0", "packetid:0"]


def test_reporter_counts_as_coverage_only_while_it_reads() -> None:
    """The BLU counterpart of the Wi-Fi case above.

    ``reporter`` was the largest measured gap on the BLU side — present on
    29 of 29 gateway-bridged devices and surfaced by nothing. It is covered
    by the gateway signal sensor now, but only when the gateway actually
    reports a reading: no entity is built for an unusable one, so the key
    must go back to being a gap rather than silently counting as covered.
    """
    status = _ble_status()
    assert "reporter" in _report(status)["covered_keys"]

    status["reporter"]["rssi"] = None
    assert "reporter" in _report(status)["uncovered_keys"]


def test_no_status_value_ever_reaches_the_report() -> None:
    """Redaction discipline: key names only.

    The block sits next to a dump that strips ``mac`` / ``ssid`` / ``ip``,
    and a coverage report that echoed values would be a way straight around
    that.
    """
    status = _covered_gen2_status()
    status["wifi"] = {"rssi": -55, "ssid": "SECRET-NETWORK", "sta_ip": "10.9.9.9"}
    status["sys"] = {"mac": "A0DD6CFFEE01"}

    rendered = repr(_report(status))

    for planted in ("SECRET-NETWORK", "10.9.9.9", "A0DD6CFFEE01", "SNSW-001X16EU"):
        assert planted not in rendered
