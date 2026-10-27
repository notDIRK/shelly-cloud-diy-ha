"""BLU TRV valve names, resolved from the account's alias listing (#48).

v0.13.0 shipped the valves as "BLU TRV" / "BLU TRV 2", because nothing in any
payload carried the name the user typed in the Shelly app. Two measurements by
[@gerok1984](https://github.com/gerok1984) on a real BLU Gateway Gen3 with two
valves changed that, and both are the reason this file exists:

* **Negative, 2026-09-27:** the valve's own v2 config returns ``name: null``,
  and so does the ``bthomedevice:<id>`` it points at. That road is closed, and
  the config fetch stays scoped as it is.
* **Positive, 2026-09-28:** the aliases are in ``/interface/device/list`` —
  the listing this integration already requests for device names. Each valve
  appears there as a **gateway child record** of its own, carrying the BLE
  address of the valve and the alias. He implemented the match in his fork and
  confirmed on that hardware that Home Assistant then shows "Salón 1" and
  "Salón 2".

The records below are his, with the aliases and the gateway id kept and
nothing else changed. What is **not** confirmed here is which field of
``blutrv_rinfo:<id>.device_info`` carries the valve's BLE address — his
sanitised snapshot did not include it and his implementation reads ``mac`` or
``id``. So the resolver accepts either spelling plus ``addr``, and each is
covered below; the day a verbatim block turns up, the extras can go.
"""
from __future__ import annotations

from typing import Any

from custom_components.shelly_cloud_diy.coordinator import resolve_blu_trv_aliases

GATEWAY_ID = "34cdb077c740"

# ``/interface/device/list`` — the gateway itself plus one child record per
# valve. ``channel`` is the cloud's own child numbering (2200 / 2201), which
# is deliberately NOT what the match is built on: the reporter established
# that the address is the reliable identity and the numbering is not.
DEVICE_LIST_RECORDS: dict[str, dict[str, Any]] = {
    GATEWAY_ID: {"id": GATEWAY_ID, "name": "Gateway Salón", "type": "S3GW-1DBT001"},
    f"{GATEWAY_ID}_2200": {
        "type": "SBTR-001AEU",
        "category": "thermostat",
        "gen": 3,
        "channel": 2200,
        "mode": "gateway",
        "addr": "f8:44:77:28:bf:0a",
        "name": "Salón 1",
    },
    f"{GATEWAY_ID}_2201": {
        "type": "SBTR-001AEU",
        "category": "thermostat",
        "gen": 3,
        "channel": 2201,
        "mode": "gateway",
        "addr": "f8:44:77:28:e0:e6",
        "name": "Salón 2",
    },
}


def _status(*, field: str = "mac") -> dict[str, Any]:
    """A gateway status with both valves and their remote-info blocks."""
    return {
        "sys": {"mac": "34CDB077C740"},
        "blutrv:200": {"id": 200, "connected": True, "target_C": 21},
        "blutrv:201": {"id": 201, "connected": True, "target_C": 21},
        "blutrv_rinfo:200": {
            "v": 39,
            "device_info": {
                "app": "BluTRV",
                "model": "BluTRV",
                "ver": "1.5.0",
                field: "f8:44:77:28:bf:0a",
            },
        },
        "blutrv_rinfo:201": {
            "v": 39,
            "device_info": {
                "app": "BluTRV",
                "model": "BluTRV",
                "ver": "1.5.0",
                field: "F8:44:77:28:E0:E6",
            },
        },
    }


def test_each_valve_gets_the_alias_of_the_child_record_with_its_address() -> None:
    aliases = resolve_blu_trv_aliases({GATEWAY_ID: _status()}, DEVICE_LIST_RECORDS)

    assert aliases == {
        GATEWAY_ID: {"blutrv:200": "Salón 1", "blutrv:201": "Salón 2"}
    }


def test_the_address_is_matched_whatever_it_is_spelled_and_cased() -> None:
    """Upper case, no separators, and the two field names his code reads."""
    for field in ("mac", "id", "addr"):
        aliases = resolve_blu_trv_aliases(
            {GATEWAY_ID: _status(field=field)}, DEVICE_LIST_RECORDS
        )
        assert aliases[GATEWAY_ID]["blutrv:201"] == "Salón 2", field

    compact = _status()
    compact["blutrv_rinfo:200"]["device_info"]["mac"] = "F84477 28BF0A"
    aliases = resolve_blu_trv_aliases({GATEWAY_ID: compact}, DEVICE_LIST_RECORDS)
    assert aliases[GATEWAY_ID]["blutrv:200"] == "Salón 1"


def test_a_valve_whose_address_is_in_no_record_gets_no_alias() -> None:
    """Silence, not a guess — the positional fallback is the better answer."""
    status = _status()
    status["blutrv_rinfo:200"]["device_info"]["mac"] = "aa:bb:cc:dd:ee:ff"

    aliases = resolve_blu_trv_aliases({GATEWAY_ID: status}, DEVICE_LIST_RECORDS)

    assert aliases == {GATEWAY_ID: {"blutrv:201": "Salón 2"}}


def test_nothing_is_invented_from_the_channel_number() -> None:
    """``2200`` is the cloud's child channel and looks like valve 200.

    Deriving the alias from that arithmetic is the obvious shortcut and the
    reporter specifically measured that it is not reliable, so a record
    without a usable address must not be matched by its number.
    """
    records = {
        key: {k: v for k, v in record.items() if k != "addr"}
        for key, record in DEVICE_LIST_RECORDS.items()
    }

    assert resolve_blu_trv_aliases({GATEWAY_ID: _status()}, records) == {}


def test_a_gateway_without_remote_info_yields_nothing() -> None:
    """The valve's address comes from ``blutrv_rinfo``; no block, no match."""
    status = {"sys": {"mac": "34CDB077C740"}, "blutrv:200": {"id": 200}}

    assert resolve_blu_trv_aliases({GATEWAY_ID: status}, DEVICE_LIST_RECORDS) == {}


def test_a_record_without_a_name_does_not_produce_an_empty_alias() -> None:
    records = dict(DEVICE_LIST_RECORDS)
    records[f"{GATEWAY_ID}_2200"] = dict(records[f"{GATEWAY_ID}_2200"], name="   ")

    aliases = resolve_blu_trv_aliases({GATEWAY_ID: _status()}, records)

    assert aliases == {GATEWAY_ID: {"blutrv:201": "Salón 2"}}


def test_a_malformed_listing_is_survived() -> None:
    """The listing is remote data; every shape in it has to be tolerated."""
    assert resolve_blu_trv_aliases({GATEWAY_ID: _status()}, {}) == {}
    assert resolve_blu_trv_aliases({}, DEVICE_LIST_RECORDS) == {}
    assert resolve_blu_trv_aliases(
        {GATEWAY_ID: _status()}, {"x": None, "y": {"addr": None, "name": 7}}  # type: ignore[dict-item]
    ) == {}


# ── The entity end of it ──────────────────────────────────────────────


class _FakeCoordinator:
    """The slice of the coordinator a climate entity reads for its name."""

    def __init__(self, aliases: dict[str, dict[str, str]] | None = None) -> None:
        self.devices = {GATEWAY_ID: {"status": _status(), "online": True}}
        self.data = self.devices
        self.last_update_success = True
        self.virtual_configs: dict[str, dict[str, dict]] = {}
        self.cloud_control_connected = True
        if aliases is not None:
            self.blu_trv_names = aliases

    def is_enabled(self, device_id: str) -> bool:
        return True

    def is_cloud_controllable(self, device_id: str) -> bool:
        return False


def _valve_names(aliases: dict[str, dict[str, str]] | None) -> list[str]:
    from custom_components.shelly_cloud_diy import climate as climate_platform

    coordinator = _FakeCoordinator(aliases)
    status = coordinator.devices[GATEWAY_ID]["status"]
    entities = climate_platform._create_climate_entities(
        GATEWAY_ID, status, set(), coordinator
    )
    return [entity.name for entity in entities]


def test_the_climate_entities_show_the_resolved_aliases() -> None:
    assert _valve_names(
        {GATEWAY_ID: {"blutrv:200": "Salón 1", "blutrv:201": "Salón 2"}}
    ) == ["Salón 1", "Salón 2"]


def test_a_valve_without_an_alias_keeps_its_positional_name() -> None:
    """The alias arrives from a background task, so it may never arrive.

    Half a set is the interesting case: one named valve must not shift the
    other one's fallback number.
    """
    assert _valve_names({GATEWAY_ID: {"blutrv:201": "Salón 2"}}) == [
        "BLU TRV",
        "Salón 2",
    ]
    assert _valve_names(None) == ["BLU TRV", "BLU TRV 2"]


# ── The seam between the two: one request, two results ────────────────


def test_one_alias_request_fills_both_the_device_names_and_the_valve_aliases(
    monkeypatch,
) -> None:
    """Drive the real refresh, because the wiring is where this can rot.

    The resolver and the entity are tested above; what this covers is that
    the coordinator actually asks for the **raw** records (the child records
    are not in the requested ids, so an id-filtered lookup would drop them)
    and files both results.
    """
    import asyncio
    from types import SimpleNamespace

    from custom_components.shelly_cloud_diy import coordinator as coordinator_module
    from custom_components.shelly_cloud_diy.coordinator import ShellyCloudCoordinator

    monkeypatch.setattr(coordinator_module, "_V2_NAME_LOOKUP_GAP_S", 0)
    monkeypatch.setattr(
        coordinator_module.dr,
        "async_get",
        lambda hass: SimpleNamespace(async_get_device=lambda identifiers: None),
    )

    calls: list[str] = []

    class _Api:
        async def get_device_records(self) -> dict[str, Any]:
            calls.append("records")
            return DEVICE_LIST_RECORDS

        async def get_device_names(self, ids=None):  # pragma: no cover
            raise AssertionError("the id-filtered lookup drops the child records")

    coordinator = object.__new__(ShellyCloudCoordinator)
    coordinator._entry = SimpleNamespace(entry_id="entry-test")
    coordinator._api = _Api()
    coordinator.devices = {GATEWAY_ID: {"status": _status(), "online": True}}
    coordinator.data = coordinator.devices
    coordinator.device_names = {}
    coordinator.blu_trv_names = {}
    coordinator._names_attempted = set()
    coordinator._name_lookup_in_flight = True
    coordinator.hass = SimpleNamespace()
    coordinator.async_update_listeners = lambda: None

    asyncio.run(coordinator._refresh_device_names([GATEWAY_ID]))

    assert calls == ["records"], "one request, not one per result"
    assert coordinator.device_names == {GATEWAY_ID: "Gateway Salón"}
    assert coordinator.devices[GATEWAY_ID]["name"] == "Gateway Salón"
    assert coordinator.blu_trv_names == {
        GATEWAY_ID: {"blutrv:200": "Salón 1", "blutrv:201": "Salón 2"}
    }
    assert coordinator._name_lookup_in_flight is False
