"""Guard: ``__init__`` really assigns everything the poll path reads.

This test exists because of a bug no other test could have caught. A rebase
moved a block of ``self._… = …`` assignments out of ``__init__`` and behind
the ``return`` of a property that had been added next to it. The result was
dead code: the module imported, all 220 tests passed, and the integration
then died on its very first poll with ``AttributeError``.

Every other coordinator test builds its subject with ``object.__new__`` and
sets the attributes by hand — deliberately, because constructing the real
coordinator drags in Home Assistant's frame-reporting machinery. That choice
is what makes ``__init__`` itself invisible to the suite, so it needs its own
guard.

The check is static rather than behavioural: it reads the assignments out of
``__init__``'s syntax tree. That is enough, because the failure mode is
positional — an assignment landing outside the function body — and a static
read catches exactly that without needing a running Home Assistant.
"""
from __future__ import annotations

import ast
import inspect

from custom_components.shelly_cloud_diy.coordinator import ShellyCloudCoordinator

# Attributes ``_async_update_data`` and its helpers read on a normal poll.
# Anything here that ``__init__`` does not set is an AttributeError on the
# first poll after a fresh start.
REQUIRED = {
    "_entry",
    "_api",
    "devices",
    "_known_device_ids",
    "device_names",
    "_names_attempted",
    "_name_lookup_in_flight",
    "virtual_configs",
    "_vcomp_config_in_flight",
    "_sleep_seen",
    "checkins",
    "_rate_limit_streak",
    "_rate_limit_since",
    "_rate_limit_reported",
    "_missing_streak",
    "_missing_since",
    "_relay_fault_streak",
    "_relay_fault_since",
    "_relay_healthy_since",
    "relay_faults",
    "_health_streak",
    "_health_since",
    "device_health",
}


def _attributes_assigned_in_init() -> set[str]:
    """Names assigned as ``self.<name>`` directly in ``__init__``'s body."""
    source = inspect.getsource(ShellyCloudCoordinator.__init__)
    tree = ast.parse(inspect.cleandoc(source))
    assigned: set[str] = set()
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                assigned.add(target.attr)
    return assigned


def test_init_assigns_every_attribute_the_poll_path_reads() -> None:
    missing = REQUIRED - _attributes_assigned_in_init()
    assert not missing, (
        "__init__ does not assign: "
        + ", ".join(sorted(missing))
        + " — the integration will raise AttributeError on its first poll. "
        "Check whether the assignments ended up outside the function body."
    )
