"""Unit tests for the structural Gen2/Gen3 detection (``is_gen2_status``).

Shelly Cloud sends ``_dev_info`` only for BLE-bridged devices, so for every
real Gen2/Gen3 Shelly this structural inference decides whether the device is
routed to the RPC entity builders or to the Gen1 block ones. A misroute means
zero entities, not a degraded set.

Regression guard for the energy meters: a relay-less meter carries nothing but
its ``em`` / ``em1`` components plus a device temperature, so before the meter
components were listed it was recognised only by the incidental
``temperature:0``.
"""
from __future__ import annotations

from custom_components.shelly_cloud_diy.const import device_gen, is_gen2_status


def test_relay_less_three_phase_meter_is_gen2():
    """Shelly Pro 3EM without the incidental ``temperature:0``."""
    assert is_gen2_status({"em:0": {}, "emdata:0": {}, "sys": {}, "cloud": {}}) is True


def test_relay_less_single_phase_meter_is_gen2():
    """2-channel meter shape (Pro EM-50) without its ``switch:0``."""
    assert is_gen2_status({"em1:0": {}, "em1data:0": {}, "cloud": {}}) is True
    assert is_gen2_status({"em1data:1": {}}) is True


def test_classic_component_shapes_still_detected():
    assert is_gen2_status({"switch:0": {}}) is True
    assert is_gen2_status({"temperature:0": {}, "humidity:0": {}}) is True
    assert is_gen2_status({"number:200": {"value": 1}}) is True


def test_gen1_status_is_not_gen2():
    """The bare ``cloud`` key must never count — Gen1 carries one as well."""
    gen1 = {
        "relays": [{"ison": False}],
        "meters": [{"power": 0.0}],
        "emeters": [{"power": 12.0}],
        "cloud": {"enabled": True, "connected": True},
        "sys": {},
        "wifi_sta": {"connected": True},
        "tmp": {"tC": 21.0},
        "temperature": 21.0,
    }
    assert is_gen2_status(gen1) is False
    assert device_gen(gen1) == "G1"


def test_indexless_system_keys_alone_are_not_gen2():
    assert is_gen2_status({"sys": {"mac": "x"}}) is False
    assert is_gen2_status({"cloud": {"connected": True}}) is False
    assert is_gen2_status({}) is False


def test_gen1_emeters_key_does_not_match_the_meter_alternatives():
    """``emeters`` starts with ``em`` — it must not be mistaken for ``em:<id>``."""
    assert is_gen2_status({"emeters": [{"power": 1.0}]}) is False


def test_dev_info_still_wins_for_ble_devices():
    ble = {"temperature:0": {}, "humidity:0": {}, "_dev_info": {"gen": "GBLE"}}
    assert device_gen(ble) == "GBLE"
