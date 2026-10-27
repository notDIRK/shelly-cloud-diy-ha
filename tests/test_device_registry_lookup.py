"""Device lookups go through the per-entry registry API where HA has it (#59).

HA 2026.10 deprecated ``DeviceRegistry.async_get_device`` and removes it in
2027.8.0. The replacement only exists from 2026.10 on, so the integration has
to pick at runtime; these tests pin both branches with fake registries, since
neither test venv carries the new API.
"""
from __future__ import annotations

from types import SimpleNamespace

from custom_components.shelly_cloud_diy.const import DOMAIN
from custom_components.shelly_cloud_diy.utils.registry import async_get_own_device


class _OldRegistry:
    """A registry from before 2026.10: only the global lookup exists."""

    def __init__(self, device) -> None:  # noqa: ANN001
        self.device = device
        self.calls: list[set] = []

    def async_get_device(self, identifiers=None):  # noqa: ANN001
        self.calls.append(identifiers)
        return self.device


class _NewRegistry(_OldRegistry):
    """A registry from 2026.10 on: the per-entry lookup exists too."""

    def __init__(self, devices: dict) -> None:  # noqa: ANN001
        super().__init__(None)
        self.devices = devices
        self.by_identifier_calls: list[tuple] = []

    def async_get_device_by_identifier(self, identifier, config_entry_id):  # noqa: ANN001
        self.by_identifier_calls.append((identifier, config_entry_id))
        return self.devices.get((identifier, config_entry_id))


def test_new_registry_is_asked_per_entry_and_never_globally() -> None:
    mine = SimpleNamespace(id="dev-mine")
    reg = _NewRegistry({((DOMAIN, "abc123"), "entry-a"): mine})

    assert async_get_own_device(reg, "abc123", "entry-a") is mine
    assert reg.by_identifier_calls == [((DOMAIN, "abc123"), "entry-a")]
    assert reg.calls == []


def test_device_shared_into_another_account_is_not_found_for_this_one() -> None:
    other = SimpleNamespace(id="dev-other")
    reg = _NewRegistry({((DOMAIN, "abc123"), "entry-b"): other})

    assert async_get_own_device(reg, "abc123", "entry-a") is None
    assert reg.calls == []


def test_old_registry_falls_back_to_the_global_lookup() -> None:
    device = SimpleNamespace(id="dev-1")
    reg = _OldRegistry(device)

    assert async_get_own_device(reg, "abc123", "entry-a") is device
    assert reg.calls == [{(DOMAIN, "abc123")}]


def test_source_has_no_direct_async_get_device_call_left() -> None:
    """Every lookup has to go through the helper, or HA logs the warning again."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "custom_components" / DOMAIN
    offenders = [
        f"{path.relative_to(root)}:{n}"
        for path in root.rglob("*.py")
        if path.name != "registry.py"
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if ".async_get_device(" in line
    ]
    assert offenders == []
