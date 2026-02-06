"""CSV Converter for Shelly EM Historical Data.

Converts Shelly EM energy CSV data to statistics format.
Supports direct HA native statistics import.

Shelly EM CSV format (input):
    Date/time UTC,Active energy Wh (1),Returned energy Wh (1),Min V,Max V
    2025-12-27 00:00,2.10,0.00,232.0,233.5
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone

_LOGGER = logging.getLogger(__name__)


def parse_shelly_csv(csv_data: str) -> dict[str, float]:
    """Parse Shelly EM CSV data into hourly aggregated energy values.

    Args:
        csv_data: Raw CSV string from Shelly EM device

    Returns:
        Dictionary mapping hour timestamps to energy deltas (Wh)
        Example: {"2025-12-27 00:00": 12.5, "2025-12-27 01:00": 8.3}
    """
    reader = csv.reader(io.StringIO(csv_data))

    # Skip header
    header = next(reader, None)
    if not header:
        _LOGGER.warning("Empty CSV data")
        return {}

    _LOGGER.debug("CSV header: %s", header)

    # Aggregate 10-minute intervals to hourly
    hourly_data: dict[str, float] = {}

    for row in reader:
        if len(row) < 2:
            continue

        try:
            timestamp_str = row[0].strip()
            timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M")
            hour_key = timestamp.strftime("%Y-%m-%d %H:00")
            energy_delta = float(row[1].strip())

            hourly_data[hour_key] = hourly_data.get(hour_key, 0.0) + energy_delta

        except (ValueError, IndexError) as err:
            _LOGGER.debug("Skipping invalid row %s: %s", row, err)
            continue

    return hourly_data


def parse_shelly_csv_for_import(csv_data: str) -> list[tuple[datetime, float]]:
    """Parse Shelly EM CSV data for direct HA statistics import.

    Args:
        csv_data: Raw CSV string from Shelly EM device

    Returns:
        List of (datetime_utc, delta_wh) tuples, sorted by time
    """
    hourly_data = parse_shelly_csv(csv_data)

    result = []
    for hour_key in sorted(hourly_data.keys()):
        dt_utc = datetime.strptime(hour_key, "%Y-%m-%d %H:%M")
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        delta = hourly_data[hour_key]
        result.append((dt_utc, delta))

    return result


def build_statistic_id(hostname: str, channel: int) -> str:
    """Build a statistic ID matching the existing HA entity.

    Maps to existing HA entities created by shelly_integrator:
    - Channel 0 -> sensor.shellyem_<mac>_energy
    - Channel 1 -> sensor.shellyem_<mac>_energy_2

    Uses '.' separator (internal statistic) to match existing entity,
    which allows delta import to work immediately.

    Args:
        hostname: Device hostname (e.g., shellyem-48E729689B2B)
        channel: Channel number (0-indexed)

    Returns:
        Statistic ID matching existing HA entity
        (e.g., sensor.shellyem_48e729689b2b_energy)
    """
    safe_hostname = hostname.lower().replace("-", "_")
    if channel == 0:
        return f"sensor.{safe_hostname}_energy"
    else:
        return f"sensor.{safe_hostname}_energy_{channel + 1}"
