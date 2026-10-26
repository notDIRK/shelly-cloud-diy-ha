"""Shared reading of a Shelly BLU TRV, as its BLU Gateway Gen3 carries it.

Three platforms build an entity per valve — the climate entity, the
calibration button and the calibration problem sensor — and all three must
agree on the parts a user would notice if they did not:

* **Which valves, in which order.** ``blutrv:<id>`` keys sorted by component
  id, so the first valve is unnumbered everywhere and the second is "2"
  everywhere.
* **The name.** The valve's Shelly-App alias, or its position on the gateway.
* **Where a field comes from.** Flat in ``blutrv:<id>`` (#48) first, then the
  gateway's mirror of the valve's own status in ``blutrv_rstatus:<id>`` (#55).
* **Whether the valve is there at all.** ``connected`` and ``paired`` are the
  valve's own link to the gateway, which stays online and keeps serving the
  last values it saw.

Mixed in *before* :class:`~.base.ShellyBaseEntity`, like the cloud-control
mixin, and relying on it for ``coordinator``, ``device_status`` and
``virtual_component_name``.
"""
from __future__ import annotations

from typing import Any

from ..const import BLUTRV_KEY_RE

#: The ``trv:0`` error the valve sets when its stepper-motor calibration
#: failed: "The TRV will not function properly." (Shelly BLU TRV docs, #58)
BLUTRV_ERROR_NOT_CALIBRATED = "not_calibrated"


def blutrv_keys(status: Any) -> list[str]:
    """The ``blutrv:<id>`` keys of a gateway status, in component-id order."""
    if not isinstance(status, dict):
        return []
    return sorted(
        (
            key
            for key, value in status.items()
            if BLUTRV_KEY_RE.match(key) and isinstance(value, dict)
        ),
        key=lambda key: int(key.split(":", 1)[1]),
    )


class BluTrvValveMixin:
    """One BLU TRV, read off the gateway record of the device it hangs on.

    The entity is attached to the gateway's device, because the valve has no
    cloud identity of its own; the component key is what tells the valves of
    one gateway apart.
    """

    _component_key: str
    _fallback_name: str

    def _init_valve(self, component_key: str, display_index: int) -> None:
        """Remember the valve. ``display_index`` is its position, not its id.

        The ids start at 200 and would make an absurd name.
        """
        self._component_key = component_key
        self._fallback_name = (
            "BLU TRV" if display_index == 0 else f"BLU TRV {display_index + 1}"
        )

    @property
    def valve_name(self) -> str:
        """The valve's Shelly-App alias, or its position on the gateway.

        A property because the alias is resolved by a background task after
        the entity exists — and because it may never resolve, which is why
        the positional fallback stays.

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

    def _valve_present(self) -> bool:
        """Whether the gateway still has the valve on its radio link."""
        component = self._component()
        return (
            bool(component)
            and component.get("connected") is not False
            and component.get("paired") is not False
        )

    def _trv_errors(self) -> tuple[list[Any] | None, str | None]:
        """The valve's ``trv:0`` error list and the status key it came from.

        Same order as every other field: flat in ``blutrv:<id>`` first, then
        ``trv:0`` of the remote status. Only an actual list counts — a missing
        list is "cannot tell", which is not the same as "no errors", and an
        empty one is a real answer.
        """
        for errors, source in (
            (self._component().get("errors"), self._component_key),
            (self._remote("trv:0").get("errors"), self._rstatus_key),
        ):
            if isinstance(errors, list):
                return errors, source
        return None, None


class BluTrvPartMixin(BluTrvValveMixin):
    """A secondary entity of one valve: its name is "<part> <valve name>".

    The part is translated (``translation_key``) and the valve name goes into
    it as the ``{valve}`` placeholder. Resolved on every read instead of
    through the base class's cached ``name``, because the alias arrives after
    the entity exists. The entity id is built from the untranslated template,
    as Home Assistant does for every other entity, so it does not change with
    the user's language.
    """

    #: English name template, used until the platform translations are loaded.
    _default_name_template = "{valve}"

    def _platform_translations(self, object_id: bool) -> dict[str, str]:
        # ``platform_data`` on current Home Assistant, ``platform`` on older.
        holder = getattr(self, "platform_data", None) or getattr(self, "platform", None)
        attribute = (
            "object_id_platform_translations" if object_id else "platform_translations"
        )
        translations = getattr(holder, attribute, None)
        return translations if isinstance(translations, dict) else {}

    def _part_name(self, translations: dict[str, str]) -> str:
        template = self._default_name_template
        if translations and getattr(self, "translation_key", None):
            template = translations.get(self._name_translation_key, template)
        try:
            return template.format(valve=self.valve_name)
        except (KeyError, IndexError, ValueError):
            return self._default_name_template.format(valve=self.valve_name)

    @property
    def name(self) -> str:
        """The translated part, filled with the valve's current name."""
        return self._part_name(self._platform_translations(object_id=False))

    @property
    def suggested_object_id(self) -> str:
        """Same name, built from the language-independent translations."""
        return self._part_name(self._platform_translations(object_id=True))
