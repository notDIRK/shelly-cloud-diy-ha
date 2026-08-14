"""The parts-per-million unit must be right on BOTH ends of the HA range (#26).

Reported by @elad-eyal: every start logged

    The deprecated constant CONCENTRATION_PARTS_PER_MILLION was used from
    shelly_cloud_diy. It will be removed in HA Core 2027.8.

The trap is that the obvious fix breaks the other end. ``UnitOfRatio`` does not
exist on HA 2025.1.4, the oldest version this integration supports — importing
it there raises ImportError outright. So the code resolves the unit per version,
and these tests pin down both halves of that bargain: the value must be
identical everywhere, and the modern name must actually be preferred where it
exists (otherwise the deprecation, and the 2027.8 removal, still apply).
"""
from __future__ import annotations

import pytest

from custom_components.shelly_cloud_diy.entities import descriptions
from custom_components.shelly_cloud_diy.entities.descriptions import BLOCK_SENSORS


def test_unit_value_is_ppm_on_every_supported_version():
    """Whatever the constant is called here, the value users see must be 'ppm'."""
    assert descriptions._PARTS_PER_MILLION == "ppm"


def test_gas_concentration_sensor_carries_the_unit():
    """The Shelly Gas concentration sensor is the only consumer — check it."""
    desc = BLOCK_SENSORS[("sensor", "concentration")]
    assert desc.native_unit_of_measurement == "ppm"


def test_modern_name_is_preferred_where_it_exists():
    """On HA versions that have UnitOfRatio, we must be using it.

    Without this, someone could 'fix' a future import error by reaching for the
    deprecated constant again and the warning would quietly come back — which is
    exactly the state #26 reported.
    """
    try:
        from homeassistant.const import UnitOfRatio
    except ImportError:
        pytest.skip("HA predates UnitOfRatio — the fallback branch is correct here")

    assert descriptions._PARTS_PER_MILLION is UnitOfRatio.PARTS_PER_MILLION


def test_deprecated_constant_is_not_imported_unconditionally():
    """The old name may appear only inside the ImportError fallback.

    A plain top-level import would emit the deprecation warning on modern HA
    even if the value were never used, because HA raises it on attribute access
    at import time. Source-level check, because by the time a test runs the
    import has already happened and the warning is long gone.
    """
    from pathlib import Path

    source = Path(descriptions.__file__).read_text(encoding="utf-8")
    occurrences = source.count("CONCENTRATION_PARTS_PER_MILLION")
    # One mention in the explanatory comment, one in the fallback import.
    assert occurrences == 2, (
        f"expected the deprecated constant exactly twice (comment + guarded "
        f"fallback), found {occurrences} — is it imported unconditionally again?"
    )
    guarded = source.split("except ImportError")[1]
    assert "CONCENTRATION_PARTS_PER_MILLION" in guarded
