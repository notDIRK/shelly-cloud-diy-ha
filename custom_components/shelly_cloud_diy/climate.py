"""Climate platform for Shelly Cloud DIY.

One entity kind lives here: a **Shelly BLU TRV** seen through the Shelly BLU
Gateway Gen3 that carries it. The valve itself speaks Bluetooth and has no
cloud identity, so everything about it — reading and writing — happens on the
gateway's record.

Reading needs nothing new: everything arrives in the ordinary poll, but in
one of two shapes, and the entity reads both:

- **Flat** — ``current_C``, ``target_C`` and ``pos`` directly in the gateway
  component ``blutrv:<id>``. Seen on @gerok1984's gateway (#48).
- **Remote status only** — ``blutrv:<id>`` without those fields; the valve's
  own status mirrored by the gateway in ``blutrv_rstatus:<id>``, as
  ``status.trv:0.{current_C,target_C,pos}`` with the internal sensor in
  ``status.temperature:0.tC``. Seen on a BLU Gateway Gen3 with a valve on
  firmware v1.5.0 (#55).

Per field the flat value wins when it is a usable reading, and the remote
status is the fallback. The ``bthomesensor:<id>`` components the gateway also
carries are deliberately not used: nothing in the payload ties one of them to
a particular valve. Writing goes over the opt-in cloud relay, which is why the
setpoint appears only once cloud control is connected and the relay has said
it will route to this gateway — exactly like the virtual booleans in
:mod:`switch`. Without it the entity is still useful: it reads the room
temperature and the valve's own setpoint, it just cannot change it.

Both shapes confirmed against real diagnostics extracts: a gateway with two
valves contributed by @gerok1984 (#48), and the remote-status-only gateway
from #55.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    BLUTRV_KEY_RE,
    BLUTRV_MAX_TEMP_C,
    BLUTRV_MIN_TEMP_C,
    DOMAIN,
    SIGNAL_DEVICE_REMOVED,
    plausible_temperature,
)
from .coordinator import ShellyCloudCoordinator, SIGNAL_NEW_DEVICE
from .entities.base import ShellyBaseEntity


def _create_climate_entities(
    device_id: str,
    status: dict[str, Any],
    created: set[str],
    coordinator: ShellyCloudCoordinator,
) -> list[ClimateEntity]:
    """Build one climate entity per BLU TRV this gateway carries.

    Same signature as the sensor and binary-sensor builders so it can be
    driven directly from a test and by the coverage report in
    :mod:`diagnostics`, which runs it to learn whether
    ``blutrv_rstatus:<id>`` is being read (#55).
    """
    entities: list[ClimateEntity] = []
    if not coordinator.is_enabled(device_id):
        return entities
    if not isinstance(status, dict):
        return entities

    keys = sorted(
        (
            key
            for key, value in status.items()
            if BLUTRV_KEY_RE.match(key) and isinstance(value, dict)
        ),
        key=lambda key: int(key.split(":", 1)[1]),
    )

    for display_index, key in enumerate(keys):
        unique_id = f"{device_id}_{key}_climate"
        if unique_id in created:
            continue
        created.add(unique_id)
        entities.append(
            ShellyBluTrvClimate(coordinator, device_id, key, display_index)
        )
    return entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Shelly Cloud DIY climate entities."""
    coordinator: ShellyCloudCoordinator = hass.data[DOMAIN][entry.entry_id]
    created: set[str] = set()

    @callback
    def async_add_device(device_id: str) -> None:
        status = coordinator.devices.get(device_id, {}).get("status", {})
        if entities := _create_climate_entities(
            device_id, status, created, coordinator
        ):
            async_add_entities(entities)

    @callback
    def async_forget_device(device_id: str) -> None:
        for unique_id in [uid for uid in created if uid.startswith(f"{device_id}_")]:
            created.discard(unique_id)

    entities: list[ClimateEntity] = []
    for device_id in list(coordinator.devices.keys()):
        status = coordinator.devices.get(device_id, {}).get("status", {})
        entities.extend(
            _create_climate_entities(device_id, status, created, coordinator)
        )
    if entities:
        async_add_entities(entities)

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, async_add_device)
    )
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_DEVICE_REMOVED, async_forget_device)
    )


class ShellyBluTrvClimate(ShellyBaseEntity, ClimateEntity):
    """A Shelly BLU TRV, reached through its BLU Gateway Gen3."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT]
    _attr_hvac_mode = HVACMode.HEAT
    _attr_min_temp = BLUTRV_MIN_TEMP_C
    _attr_max_temp = BLUTRV_MAX_TEMP_C
    _attr_target_temperature_step = 0.5

    def __init__(
        self,
        coordinator: ShellyCloudCoordinator,
        device_id: str,
        component_key: str,
        display_index: int,
    ) -> None:
        """Initialize the valve.

        ``display_index`` is the position among this gateway's valves, not
        the component id: the ids start at 200 and would make an absurd
        entity name.
        """
        super().__init__(coordinator, device_id, display_index)
        self._component_key = component_key
        self._attr_unique_id = f"{device_id}_{component_key}_climate"
        self._fallback_name = (
            "BLU TRV" if display_index == 0 else f"BLU TRV {display_index + 1}"
        )

    @property
    def name(self) -> str:
        """The valve's Shelly-App alias, or its position on the gateway.

        A property rather than a fixed name because the alias is resolved by
        a background task after the entity exists — and because it may never
        resolve, which is why the positional fallback stays.

        Two roads to the name were measured and found closed: the valve's own
        v2 config answers ``name: null``, and so does the
        ``bthomedevice:<id>`` it points at. The one that works is the
        account's alias listing, where each valve has a child record of its
        own — resolved in the coordinator, which is the side that makes that
        request anyway. (#48)
        """
        aliases = getattr(self.coordinator, "blu_trv_names", None)
        if isinstance(aliases, dict):
            gateway = aliases.get(self._device_id)
            if isinstance(gateway, dict):
                alias = gateway.get(self._component_key)
                if isinstance(alias, str) and alias.strip():
                    return alias.strip()
        return self.virtual_component_name(self._component_key) or self._fallback_name

    def _component(self) -> dict[str, Any]:
        value = self.device_status.get(self._component_key)
        return value if isinstance(value, dict) else {}

    @property
    def _rstatus_key(self) -> str:
        """The gateway's mirror of this valve's own status (#55)."""
        return f"blutrv_rstatus:{self._component_key.split(':', 1)[1]}"

    def _remote(self, component: str) -> dict[str, Any]:
        """One component of the valve's own status, as the gateway mirrors it."""
        rstatus = self.device_status.get(self._rstatus_key)
        if not isinstance(rstatus, dict):
            return {}
        status = rstatus.get("status")
        if not isinstance(status, dict):
            return {}
        value = status.get(component)
        return value if isinstance(value, dict) else {}

    def _remote_internal_temperature(self) -> Any:
        """``temperature:0.tC``, unless the component reports an error.

        In the #48 snapshot that component carries ``errors: ["disabled"]``
        and reads two degrees off ``current_C``, so a flagged reading is
        treated as none rather than as a room temperature.
        """
        sensor = self._remote("temperature:0")
        if sensor.get("errors"):
            return None
        return sensor.get("tC")

    @staticmethod
    def _as_celsius(value: Any) -> float | None:
        """A temperature, or None if the value is no reading.

        Below absolute zero counts as no reading, as on every temperature
        sensor (#51) — a placeholder must not reach the history as a value.
        """
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        value = plausible_temperature(value)
        return None if value is None else float(value)

    def _celsius(self, field: str) -> tuple[float | None, str | None]:
        """A temperature field of the valve and the status key it came from.

        Flat in ``blutrv:<id>`` first (#48), then ``trv:0`` of the remote
        status (#55). Each source is judged on its own, so a placeholder in
        the flat field falls through to the remote one instead of hiding it.
        For the room temperature the internal sensor is the last resort.
        """
        if (value := self._as_celsius(self._component().get(field))) is not None:
            return value, self._component_key
        if (value := self._as_celsius(self._remote("trv:0").get(field))) is not None:
            return value, self._rstatus_key
        if field == "current_C" and (
            value := self._as_celsius(self._remote_internal_temperature())
        ) is not None:
            return value, self._rstatus_key
        return None, None

    def _position(self) -> tuple[int | float | None, str | None]:
        """How far the valve is open, in percent, and where that came from."""
        for value, source in (
            (self._component().get("pos"), self._component_key),
            (self._remote("trv:0").get("pos"), self._rstatus_key),
        ):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return value, source
        return None, None

    @property
    def _fallback_source_keys(self) -> set[str]:
        """Status keys beyond ``blutrv:<id>`` that currently supply a value.

        Read by the coverage report. ``blutrv_rstatus:<id>`` counts only
        while it actually fills a field the flat component leaves empty — in
        the #48 shape it is present but unused, and calling it covered there
        would hide exactly the kind of gap the report exists to find.
        """
        sources = {
            self._celsius("current_C")[1],
            self._celsius("target_C")[1],
            self._position()[1],
        }
        return {key for key in sources if key and key != self._component_key}

    @property
    def available(self) -> bool:
        """Available while the gateway is reporting and the valve is on it.

        ``connected`` is the valve's own radio link. The gateway stays online
        and keeps serving the last values it saw, so a valve that dropped off
        would otherwise present a stale temperature as a live one.
        """
        component = self._component()
        return (
            bool(component)
            and component.get("connected") is not False
            and component.get("paired") is not False
            and super().available
        )

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Offer the setpoint only when the relay can actually carry it."""
        if self.coordinator.cloud_control_connected and (
            self.coordinator.is_cloud_controllable(self._device_id)
        ):
            return ClimateEntityFeature.TARGET_TEMPERATURE
        return ClimateEntityFeature(0)

    @property
    def current_temperature(self) -> float | None:
        return self._celsius("current_C")[0]

    @property
    def target_temperature(self) -> float | None:
        return self._celsius("target_C")[0]

    @property
    def hvac_action(self) -> HVACAction:
        """Derive heating from the valve position, the only signal there is.

        ``pos`` is how far the valve is open, in percent. A BLU TRV reports
        no flow and no demand flag, so anything above closed is the honest
        answer to "is this radiator being fed".
        """
        pos, _ = self._position()
        if pos is not None and pos > 0:
            return HVACAction.HEATING
        return HVACAction.IDLE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Valve-specific readings with no entity of their own.

        Battery and signal are deliberately absent: those are sensors, built
        by the sensor platform from the same component, so that they land in
        the recorder and on a dashboard like every other device's.
        """
        component = self._component()
        attrs: dict[str, Any] = {}
        pos, _ = self._position()
        if pos is not None:
            attrs["valve_position"] = pos
        if isinstance(component.get("last_updated_ts"), (int, float)):
            attrs["last_updated_ts"] = component["last_updated_ts"]
        if isinstance(component.get("fw_ver"), str):
            attrs["firmware_version"] = component["fw_ver"]
        return attrs

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Send a new setpoint to the valve, via the gateway."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if not isinstance(temperature, (int, float)):
            return
        await self.coordinator.async_set_blutrv_target(
            self._device_id, self._component_key, float(temperature)
        )
