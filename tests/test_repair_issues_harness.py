"""Live issue-registry tests for the ``repair_issues`` registry wrappers.

``tests/test_repair_issues.py`` is deliberately pure-logic: it stubs the
four ``async_manage_*`` / ``async_clear_entry_issues`` wrappers so it can
run against any Home Assistant version in the two-venv runner. That leaves
the wrappers themselves — and the load-bearing assumption they rest on —
unexecuted.

The assumption: ``ir.async_create_issue`` is an idempotent upsert. Calling
it on every poll while the condition holds must

* not churn the registry (no spurious ``create``/``update`` events, stable
  ``created`` timestamp), and
* **not destroy a user's "Ignore"** — the registry stores the dismissal on
  the issue itself as ``dismissed_version``.

That is why ``repair_issues.py`` carries no ``_issue_raised`` guard
booleans. If the assumption were false the repair card would come back
seconds after every dismissal, so it is verified here against a REAL
``homeassistant.helpers.issue_registry`` on a real ``hass`` object.

This file needs ``pytest-homeassistant-custom-component``, which is
deliberately NOT installed in the two matrix venvs (it hard-pins
``homeassistant==``, which would collapse the min/max bracket). It lives in
a third, isolated venv and is skipped cleanly everywhere else::

    ~/.venvs/shelly-diy-harness/bin/python -m pytest \\
        tests/test_repair_issues_harness.py \\
        -p pytest_homeassistant_custom_component -o asyncio_mode=auto

The plugin and the asyncio mode are passed on the command line rather than
via a conftest/ini so that ``pytest tests/`` in the two matrix venvs is
completely untouched; there this module simply reports SKIPPED.

The harness pins ``homeassistant==2025.1.4`` (this repo's stated minimum,
and the newest HA that ``pytest-homeassistant-custom-component`` ships a
release for), so these tests execute against the OLD end of the bracket.
They transfer to the new end by inspection: ``IssueRegistry
.async_get_or_create/.async_delete/.async_ignore`` and the module-level
``async_create_issue/async_delete_issue/async_ignore_issue`` are
byte-identical between 2025.1.4 and 2026.7.2, as is the ``IssueEntry``
field set. If a future HA changes any of them, re-run this file.
"""
from __future__ import annotations

import pytest

pytest.importorskip(
    "pytest_homeassistant_custom_component",
    reason="live issue-registry harness venv not present in this environment",
)

from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
    async_capture_events,
)

from homeassistant.const import __version__ as HA_VERSION  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.helpers import issue_registry as ir  # noqa: E402

from custom_components.shelly_cloud_diy.const import DOMAIN  # noqa: E402
from custom_components.shelly_cloud_diy.repair_issues import (  # noqa: E402
    ISSUE_HISTORY_IMPORT_FAILED,
    ISSUE_MISSING_DEVICES,
    ISSUE_RATE_LIMITED,
    ISSUE_RELAY_FAULT,
    async_clear_entry_issues,
    async_manage_history_import_issue,
    async_manage_missing_devices_issue,
    async_manage_rate_limit_issue,
    async_manage_relay_fault_issue,
    format_device_list,
    format_relay_fault_list,
    issue_id,
)

pytestmark = pytest.mark.asyncio


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def entry() -> MockConfigEntry:
    """A config entry standing in for one Shelly Cloud account."""
    return MockConfigEntry(
        domain=DOMAIN, title="Shelly Cloud", entry_id="entry_one"
    )


@pytest.fixture
def other_entry() -> MockConfigEntry:
    """A second account, used to prove per-entry isolation."""
    return MockConfigEntry(
        domain=DOMAIN, title="Second Account", entry_id="entry_two"
    )


@pytest.fixture
def events(hass: HomeAssistant) -> list:
    """Every issue-registry update event fired during a test."""
    return async_capture_events(hass, ir.EVENT_REPAIRS_ISSUE_REGISTRY_UPDATED)


def _actions(events: list) -> list[str]:
    return [e.data["action"] for e in events]


# ── 1. Each wrapper creates the issue it advertises ───────────────────


async def test_rate_limit_wrapper_creates_expected_issue(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The rate-limit wrapper writes a real, correctly-shaped issue."""
    async_manage_rate_limit_issue(hass, entry, active=True)

    reg = ir.async_get(hass)
    iid = issue_id(ISSUE_RATE_LIMITED, entry.entry_id)
    issue = reg.async_get_issue(DOMAIN, iid)

    assert issue is not None
    assert issue.issue_id == f"rate_limited_{entry.entry_id}"
    assert issue.domain == DOMAIN
    assert issue.translation_key == ISSUE_RATE_LIMITED
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.is_fixable is False
    assert issue.is_persistent is False
    assert issue.active is True
    assert issue.dismissed_version is None
    assert issue.translation_placeholders == {"entry_title": "Shelly Cloud"}


async def test_missing_devices_wrapper_creates_expected_issue(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The missing-devices wrapper renders count + formatted id list."""
    missing = {"3494546e1f2a", "3494546e1f2b"}
    names = {"3494546e1f2a": "Wohnzimmer"}

    async_manage_missing_devices_issue(
        hass, entry, active=True, missing=missing, names=names
    )

    reg = ir.async_get(hass)
    iid = issue_id(ISSUE_MISSING_DEVICES, entry.entry_id)
    issue = reg.async_get_issue(DOMAIN, iid)

    assert issue is not None
    assert issue.issue_id == f"missing_devices_{entry.entry_id}"
    assert issue.translation_key == ISSUE_MISSING_DEVICES
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.is_fixable is False
    assert issue.is_persistent is False
    assert issue.translation_placeholders == {
        "entry_title": "Shelly Cloud",
        "count": "2",
        "device_ids": format_device_list(missing, names),
    }
    # The rendered list is what the user actually reads.
    assert (
        issue.translation_placeholders["device_ids"]
        == "Wohnzimmer (3494546e1f2a), 3494546e1f2b"
    )


async def test_history_import_wrapper_creates_expected_issue(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The historical-import wrapper writes a real, correct issue."""
    async_manage_history_import_issue(hass, entry, active=True)

    reg = ir.async_get(hass)
    iid = issue_id(ISSUE_HISTORY_IMPORT_FAILED, entry.entry_id)
    issue = reg.async_get_issue(DOMAIN, iid)

    assert issue is not None
    assert issue.issue_id == f"history_import_failed_{entry.entry_id}"
    assert issue.translation_key == ISSUE_HISTORY_IMPORT_FAILED
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.is_fixable is False
    assert issue.is_persistent is False
    assert issue.translation_placeholders == {"entry_title": "Shelly Cloud"}


async def test_issue_ids_are_namespaced_per_entry(
    hass: HomeAssistant, entry: MockConfigEntry, other_entry: MockConfigEntry
) -> None:
    """Two accounts raise two distinct cards, not one clobbered card."""
    async_manage_rate_limit_issue(hass, entry, active=True)
    async_manage_rate_limit_issue(hass, other_entry, active=True)

    reg = ir.async_get(hass)
    first = reg.async_get_issue(
        DOMAIN, issue_id(ISSUE_RATE_LIMITED, entry.entry_id)
    )
    second = reg.async_get_issue(
        DOMAIN, issue_id(ISSUE_RATE_LIMITED, other_entry.entry_id)
    )
    assert first is not None and second is not None
    assert first.issue_id != second.issue_id
    assert first.translation_placeholders["entry_title"] == "Shelly Cloud"
    assert second.translation_placeholders["entry_title"] == "Second Account"


# ── 2. THE CORE CLAIM: repeated calls do not churn ────────────────────


@pytest.mark.parametrize("polls", [2, 20])
async def test_repeated_rate_limit_calls_do_not_churn(
    hass: HomeAssistant, entry: MockConfigEntry, events: list, polls: int
) -> None:
    """Re-raising an unchanged issue fires exactly one create, ever.

    This is what licenses calling the wrapper unconditionally from every
    poll instead of guarding it with an ``_issue_raised`` boolean.
    """
    async_manage_rate_limit_issue(hass, entry, active=True)
    await hass.async_block_till_done()

    reg = ir.async_get(hass)
    iid = issue_id(ISSUE_RATE_LIMITED, entry.entry_id)
    first = reg.async_get_issue(DOMAIN, iid)
    assert _actions(events) == ["create"]

    for _ in range(polls):
        async_manage_rate_limit_issue(hass, entry, active=True)
    await hass.async_block_till_done()

    again = reg.async_get_issue(DOMAIN, iid)
    # Same object — async_get_or_create did not even build a replacement.
    assert again is first
    assert again.created == first.created
    # And no further registry traffic: no update events at all.
    assert _actions(events) == ["create"]


async def test_repeated_missing_devices_calls_do_not_churn(
    hass: HomeAssistant, entry: MockConfigEntry, events: list
) -> None:
    """An unchanged missing set re-raised 20x stays one create."""
    missing = {"aaaa", "bbbb"}
    for _ in range(20):
        async_manage_missing_devices_issue(
            hass, entry, active=True, missing=missing, names={}
        )
    await hass.async_block_till_done()

    assert _actions(events) == ["create"]


async def test_missing_devices_set_change_updates_in_place(
    hass: HomeAssistant, entry: MockConfigEntry, events: list
) -> None:
    """A CHANGED missing set updates once — never delete-then-create.

    A delete+create would reset the user's Ignore; an update preserves it.
    """
    async_manage_missing_devices_issue(
        hass, entry, active=True, missing={"aaaa"}, names={}
    )
    await hass.async_block_till_done()

    async_manage_missing_devices_issue(
        hass, entry, active=True, missing={"aaaa", "bbbb"}, names={}
    )
    await hass.async_block_till_done()

    assert _actions(events) == ["create", "update"]
    assert "remove" not in _actions(events)

    reg = ir.async_get(hass)
    issue = reg.async_get_issue(
        DOMAIN, issue_id(ISSUE_MISSING_DEVICES, entry.entry_id)
    )
    assert issue.translation_placeholders["count"] == "2"


async def test_repeated_history_import_calls_do_not_churn(
    hass: HomeAssistant, entry: MockConfigEntry, events: list
) -> None:
    """The historical-import wrapper is idempotent too."""
    for _ in range(20):
        async_manage_history_import_issue(hass, entry, active=True)
    await hass.async_block_till_done()

    assert _actions(events) == ["create"]


# ── 3. THE DISMISSAL CLAIM (the one that matters most) ────────────────


@pytest.mark.parametrize(
    ("raise_issue", "kind"),
    [
        (
            lambda hass, entry: async_manage_rate_limit_issue(
                hass, entry, active=True
            ),
            ISSUE_RATE_LIMITED,
        ),
        (
            lambda hass, entry: async_manage_missing_devices_issue(
                hass, entry, active=True, missing={"aaaa"}, names={}
            ),
            ISSUE_MISSING_DEVICES,
        ),
        (
            lambda hass, entry: async_manage_history_import_issue(
                hass, entry, active=True
            ),
            ISSUE_HISTORY_IMPORT_FAILED,
        ),
    ],
    ids=["rate_limited", "missing_devices", "history_import_failed"],
)
async def test_user_dismissal_survives_further_polls(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    events: list,
    raise_issue,
    kind: str,
) -> None:
    """Ignoring a card must stick even though every poll re-raises it.

    If this fails, ``repair_issues.py`` needs its guard booleans back.
    """
    raise_issue(hass, entry)
    await hass.async_block_till_done()

    reg = ir.async_get(hass)
    iid = issue_id(kind, entry.entry_id)

    # The user clicks "Ignore" in the Repairs panel.
    ir.async_ignore_issue(hass, DOMAIN, iid, True)
    await hass.async_block_till_done()

    dismissed = reg.async_get_issue(DOMAIN, iid)
    assert dismissed.dismissed_version == HA_VERSION
    events.clear()

    # ~100 more polls with the condition still active.
    for _ in range(100):
        raise_issue(hass, entry)
    await hass.async_block_till_done()

    still = reg.async_get_issue(DOMAIN, iid)
    assert still is not None
    assert still.dismissed_version == HA_VERSION, (
        "re-raising the issue destroyed the user's Ignore — "
        "the no-guard design in repair_issues.py is invalid"
    )
    assert still.created == dismissed.created
    assert _actions(events) == []


async def test_dismissal_survives_a_changing_missing_set(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Even a placeholder-changing re-raise keeps the dismissal.

    This is the case the docstring on ``async_manage_missing_devices_issue``
    warns about: a delete-then-create here would silently un-ignore.
    """
    async_manage_missing_devices_issue(
        hass, entry, active=True, missing={"aaaa"}, names={}
    )
    await hass.async_block_till_done()

    iid = issue_id(ISSUE_MISSING_DEVICES, entry.entry_id)
    ir.async_ignore_issue(hass, DOMAIN, iid, True)
    await hass.async_block_till_done()

    async_manage_missing_devices_issue(
        hass, entry, active=True, missing={"aaaa", "bbbb", "cccc"}, names={}
    )
    await hass.async_block_till_done()

    reg = ir.async_get(hass)
    issue = reg.async_get_issue(DOMAIN, iid)
    assert issue.dismissed_version == HA_VERSION
    assert issue.translation_placeholders["count"] == "3"


async def test_dismissal_does_not_survive_a_genuine_clear_and_reraise(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Deleting genuinely resets the dismissal — the intended semantics.

    The condition resolving and then recurring later is a NEW event and
    should show the user a fresh card.
    """
    async_manage_rate_limit_issue(hass, entry, active=True)
    iid = issue_id(ISSUE_RATE_LIMITED, entry.entry_id)
    ir.async_ignore_issue(hass, DOMAIN, iid, True)
    await hass.async_block_till_done()

    async_manage_rate_limit_issue(hass, entry, active=False)
    async_manage_rate_limit_issue(hass, entry, active=True)
    await hass.async_block_till_done()

    reg = ir.async_get(hass)
    assert reg.async_get_issue(DOMAIN, iid).dismissed_version is None


# ── 4. active=False deletes; deleting nothing is a no-op ──────────────


@pytest.mark.parametrize(
    ("raise_issue", "clear_issue", "kind"),
    [
        (
            lambda hass, entry: async_manage_rate_limit_issue(
                hass, entry, active=True
            ),
            lambda hass, entry: async_manage_rate_limit_issue(
                hass, entry, active=False
            ),
            ISSUE_RATE_LIMITED,
        ),
        (
            lambda hass, entry: async_manage_missing_devices_issue(
                hass, entry, active=True, missing={"aaaa"}, names={}
            ),
            lambda hass, entry: async_manage_missing_devices_issue(
                hass, entry, active=False, missing=set(), names={}
            ),
            ISSUE_MISSING_DEVICES,
        ),
        (
            lambda hass, entry: async_manage_history_import_issue(
                hass, entry, active=True
            ),
            lambda hass, entry: async_manage_history_import_issue(
                hass, entry, active=False
            ),
            ISSUE_HISTORY_IMPORT_FAILED,
        ),
    ],
    ids=["rate_limited", "missing_devices", "history_import_failed"],
)
async def test_inactive_deletes_and_repeat_delete_is_a_noop(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    events: list,
    raise_issue,
    clear_issue,
    kind: str,
) -> None:
    """``active=False`` removes the card; deleting again does not raise."""
    reg = ir.async_get(hass)
    iid = issue_id(kind, entry.entry_id)

    # Deleting before anything was ever raised must be a silent no-op.
    clear_issue(hass, entry)
    await hass.async_block_till_done()
    assert reg.async_get_issue(DOMAIN, iid) is None
    assert _actions(events) == []

    raise_issue(hass, entry)
    clear_issue(hass, entry)
    await hass.async_block_till_done()
    assert reg.async_get_issue(DOMAIN, iid) is None
    assert _actions(events) == ["create", "remove"]

    # And again, on an id that is now gone.
    events.clear()
    for _ in range(5):
        clear_issue(hass, entry)
    await hass.async_block_till_done()
    assert _actions(events) == []


# ── 5. async_clear_entry_issues is entry-scoped ───────────────────────


async def test_clear_entry_issues_removes_only_this_entry(
    hass: HomeAssistant, entry: MockConfigEntry, other_entry: MockConfigEntry
) -> None:
    """Unloading one account must not wipe the other account's cards."""
    for target in (entry, other_entry):
        async_manage_rate_limit_issue(hass, target, active=True)
        async_manage_missing_devices_issue(
            hass, target, active=True, missing={"aaaa"}, names={}
        )
        async_manage_history_import_issue(hass, target, active=True)
        async_manage_relay_fault_issue(
            hass, target, active=True, faults={("aaaa", 0)}, names={}
        )
    await hass.async_block_till_done()

    reg = ir.async_get(hass)
    kinds = (
        ISSUE_RATE_LIMITED,
        ISSUE_MISSING_DEVICES,
        ISSUE_HISTORY_IMPORT_FAILED,
        ISSUE_RELAY_FAULT,
    )
    assert len(reg.issues) == 8

    async_clear_entry_issues(hass, entry)
    await hass.async_block_till_done()

    for kind in kinds:
        assert (
            reg.async_get_issue(DOMAIN, issue_id(kind, entry.entry_id))
            is None
        )
        assert (
            reg.async_get_issue(DOMAIN, issue_id(kind, other_entry.entry_id))
            is not None
        )
    assert len(reg.issues) == 4


async def test_clear_entry_issues_on_a_clean_entry_is_a_noop(
    hass: HomeAssistant, entry: MockConfigEntry, events: list
) -> None:
    """A setup that never raised anything unloads without registry traffic."""
    async_clear_entry_issues(hass, entry)
    await hass.async_block_till_done()

    assert ir.async_get(hass).issues == {}
    assert _actions(events) == []


async def test_relay_fault_wrapper_creates_expected_issue(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The stuck-contact wrapper renders the affected channels.

    Worth running against the real registry rather than only as a pure
    function: this card is the one surface a user sees without having gone
    looking for a diagnostic entity first, so a placeholder the registry
    refuses to render would make the whole feature silent.
    """
    faults = {("5432044e9768", 0), ("3494546e1f2a", 1)}
    names = {"5432044e9768": "Büro Licht"}

    async_manage_relay_fault_issue(
        hass, entry, active=True, faults=faults, names=names
    )
    await hass.async_block_till_done()

    reg = ir.async_get(hass)
    issue = reg.async_get_issue(
        DOMAIN, issue_id(ISSUE_RELAY_FAULT, entry.entry_id)
    )

    assert issue is not None
    assert issue.translation_key == ISSUE_RELAY_FAULT
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.is_fixable is False
    assert issue.is_persistent is False
    assert issue.translation_placeholders == {
        "entry_title": "Shelly Cloud",
        "count": "2",
        "devices": format_relay_fault_list(faults, names),
    }
    rendered = issue.translation_placeholders["devices"]
    assert "Büro Licht (5432044e9768)" in rendered
    assert "3494546e1f2a channel 2" in rendered


async def test_relay_fault_card_survives_a_second_failing_actuator(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Upsert, not delete-then-create: a user who dismissed the card for one
    welded relay must not have it reappear un-ignored because a second one
    joined the list."""
    reg = ir.async_get(hass)
    iid = issue_id(ISSUE_RELAY_FAULT, entry.entry_id)

    async_manage_relay_fault_issue(
        hass, entry, active=True, faults={("aaaa", 0)}, names={}
    )
    await hass.async_block_till_done()
    ir.async_ignore_issue(hass, DOMAIN, iid, True)
    await hass.async_block_till_done()
    dismissed = reg.async_get_issue(DOMAIN, iid).dismissed_version
    assert dismissed is not None

    async_manage_relay_fault_issue(
        hass, entry, active=True, faults={("aaaa", 0), ("bbbb", 0)}, names={}
    )
    await hass.async_block_till_done()

    issue = reg.async_get_issue(DOMAIN, iid)
    assert issue.dismissed_version == dismissed, "the Ignore was destroyed"
    assert issue.translation_placeholders["count"] == "2"
