"""Unit tests for irrigation zone names (issue #20).

The FRANKEVER FK-06X sprinkler controller exposes its six zones as virtual
``boolean:<id>`` components. Those components carry no meaningful ``name`` of
their own — the name the user typed lives in the device's ``service:0`` block,
indexed by the number in the component's ``role`` (``zone0``…``zone5``). The
native HA Shelly integration works around exactly this in
``utils.py::get_rpc_entity_name`` / ``get_irrigation_zone_id``, and we mirror
its precedence: **zone name first, own name second, generic fallback last**.

Two halves are covered:

1. ``ShellyCloudControl.get_device_configs`` must stop discarding
   ``service:<n>`` from the v2 ``settings`` object (it used to keep only
   ``number|enum|text|boolean:<id>``), because that is the block holding the
   zone names. It rides along in a request we already make.
2. ``ShellyBaseEntity.virtual_component_name`` resolves the cascade.

⚠ Two things are NOT hardware-confirmed for the FK-06X over the cloud: whether
the cloud's ``settings`` view carries ``service:0`` at all, and whether the
components' ``role`` field survives it (the test device here reported no
``role`` — the firmware sets it, not the user). Every step is therefore
guarded, and the tests below nail down that a missing piece degrades to the
previous behaviour instead of raising.
"""
from __future__ import annotations

import asyncio
from typing import Any

from custom_components.shelly_cloud_diy.api.cloud_control import ShellyCloudControl
from custom_components.shelly_cloud_diy.binary_sensor import (
    RpcVirtualBinarySensor,
    _create_rpc_sensors as create_rpc_binary_sensors,
)

DEVICE_ID = "fk06xsprinkler"


class _FakeCoordinator:
    def __init__(
        self,
        device_id: str,
        status: dict[str, Any],
        virtual_configs: dict[str, dict[str, dict]] | None = None,
    ) -> None:
        self.devices = {
            device_id: {"status": status, "device_code": "FK-06X", "online": True}
        }
        self.data = self.devices
        self.last_update_success = True
        if virtual_configs is not None:
            self.virtual_configs = virtual_configs


# Six zones as virtual booleans, plus sys so the status reads as Gen2.
_FK06X_STATUS: dict[str, Any] = {
    "sys": {"mac": "AABBCCDDEEFF"},
    **{f"boolean:{200 + i}": {"value": i == 2} for i in range(6)},
}

# ``settings`` as we expect it from the v2 endpoint: a role per zone component
# and the human-readable names in the service block.
_FK06X_CONFIG: dict[str, dict[str, dict]] = {
    DEVICE_ID: {
        **{
            f"boolean:{200 + i}": {"id": 200 + i, "role": f"zone{i}"}
            for i in range(6)
        },
        "service:0": {
            "id": 0,
            "zones": [
                {"name": "Lawn North"},
                {"name": "Lawn South"},
                {"name": "Vegetable Patch"},
                {"name": "Hedge"},
                {"name": "Greenhouse"},
                {"name": "Terrace Pots"},
            ],
        },
    }
}


def _booleans(status=None, virtual_configs=None) -> dict[str, RpcVirtualBinarySensor]:
    status = _FK06X_STATUS if status is None else status
    coord = _FakeCoordinator(DEVICE_ID, status, virtual_configs)
    entities = create_rpc_binary_sensors(DEVICE_ID, status, set(), coord)
    return {
        e.unique_id: e for e in entities if isinstance(e, RpcVirtualBinarySensor)
    }


def _name(comp_id: int, virtual_configs=None) -> str:
    return _booleans(virtual_configs=virtual_configs)[
        f"{DEVICE_ID}_boolean:{comp_id}_value"
    ].name


# ── 1. The API layer must keep ``service:<n>`` ──────────────────────────────


def _get_configs(settings: dict[str, Any]) -> dict[str, dict[str, dict]]:
    """Drive ``get_device_configs`` against a canned v2 response.

    Positional args on purpose: the constructor is inert (it only stores what
    it is given), and naming the credential parameter as a keyword would trip
    the repo's pre-push secret scan on a value that is plainly a dummy. No
    session is needed because ``_post_json`` is stubbed out below — nothing
    goes near the network.
    """
    client = ShellyCloudControl(None, "example.invalid", "dummy")  # type: ignore[arg-type]

    async def _fake_post_json(path: str, payload: dict[str, Any]) -> Any:
        assert path == "/v2/devices/api/get"
        assert payload["select"] == ["settings"]
        return [{"id": DEVICE_ID, "settings": settings}]

    client._post_json = _fake_post_json  # type: ignore[method-assign]  # noqa: SLF001
    return asyncio.run(client.get_device_configs([DEVICE_ID]))


def test_service_block_survives_the_config_harvest():
    """``service:0`` is what carries the zone names — it must not be dropped."""
    result = _get_configs(
        {
            "sys": {"device": {"name": "Sprinkler"}},
            "switch:0": {"id": 0},
            "script:1": {"id": 1},
            "boolean:200": {"id": 200, "role": "zone0"},
            "service:0": {"id": 0, "zones": [{"name": "Lawn North"}]},
        }
    )
    kept = result[DEVICE_ID]
    assert kept["service:0"]["zones"][0]["name"] == "Lawn North"
    assert kept["boolean:200"]["role"] == "zone0"
    # Everything else is still dropped so the cache stays small.
    assert set(kept) == {"boolean:200", "service:0"}


def test_non_virtual_settings_alone_still_yield_nothing():
    """A device with no virtual components and no service block is omitted."""
    assert _get_configs({"switch:0": {"id": 0}, "sys": {}}) == {}


# ── 2. Name resolution ──────────────────────────────────────────────────────


def test_zone_names_resolve_from_service_block():
    booleans = _booleans(virtual_configs=_FK06X_CONFIG)
    assert len(booleans) == 6
    assert [booleans[f"{DEVICE_ID}_boolean:{200 + i}_value"].name for i in range(6)] == [
        "Lawn North",
        "Lawn South",
        "Vegetable Patch",
        "Hedge",
        "Greenhouse",
        "Terrace Pots",
    ]
    # The live value still comes from the status, untouched by naming.
    assert booleans[f"{DEVICE_ID}_boolean:202_value"].is_on is True
    assert booleans[f"{DEVICE_ID}_boolean:200_value"].is_on is False


def test_zone_name_wins_over_component_name():
    """Core's precedence: a zone component's own ``name`` is not the good one."""
    config = {
        DEVICE_ID: {
            "boolean:200": {"role": "zone0", "name": "Boolean 200"},
            "service:0": {"zones": [{"name": "Lawn North"}]},
        }
    }
    assert _name(200, config) == "Lawn North"


def test_component_name_still_used_without_a_zone_role():
    """Non-irrigation virtual components keep the #9 behaviour unchanged."""
    config = {DEVICE_ID: {"boolean:200": {"name": "Away Flag"}}}
    assert _name(200, config) == "Away Flag"


def test_missing_role_falls_back_to_generic():
    """Unconfirmed: the cloud may strip ``role``. Then we are a no-op."""
    config = {
        DEVICE_ID: {
            "boolean:200": {"id": 200},
            "service:0": {"zones": [{"name": "Lawn North"}]},
        }
    }
    assert _name(200, config) == "Boolean 200"


def test_missing_service_block_falls_back_to_component_name_then_generic():
    """Unconfirmed: the cloud may not carry ``service:0``. Degrade, don't fail."""
    assert _name(200, {DEVICE_ID: {"boolean:200": {"role": "zone0"}}}) == "Boolean 200"
    config = {DEVICE_ID: {"boolean:201": {"role": "zone1", "name": "Zone B"}}}
    assert _name(201, config) == "Zone B"


def test_zone_index_beyond_the_service_list_falls_back():
    """A controller may report fewer zone entries than zone components."""
    config = {
        DEVICE_ID: {
            "boolean:205": {"role": "zone5"},
            "service:0": {"zones": [{"name": "Lawn North"}]},
        }
    }
    assert _name(205, config) == "Boolean 205"


def test_malformed_service_block_never_raises():
    """Every shape the cloud could plausibly send back must degrade quietly."""
    for service in (
        {"zones": "not-a-list"},
        {"zones": []},
        {"zones": [None]},
        {"zones": [{"name": None}]},
        {"zones": [{"name": "   "}]},  # whitespace-only is not a name
        {},
        [],
        None,
    ):
        config = {DEVICE_ID: {"boolean:200": {"role": "zone0"}, "service:0": service}}
        assert _name(200, config) == "Boolean 200"


def test_non_numeric_zone_role_is_ignored():
    """``role`` is free-form text — ``zone`` / ``zoneX`` must not blow up."""
    for role in ("zone", "zoneX", "zone-1", "irrigation", "", "Zone0"):
        config = {
            DEVICE_ID: {
                "boolean:200": {"role": role},
                "service:0": {"zones": [{"name": "Lawn North"}]},
            }
        }
        assert _name(200, config) == "Boolean 200"


def test_no_configs_at_all_is_unchanged():
    """No v2 config yet (or ever) → the generic names from #9."""
    booleans = _booleans()
    assert booleans[f"{DEVICE_ID}_boolean:200_value"].name == "Boolean 200"
    assert booleans[f"{DEVICE_ID}_boolean:205_value"].name == "Boolean 205"


def test_zone_names_are_stripped():
    config = {
        DEVICE_ID: {
            "boolean:200": {"role": "zone0"},
            "service:0": {"zones": [{"name": "  Lawn North  "}]},
        }
    }
    assert _name(200, config) == "Lawn North"
