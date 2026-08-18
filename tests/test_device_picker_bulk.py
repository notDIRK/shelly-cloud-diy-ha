"""Unit tests for the device picker's bulk action (both flows).

Background: picking "tick all" / "untick all" and pressing Submit does NOT
save — it re-renders the picker with the ticks applied, and only a second,
manual Submit persists it. That is a sound design (an accidental "untick all"
must not silently delete every entity), but the old implementation re-rendered
the *same* step, so the user saw a form identical to the one just submitted
and read it as "saved". It cost a real user their selection.

These tests pin the fix: the re-render is a distinct step (``devices_bulk``,
with its own translated text), it carries the resulting counts, and it still
saves on the next manual submit — for the config flow and the options flow
alike, because the trap existed in both.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.shelly_cloud_diy.config_flow import (
    ShellyCloudDiyConfigFlow,
    ShellyCloudDiyOptionsFlow,
)
from custom_components.shelly_cloud_diy.const import (
    CONF_CREATE_ALL_INITIALLY,
    CONF_ENABLED_DEVICES,
)

DEV_A = "aabbccddeeff"
DEV_B = "112233445566"
DEV_C = "ffeeddccbbaa"
ALL_IDS = [DEV_A, DEV_B, DEV_C]

PENDING = {d: {"status": {}, "online": True, "device_code": "SNSW-001X16EU"} for d in ALL_IDS}


class _Entry:
    def __init__(self, options: dict[str, Any]) -> None:
        self.entry_id = "01TESTENTRY"
        self.data = {"auth_key": "irrelevant", "server_uri": "https://example"}
        self.options = options


def _config_flow() -> ShellyCloudDiyConfigFlow:
    """A config flow far enough along to render the device step."""
    flow = ShellyCloudDiyConfigFlow()
    flow.handler = "shelly_cloud_diy"
    flow.flow_id = "testflow"
    flow._pending_devices = dict(PENDING)
    flow._pending_names = {}
    flow._pending_data = {"auth_key": "irrelevant"}
    flow._pending_options = {"poll_interval": 5}
    return flow


class _StubHass:
    """Just enough of HomeAssistant for ``OptionsFlow.config_entry``.

    Newer Home Assistant resolves that property through
    ``hass.config_entries`` instead of a stored attribute, so the fixture has
    to satisfy both shapes to keep the tests meaningful on the whole
    supported range rather than only on the floor version.
    """

    def __init__(self, entry: "_Entry") -> None:
        self.config_entries = SimpleNamespace(
            async_get_known_entry=lambda entry_id: entry,
            async_get_entry=lambda entry_id: entry,
        )


def _options_flow(options: dict[str, Any] | None = None) -> ShellyCloudDiyOptionsFlow:
    entry = _Entry(
        options if options is not None else {CONF_ENABLED_DEVICES: [DEV_A]}
    )
    flow = ShellyCloudDiyOptionsFlow()
    flow.handler = entry.entry_id
    flow.flow_id = "testflow"
    flow.hass = _StubHass(entry)
    flow._pending_devices = dict(PENDING)
    flow._pending_names = {}
    flow._pending_base_options = {"poll_interval": 5}
    # Pre-2025.12 Home Assistant reads the entry straight off the flow.
    flow._config_entry = entry
    return flow


def _saved(result: dict[str, Any]) -> dict[str, Any]:
    """Return the options as persisted, whichever flow produced the entry.

    The config flow creates the entry and passes the selection alongside its
    data; the options flow has only the one slot. Same content either way.
    """
    return result.get("options") or result["data"]


def _ticked(result: dict[str, Any]) -> list[str]:
    """Return the devices the rendered form has ticked by default."""
    for key in result["data_schema"].schema:
        if str(key) == CONF_ENABLED_DEVICES:
            return list(key.default())
    raise AssertionError("device list not in the form")


# ── The fix: the re-render announces itself ───────────────────────────


@pytest.mark.parametrize("flow_factory", [_config_flow, _options_flow])
@pytest.mark.parametrize(
    ("action", "expected"),
    [("all", ALL_IDS), ("none", [])],
)
def test_bulk_action_rerenders_under_its_own_step(
    flow_factory: Any, action: str, expected: list[str]
) -> None:
    """A bulk action must never look like the form that was just submitted."""
    flow = flow_factory()
    result = asyncio.run(flow.async_step_devices({"bulk_action": action}))

    assert result["type"] == "form"
    assert result["step_id"] == "devices_bulk"
    assert sorted(_ticked(result)) == sorted(expected)


@pytest.mark.parametrize("flow_factory", [_config_flow, _options_flow])
def test_bulk_rerender_reports_both_counts(flow_factory: Any) -> None:
    """The text can only say "3 of 3 ticked" if both numbers are passed."""
    flow = flow_factory()
    result = asyncio.run(flow.async_step_devices({"bulk_action": "all"}))
    assert result["description_placeholders"] == {"total": "3", "selected": "3"}


@pytest.mark.parametrize("flow_factory", [_config_flow, _options_flow])
def test_plain_render_also_reports_counts(flow_factory: Any) -> None:
    """The normal picker shows the same state, so a submit is never blind."""
    flow = flow_factory()
    result = asyncio.run(flow.async_step_devices())
    assert result["step_id"] == "devices"
    assert result["description_placeholders"]["total"] == "3"
    assert "selected" in result["description_placeholders"]


# ── Still saves: the second submit does what it always did ────────────


@pytest.mark.parametrize("flow_factory", [_config_flow, _options_flow])
def test_bulk_step_saves_on_the_next_manual_submit(
    flow_factory: Any,
) -> None:
    """Submitting the re-rendered form persists, via the same handling."""
    flow = flow_factory()
    asyncio.run(flow.async_step_devices({"bulk_action": "all"}))
    result = asyncio.run(
        flow.async_step_devices_bulk(
            {"bulk_action": "manual", CONF_ENABLED_DEVICES: ALL_IDS}
        )
    )

    assert result["type"] == "create_entry"
    assert sorted(_saved(result)[CONF_ENABLED_DEVICES]) == sorted(ALL_IDS)
    # Everything ticked also means "adopt devices that appear later".
    assert _saved(result)[CONF_CREATE_ALL_INITIALLY] is True


@pytest.mark.parametrize("flow_factory", [_config_flow, _options_flow])
def test_manual_submit_of_a_subset_saves_that_subset(
    flow_factory: Any,
) -> None:
    """The ordinary path is untouched by the new step."""
    flow = flow_factory()
    result = asyncio.run(
        flow.async_step_devices(
            {"bulk_action": "manual", CONF_ENABLED_DEVICES: [DEV_B]}
        )
    )
    assert result["type"] == "create_entry"
    assert _saved(result)[CONF_ENABLED_DEVICES] == [DEV_B]
    assert _saved(result)[CONF_CREATE_ALL_INITIALLY] is False


def test_untick_all_then_save_clears_the_selection() -> None:
    """The destructive case still needs two deliberate submits."""
    flow = _options_flow({CONF_ENABLED_DEVICES: ALL_IDS})
    rendered = asyncio.run(flow.async_step_devices({"bulk_action": "none"}))
    assert rendered["step_id"] == "devices_bulk"
    assert _ticked(rendered) == []

    result = asyncio.run(
        flow.async_step_devices_bulk(
            {"bulk_action": "manual", CONF_ENABLED_DEVICES: []}
        )
    )
    assert result["type"] == "create_entry"
    assert _saved(result)[CONF_ENABLED_DEVICES] == []
