"""One entry per cloud key, not per server (#52).

Reported by @ninoflorj: a Shelly account split into several homes has one
Authorization cloud key per home, and every home of the account lives on the
same cloud server. The config entry was tied to the server URI alone, so the
second home was turned away with "already configured" although its key — and
its devices — were different.

The rule now: two entries clash only when both the server and the key are the
same. Entries created before this carry the bare server URI as unique id, so
the duplicate test reads the stored pair and does not rely on the id.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.data_entry_flow import AbortFlow

from custom_components.shelly_cloud_diy import config_flow
from custom_components.shelly_cloud_diy.const import CONF_AUTH_KEY, CONF_SERVER_URI

SERVER = "shelly-42-eu.shelly.cloud"
KEY_HOME_A = "FAKE-AUTH-KEY-home-a-not-a-real-credential"  # noqa: S105
KEY_HOME_B = "FAKE-AUTH-KEY-home-b-not-a-real-credential"  # noqa: S105


def _flow(monkeypatch, existing: list[SimpleNamespace]):
    """A config flow whose account check passes and whose entries are ``existing``."""

    async def _fake_fetch(api: Any):
        return {"aabbccddeeff": {}}, {}

    monkeypatch.setattr(config_flow, "_fetch_devices_and_names", _fake_fetch)
    monkeypatch.setattr(
        config_flow, "async_get_clientsession", lambda hass: SimpleNamespace()
    )

    flow = config_flow.ShellyCloudDiyConfigFlow()
    flow.hass = SimpleNamespace()
    flow.context = {}
    flow._async_current_entries = lambda include_ignore=True: existing

    async def _set_unique_id(unique_id: str, **_: Any) -> None:
        flow.context["unique_id"] = unique_id

    def _abort_if_configured(**_: Any) -> None:
        if any(e.unique_id == flow.context["unique_id"] for e in existing):
            raise AbortFlow("already_configured")

    flow.async_set_unique_id = _set_unique_id
    flow._abort_if_unique_id_configured = _abort_if_configured
    flow.async_step_devices = lambda user_input=None: _done()
    return flow


async def _done() -> dict[str, Any]:
    return {"type": "form", "step_id": "devices"}


def _entry(unique_id: str, key: str) -> SimpleNamespace:
    return SimpleNamespace(
        unique_id=unique_id, data={CONF_SERVER_URI: SERVER, CONF_AUTH_KEY: key}
    )


def _submit(flow, key: str) -> dict[str, Any]:
    async def _run() -> dict[str, Any]:
        try:
            return await flow.async_step_user(
                {CONF_AUTH_KEY: key, CONF_SERVER_URI: SERVER}
            )
        except AbortFlow as err:
            return {"type": "abort", "reason": err.reason}

    return asyncio.run(_run())


def test_a_second_home_with_its_own_key_can_be_added(monkeypatch) -> None:
    existing = [_entry(config_flow._entry_unique_id(SERVER, KEY_HOME_A), KEY_HOME_A)]
    result = _submit(_flow(monkeypatch, existing), KEY_HOME_B)
    assert result["type"] == "form"
    assert result["step_id"] == "devices"


def test_a_second_home_next_to_an_entry_from_before_the_fix(monkeypatch) -> None:
    """Old entries carry the bare server URI as id — they must not block either."""
    existing = [_entry(SERVER, KEY_HOME_A)]
    result = _submit(_flow(monkeypatch, existing), KEY_HOME_B)
    assert result["type"] == "form"


@pytest.mark.parametrize(
    "existing_id",
    [SERVER, config_flow._entry_unique_id(SERVER, KEY_HOME_A)],
    ids=["entry-from-before-the-fix", "entry-from-after-the-fix"],
)
def test_the_same_key_twice_is_still_refused(monkeypatch, existing_id) -> None:
    existing = [_entry(existing_id, KEY_HOME_A)]
    result = _submit(_flow(monkeypatch, existing), KEY_HOME_A)
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


def test_the_unique_id_does_not_contain_the_key() -> None:
    unique_id = config_flow._entry_unique_id(SERVER, KEY_HOME_A)
    assert KEY_HOME_A not in unique_id
    assert unique_id != config_flow._entry_unique_id(SERVER, KEY_HOME_B)
