"""Guard: every entity description is actually reachable from a builder.

Four bugs in one month had the same shape. A description sat complete in one
of the tables in ``entities/descriptions.py`` — right name, right device
class, right unit — and no builder in ``sensor.py`` or ``binary_sensor.py``
ever looked its key up. The device reported the value, the integration read
the payload, and the entity was never created:

* ``("emeter", "energyReturned")`` — a Shelly 3EM owner got the consumption
  half of the meter and not the feed-in half (#38).
* ``flood:<id>`` — a Shelly Flood Gen4 arrived with diagnostics and without
  its alarm (#41).
* Gen1 ``flood`` / ``smoke`` — the same, for the Gen1 leaf sensors (#42).
* ``wifi.rssi`` / ``wifi_sta.rssi`` — signal strength, present on 35 of 35
  Gen2+ devices in the recorded account snapshot, never surfaced.

Not one of them was findable by reading either file alone: the table looks
complete, and the builder looks complete. Only the *join* is broken, and
nothing in Python checks a join that is expressed as a dict lookup.

So this test performs the join statically. It parses both platform modules
with ``ast`` — the same approach ``test_coordinator_init.py`` takes, and for
the same reason: the failure is structural, so a static read catches it
without a running Home Assistant — collects every key literal those modules
can ever look up, and asserts that no table entry is left over.

A lookup whose argument cannot be resolved to literals (``BLE_SENSORS`` is
indexed by whatever component type the payload happens to carry) marks the
whole table as dynamically reached, because in that case any key genuinely
can be hit.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from custom_components.shelly_cloud_diy.entities import descriptions

# The description tables and the two modules allowed to consume them.
TABLES = (
    "RPC_SENSORS",
    "BLOCK_SENSORS",
    "RPC_BINARY_SENSORS",
    "BLOCK_BINARY_SENSORS",
    "BLE_SENSORS",
    "BLE_BINARY_SENSORS",
)

PLATFORM_MODULES = ("sensor.py", "binary_sensor.py")

COMPONENT_DIR = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "shelly_cloud_diy"
)

# Entries that exist in a table and that NO builder reaches, knowingly.
#
# This is a backlog, not a design. Every line below is a reading a device
# could be publishing today and that we do not surface — it is here because
# wiring it up would rest on a shape nobody in this project has ever measured,
# and lesson #32 is explicit that a fix must not smuggle in a second,
# unevidenced assumption. Shrinking this list is the goal; growing it needs a
# reason written down next to the entry.
_INTENTIONALLY_UNWIRED: dict[str, set[Any]] = {
    # Gen2+ ``illuminance:<id>.lux``. A real gap, and the only Gen2 one left.
    # It is not wired because no Gen2+ device in any payload recorded from a
    # real account carries an ``illuminance:<id>`` component — every one of
    # the 24 illuminance readings in the snapshot belongs to a BLU device and
    # is served by ``BLE_SENSORS["illuminance"]`` instead. Wiring the Gen2
    # path would be an unmeasured guess shipped alongside a measured fix.
    "RPC_SENSORS": {"illuminance"},
    # Gen1 Block sensors. NO Gen1 device appears in any recorded payload —
    # the snapshot holds 35 Gen2+ and 29 BLU devices and zero Gen1 — so every
    # one of these would be wired against the vendor documentation alone.
    # ``wifi_sta.rssi`` was wired from that documentation deliberately and in
    # isolation; doing the same for eighteen more shapes in one change is the
    # bulk guesswork #32 warns about. They need a Gen1 payload first.
    "BLOCK_SENSORS": {
        ("adc", "adc"),
        ("device", "battery"),
        ("device", "deviceTemp"),
        ("device", "energy"),
        ("device", "power"),
        # Reactive power on the Gen1 3EM. Left out of the #38 fix on purpose,
        # for exactly this reason, and still unmeasured.
        ("emeter", "reactive"),
        ("light", "energy"),
        ("light", "power"),
        ("relay", "energy"),
        ("roller", "rollerEnergy"),
        ("roller", "rollerPower"),
        ("sensor", "extTemp"),
        ("sensor", "gas"),
        ("sensor", "humidity"),
        ("sensor", "luminosity"),
        ("sensor", "selfTest"),
        ("sensor", "tilt"),
        ("valve", "valve"),
    },
    # Gen1 top-level fault flags, same generation problem as above. These are
    # the closest remaining relatives of the ``flood`` / ``smoke`` flags fixed
    # in #42 and should be the first thing wired once a Gen1 payload exists.
    "BLOCK_BINARY_SENSORS": {"overpower", "overtemperature", "vibration"},
}


def _literal(node: ast.AST) -> Any:
    """Return the str / tuple-of-str a node denotes, else ``None``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Tuple) and all(
        isinstance(e, ast.Constant) and isinstance(e.value, str)
        for e in node.elts
    ):
        return tuple(e.value for e in node.elts)
    return None


def _loop_bound_literals(tree: ast.AST) -> dict[str, set[Any]]:
    """Map loop variables to the literals a literal iterable can bind them to.

    Both platform modules drive their lookups from tables written inline, e.g.
    ``for attr, desc_key in [("apower", "pm1_apower"), …]`` followed by
    ``RPC_SENSORS.get(desc_key)``. The key at the lookup site is a variable,
    so resolving it means reading the loop that binds it.
    """
    bound: dict[str, set[Any]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.comprehension)):
            continue
        if not isinstance(node.iter, (ast.List, ast.Tuple)):
            continue

        if isinstance(node.target, ast.Name):
            for element in node.iter.elts:
                value = _literal(element)
                if value is not None:
                    bound.setdefault(node.target.id, set()).add(value)
        elif isinstance(node.target, ast.Tuple):
            names = [
                e.id if isinstance(e, ast.Name) else None
                for e in node.target.elts
            ]
            for element in node.iter.elts:
                if not isinstance(element, ast.Tuple):
                    continue
                for position, sub in enumerate(element.elts):
                    if position >= len(names) or names[position] is None:
                        continue
                    value = _literal(sub)
                    if value is not None:
                        bound.setdefault(names[position], set()).add(value)
    return bound


def _collect(path: Path) -> tuple[dict[str, set[Any]], set[str]]:
    """Return (keys looked up per table, tables reached dynamically)."""
    tree = ast.parse(path.read_text())
    bound = _loop_bound_literals(tree)
    looked_up: dict[str, set[Any]] = {name: set() for name in TABLES}
    dynamic: set[str] = set()

    def resolve(table: str, node: ast.AST) -> None:
        value = _literal(node)
        if value is not None:
            looked_up[table].add(value)
            return
        if isinstance(node, ast.Name) and node.id in bound:
            looked_up[table] |= bound[node.id]
            return
        # An unresolvable key means the table is indexed by whatever the
        # payload carries, so every entry in it is genuinely reachable.
        dynamic.add(table)

    for node in ast.walk(tree):
        # TABLE.get(key)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in looked_up
            and node.args
        ):
            resolve(node.func.value.id, node.args[0])
        # TABLE[key]
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id in looked_up
        ):
            resolve(node.value.id, node.slice)
        # key in TABLE
        elif (
            isinstance(node, ast.Compare)
            and len(node.ops) == 1
            and isinstance(node.ops[0], ast.In)
            and isinstance(node.comparators[0], ast.Name)
            and node.comparators[0].id in looked_up
        ):
            resolve(node.comparators[0].id, node.left)
        # for … in TABLE / TABLE.items() / TABLE.keys()
        elif isinstance(node, (ast.For, ast.comprehension)):
            iterated = node.iter
            if isinstance(iterated, ast.Name) and iterated.id in looked_up:
                dynamic.add(iterated.id)
            elif (
                isinstance(iterated, ast.Call)
                and isinstance(iterated.func, ast.Attribute)
                and isinstance(iterated.func.value, ast.Name)
                and iterated.func.value.id in looked_up
            ):
                dynamic.add(iterated.func.value.id)

    return looked_up, dynamic


def _reachable_keys() -> tuple[dict[str, set[Any]], set[str]]:
    """Join both platform modules into one reachability picture."""
    reached: dict[str, set[Any]] = {name: set() for name in TABLES}
    dynamic: set[str] = set()
    for module in PLATFORM_MODULES:
        module_keys, module_dynamic = _collect(COMPONENT_DIR / module)
        for table, keys in module_keys.items():
            reached[table] |= keys
        dynamic |= module_dynamic
    return reached, dynamic


def test_every_description_is_reachable_from_a_builder() -> None:
    """No table entry may exist without a builder that can create it."""
    reached, dynamic = _reachable_keys()

    orphans: dict[str, list[Any]] = {}
    for table in TABLES:
        if table in dynamic:
            continue
        allowed = _INTENTIONALLY_UNWIRED.get(table, set())
        missing = [
            key
            for key in getattr(descriptions, table)
            if key not in reached[table] and key not in allowed
        ]
        if missing:
            orphans[table] = sorted(missing, key=repr)

    assert not orphans, (
        "Entity descriptions declared but no builder creates them — the "
        "device reports the value and the entity never appears (the #38 / "
        "#41 / #42 bug class):\n"
        + "\n".join(
            f"  {table}: {', '.join(repr(k) for k in keys)}"
            for table, keys in sorted(orphans.items())
        )
        + "\n\nFix: wire the key into the matching builder in sensor.py or "
        "binary_sensor.py. If it is genuinely unwirable today, add it to "
        "_INTENTIONALLY_UNWIRED in this file WITH the reason."
    )


def test_wifi_rssi_is_wired_on_both_generations() -> None:
    """The two RSSI descriptions specifically — the fourth instance.

    Named on its own so a regression reads as "RSSI lost its builder" rather
    than as one line in a list.
    """
    reached, _ = _reachable_keys()
    assert "rssi" in reached["RPC_SENSORS"], (
        "RPC_SENSORS['rssi'] is declared but no builder creates it; "
        "wifi.rssi is present on every Gen2+ device we have ever recorded"
    )
    assert ("wifi_sta", "rssi") in reached["BLOCK_SENSORS"], (
        "BLOCK_SENSORS[('wifi_sta', 'rssi')] is declared but no builder "
        "creates it"
    )


def test_rssi_entities_read_the_right_field() -> None:
    """Reachability is necessary but not sufficient — read the value too.

    A builder can be wired to the correct description and still hand it the
    wrong status key, which the static check above cannot see. The Gen1 shape
    here comes from the vendor Gen1 API documentation
    (https://shelly-api-docs.shelly.cloud/gen1/, /status), not from a
    measurement: no Gen1 device appears in any recorded payload.
    """
    from custom_components.shelly_cloud_diy.sensor import (
        _create_block_sensors,
        _create_rpc_sensors,
    )

    device_id = "a0dd6cffee01"

    gen2 = {"switch:0": {"id": 0, "output": True}, "wifi": {"rssi": -82}}
    gen1 = {
        "wifi_sta": {"connected": True, "ssid": "x", "ip": "10.0.0.2", "rssi": -54},
        "tmp": {"tC": 21.0, "is_valid": True},
    }

    coordinator = _StatusOnlyCoordinator(device_id, gen2)
    rpc = {e.unique_id: e for e in _create_rpc_sensors(device_id, gen2, set(), coordinator)}
    assert rpc[f"{device_id}_wifi_rssi"].native_value == -82
    assert rpc[f"{device_id}_wifi_rssi"].native_unit_of_measurement == "dBm"

    coordinator = _StatusOnlyCoordinator(device_id, gen1)
    block = {
        e.unique_id: e for e in _create_block_sensors(device_id, gen1, set(), coordinator)
    }
    assert block[f"{device_id}_wifi_sta|rssi_0"].native_value == -54


def test_rssi_entity_is_skipped_when_the_radio_reports_nothing() -> None:
    """A component present but null must not become a permanently-unknown entity."""
    from custom_components.shelly_cloud_diy.sensor import _create_rpc_sensors

    device_id = "a0dd6cffee01"
    status = {"switch:0": {"id": 0}, "wifi": {"rssi": None, "status": "got ip"}}
    coordinator = _StatusOnlyCoordinator(device_id, status)

    built = _create_rpc_sensors(device_id, status, set(), coordinator)
    assert not [e for e in built if e.unique_id.endswith("_wifi_rssi")]


class _StatusOnlyCoordinator:
    """Minimal coordinator: entities only read ``.devices[id]['status']``."""

    def __init__(self, device_id: str, status: dict[str, Any]) -> None:
        self.devices = {device_id: {"status": status, "online": True}}
        self.data = self.devices
        self.last_update_success = True


def test_allowlist_only_holds_real_table_entries() -> None:
    """A stale allowlist would silently mask a new orphan.

    If an entry is renamed or removed from a table but left behind here, the
    allowlist keeps growing while the guard quietly weakens.
    """
    stale: dict[str, list[Any]] = {}
    for table, keys in _INTENTIONALLY_UNWIRED.items():
        leftover = [key for key in keys if key not in getattr(descriptions, table)]
        if leftover:
            stale[table] = sorted(leftover, key=repr)

    assert not stale, (
        "_INTENTIONALLY_UNWIRED names entries that no longer exist in the "
        f"description tables; remove them: {stale}"
    )


def test_dynamic_tables_are_the_expected_ones() -> None:
    """Only the BLE tables may be reached by an unresolvable key.

    A dynamic lookup switches the guard off for a whole table, so the set of
    tables that get that exemption must not grow by accident — a refactor
    that turned a literal lookup into a computed one would otherwise blind
    the check without a single test failing.
    """
    _, dynamic = _reachable_keys()
    assert dynamic == {"BLE_SENSORS", "BLE_BINARY_SENSORS"}, (
        "Tables reached by an unresolvable key changed to "
        f"{sorted(dynamic)}; a table listed here is exempt from the "
        "dead-description check entirely"
    )
