# Shelly Integrator - Timezone Fix

## Problem

ApexCharts shows dates shifted by 1 day. For example, today is Feb 3 but the chart shows Feb 4 for today's data.

## Root Cause

1. Shelly EM CSV data is in **UTC** (`Date/time UTC` column)
2. Current conversion outputs timestamps in **local time** (Europe/Istanbul, UTC+3)
3. `homeassistant-statistics` imports WITHOUT specifying timezone
4. HA stores statistics in **UTC** internally
5. When ApexCharts aggregates by day, the day boundaries are misaligned

## Current Flow (Wrong)

```
Shelly CSV (UTC 21:00 Feb 2) 
  → Convert to local (00:00 Feb 3 Istanbul) 
  → Import (interpreted as local, stored as UTC Feb 2 21:00)
  → Display aggregates by local day boundaries → MISMATCH
```

## Solution

**Option A: Keep UTC throughout**

```python
# In shelly-integrator-ha conversion:
# Do NOT convert UTC to local time - keep as UTC

utc_time = datetime.strptime(row['Date/time UTC'], '%Y-%m-%d %H:%M')
output_timestamp = utc_time.strftime('%d.%m.%Y %H:%M')  # Keep UTC
```

Then import with `timezone_identifier: "UTC"`:

```python
await hass.services.async_call(
    "import_statistics",
    "import_from_file",
    {
        "filename": output_filename,
        "delimiter": ",",
        "decimal": ".",
        "datetime_format": "%d.%m.%Y %H:%M",
        "timezone_identifier": "UTC",  # CRITICAL
        "unit_from_entity": True,
    },
)
```

**Option B: Specify timezone on import (Quick fix)**

Keep current conversion (local time output) but add timezone to import:

```python
"timezone_identifier": "Europe/Istanbul",
```

## Testing

After fix, verify:
1. Today's date (Feb 3) should show Feb 3 in tooltip
2. Day boundaries should align with local midnight

## Files to Update

- `shelly-integrator-ha/custom_components/shelly_integrator/csv_converter.py`
- Service call in `__init__.py` or wherever import is called
