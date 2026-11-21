"""Device-registry access that survives HA 2026.11's single-entry devices (#69).

HA 2026.11 made a device belong to exactly one config entry. It deprecated
``DeviceEntry.config_entries`` and ``primary_config_entry`` (removed in
2027.10.0) and mapping access to ``DeviceRegistry.devices`` (removed in
2027.9.0); Fleet-Map logged five warnings for it. Older releases have none of
the replacements, so ``utils/registry`` picks at runtime.

The fake-shape tests pin both branches on any venv. The real-HA tests run
against whatever Home Assistant is installed and assert that no deprecation
is reported, so running them in an HA >= 2026.11 venv checks the new branch
against the real classes.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import attr
import pytest
from homeassistant.helpers import device_registry as dr

from custom_components.shelly_cloud_diy.utils.registry import (
    device_config_entry_ids,
    device_primary_config_entry,
    iter_devices,
)

PACKAGE = Path(__file__).resolve().parents[1] / "custom_components" / "shelly_cloud_diy"


# ── Fake shapes: both branches on every venv ──────────────────────────


def test_old_device_reports_all_its_entries() -> None:
    device = SimpleNamespace(config_entries={"a", "b"}, primary_config_entry="a")
    assert device_config_entry_ids(device) == {"a", "b"}
    assert device_primary_config_entry(device) == "a"


def test_old_device_without_a_primary_entry() -> None:
    device = SimpleNamespace(config_entries={"a"})
    assert device_primary_config_entry(device) is None


def test_new_device_reports_its_single_entry() -> None:
    device = SimpleNamespace(config_entry_id="a")
    assert device_config_entry_ids(device) == {"a"}
    assert device_primary_config_entry(device) == "a"


def test_new_device_never_touches_the_deprecated_properties() -> None:
    class _NewDevice:
        config_entry_id = "a"

        @property
        def config_entries(self):  # noqa: ANN201
            raise AssertionError("deprecated config_entries read")

        @property
        def primary_config_entry(self):  # noqa: ANN201
            raise AssertionError("deprecated primary_config_entry read")

    assert device_config_entry_ids(_NewDevice()) == {"a"}
    assert device_primary_config_entry(_NewDevice()) == "a"


def test_new_device_without_an_entry_is_empty() -> None:
    device = SimpleNamespace(config_entry_id=None)
    assert device_config_entry_ids(device) == set()
    assert device_primary_config_entry(device) is None


def test_mapping_registry_yields_entries_not_ids() -> None:
    first, second = SimpleNamespace(id="d1"), SimpleNamespace(id="d2")
    reg = SimpleNamespace(devices={"d1": first, "d2": second})
    assert iter_devices(reg) == [first, second]


def test_iterable_registry_is_iterated_without_mapping_access() -> None:
    class _View:
        def __init__(self, entries) -> None:  # noqa: ANN001
            self._entries = entries

        def __iter__(self):  # noqa: ANN204
            return iter(self._entries)

        def values(self):  # noqa: ANN201
            raise AssertionError("deprecated mapping access")

    first = SimpleNamespace(id="d1")
    assert iter_devices(SimpleNamespace(devices=_View([first]))) == [first]


# ── Real Home Assistant classes of the installed version ──────────────


@pytest.fixture
def reported(monkeypatch) -> list[str]:  # noqa: ANN001
    """Record every deprecation report the device registry would make."""
    calls: list[str] = []
    if hasattr(dr, "report_usage"):
        monkeypatch.setattr(dr, "report_usage", lambda *a, **k: calls.append(str(a)))
    if hasattr(dr, "_report_deprecated_config_entries_property"):
        monkeypatch.setattr(
            dr,
            "_report_deprecated_config_entries_property",
            lambda *a, **k: calls.append(str(a)),
        )
    return calls


def _real_device(entry_id: str) -> dr.DeviceEntry:
    fields = {a.name for a in attr.fields(dr.DeviceEntry)}
    if "config_entry_id" in fields:
        return dr.DeviceEntry(config_entry_id=entry_id)
    return dr.DeviceEntry(config_entries={entry_id}, primary_config_entry=entry_id)


def test_real_device_entry_resolves_without_deprecation(reported: list[str]) -> None:
    device = _real_device("entry-1")
    assert device_config_entry_ids(device) == {"entry-1"}
    assert device_primary_config_entry(device) == "entry-1"
    assert reported == []


def test_real_registry_items_iterate_without_deprecation(reported: list[str]) -> None:
    device = _real_device("entry-1")
    items = dr.ActiveDeviceRegistryItems()
    items[device.id] = device
    view_cls = getattr(dr, "_DeprecatedDeviceRegistryItemsView", None)
    devices = view_cls(items) if view_cls is not None else items
    assert iter_devices(SimpleNamespace(devices=devices)) == [device]
    assert reported == []


# ── No call site bypasses the helpers ─────────────────────────────────

# Registry access only: ``dev_reg.devices`` in any form, and the entry
# properties on anything named like a device (``device``, ``old_dev``, …).
# ``hass.config_entries`` and the coordinator's own ``devices`` dict are fine.
_DEPRECATED = re.compile(
    r"\bdev_reg\.devices\b|"
    r"\b\w*dev\w*\.(config_entries|config_entries_subentries|primary_config_entry)\b"
)


def test_no_deprecated_registry_access_outside_the_helper() -> None:
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        if path.name == "registry.py" and path.parent.name == "utils":
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if _DEPRECATED.search(code):
                offenders.append(f"{path.relative_to(PACKAGE)}:{number}: {line.strip()}")
    assert offenders == []
