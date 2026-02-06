"""Historical data sync service for Shelly Integrator.

Handles fetching and importing historical energy data from EM devices.
Uses Home Assistant's native statistics API for direct import.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.helpers.event import async_track_time_interval

from ..const import CONF_LOCAL_GATEWAY_URL, HISTORICAL_SYNC_INTERVAL
from ..utils.csv_converter import (
    build_statistic_id,
    parse_shelly_csv_for_import,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..utils.http import fetch_csv_from_gateway
from .notifications import NotificationService

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, ServiceCall
    from ..coordinator import ShellyIntegratorCoordinator

_LOGGER = logging.getLogger(__name__)

# EM device codes that support historical data
EM_DEVICE_CODES = {"SHEM", "SHEM-3", "SPEM-003CEBEU"}


class HistoricalDataService:
    """Service for syncing historical energy data from EM devices."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: ShellyIntegratorCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize historical data service.

        Args:
            hass: Home Assistant instance
            coordinator: Shelly Integrator coordinator
            entry: Config entry
        """
        self._hass = hass
        self._coordinator = coordinator
        self._entry = entry
        self._notifications = NotificationService(hass)
        self._cancel_interval: callable | None = None

    @property
    def gateway_url(self) -> str:
        """Get configured gateway URL."""
        return self._entry.options.get(CONF_LOCAL_GATEWAY_URL, "")

    async def handle_service_call(self, call: ServiceCall) -> None:
        """Handle download_and_convert_history service call.

        Args:
            call: Service call data
        """
        gateway_url = call.data.get("gateway_url") or self.gateway_url
        device_id = call.data.get("device_id")

        if not gateway_url:
            _LOGGER.error("No gateway URL provided")
            self._notifications.show_gateway_url_missing()
            return

        # sync_data now imports directly using native HA API
        imported_stats = await self.sync_data(gateway_url, device_id)

        if imported_stats:
            self._notifications.show_historical_success(imported_stats)
        else:
            self._notifications.show_historical_error(
                "No EM devices found or failed to fetch data. Check logs."
            )

    async def setup_auto_sync(self) -> None:
        """Set up automatic daily sync if gateway URL is configured."""
        if not self.gateway_url:
            _LOGGER.debug("No gateway URL, skipping auto sync")
            return

        # Run initial sync after startup
        self._hass.loop.call_later(
            60,
            lambda: self._hass.async_create_task(self._run_auto_sync())
        )

        # Schedule daily sync
        self._cancel_interval = async_track_time_interval(
            self._hass,
            self._run_auto_sync,
            timedelta(seconds=HISTORICAL_SYNC_INTERVAL),
        )

        _LOGGER.info(
            "Scheduled auto sync every %d hours",
            HISTORICAL_SYNC_INTERVAL // 3600
        )

    async def _run_auto_sync(self, now=None) -> None:
        """Run automatic sync."""
        _LOGGER.info("Starting automatic historical sync")
        try:
            # sync_data imports directly using native HA API
            imported_stats = await self.sync_data(self.gateway_url)
            if imported_stats:
                _LOGGER.info("Sync complete: %s", ", ".join(imported_stats))
            else:
                _LOGGER.warning("Sync complete: No statistics imported")
        except Exception as err:
            _LOGGER.error("Auto sync failed: %s", err)

    async def _get_ha_current_sum(self, statistic_id: str) -> float:
        """Get HA's current cumulative sum for an entity.
        
        Args:
            statistic_id: The entity ID
            
        Returns:
            Current cumulative sum, or 0 if not found
        """
        try:
            # Query the last 24 hours of statistics to find the latest sum
            start_time = datetime.now(timezone.utc) - timedelta(hours=24)
            
            stats = await statistics_during_period(
                self._hass,
                start_time=start_time,
                end_time=None,
                statistic_ids={statistic_id},
                period="hour",
                units=None,
                types={"sum"},
            )
            
            if statistic_id in stats and stats[statistic_id]:
                # Get the latest sum value
                latest = stats[statistic_id][-1]
                ha_sum = latest.get("sum", 0) or 0
                _LOGGER.debug(
                    "Found HA current sum for %s: %.2f Wh", 
                    statistic_id, ha_sum
                )
                return ha_sum
                
        except Exception as err:
            _LOGGER.warning("Could not get HA current sum: %s", err)
        
        return 0.0

    async def _get_ha_sum_before_date(
        self, statistic_id: str, before_dt: datetime
    ) -> float | None:
        """Get HA's latest cumulative sum before a given datetime.

        Used to find the sum at midnight (start of today) so that imported
        historical data aligns with the recorder's live tracking without
        causing a sum discontinuity.

        Args:
            statistic_id: The entity ID
            before_dt: Get the latest sum before this time (UTC aware)

        Returns:
            Latest sum before the datetime, or None if not found
        """
        try:
            # Query a 48-hour window before the target to handle brief
            # HA downtime or gaps in statistics.
            query_start = before_dt - timedelta(hours=48)

            stats = await statistics_during_period(
                self._hass,
                start_time=query_start,
                end_time=before_dt,
                statistic_ids={statistic_id},
                period="hour",
                units=None,
                types={"sum"},
            )

            if statistic_id in stats and stats[statistic_id]:
                latest = stats[statistic_id][-1]
                ha_sum = latest.get("sum", 0) or 0
                _LOGGER.debug(
                    "Found HA sum before %s for %s: %.2f Wh",
                    before_dt, statistic_id, ha_sum
                )
                return ha_sum

        except Exception as err:
            _LOGGER.warning(
                "Could not get HA sum before %s: %s", before_dt, err
            )

        return None

    async def _import_statistics_native(
        self,
        statistic_id: str,
        data: list[tuple[datetime, float]],
    ) -> bool:
        """Import statistics using Home Assistant's native API.
        
        This bypasses the import_statistics HACS integration and its
        65-minute timestamp restriction.
        
        IMPORTANT: Aligns cumulative sum with HA's current baseline to
        prevent negative daily values.
        
        Args:
            statistic_id: The entity ID (e.g., sensor.shellyem_xxx_energy)
            data: List of (datetime_utc, delta_wh) tuples
            
        Returns:
            True if import was successful
        """
        if not data:
            return False

        try:
            from homeassistant.components.recorder.models import (
                StatisticData,
                StatisticMeanType,
                StatisticMetaData,
            )

            # STEP 1: Filter out today's data to prevent conflict with
            # HA's recorder.  The recorder actively tracks today's energy
            # from the live sensor.  Importing over it causes a sum
            # discontinuity (the recorder's in-memory cumulative sum
            # diverges from the imported database value), which results
            # in wrong — often very negative — daily consumption.
            start_of_today_utc = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            historical_data = [
                (dt, delta) for dt, delta in data if dt < start_of_today_utc
            ]

            if not historical_data:
                _LOGGER.debug(
                    "No historical data to import for %s "
                    "(all %d data points are from today)",
                    statistic_id, len(data),
                )
                return False

            _LOGGER.info(
                "Filtered data for %s: %d historical / %d today (skipped)",
                statistic_id,
                len(historical_data),
                len(data) - len(historical_data),
            )

            # STEP 2: Get HA's sum at the boundary (start of today).
            # This is the anchor between imported historical data and
            # the recorder's live tracking, preventing sum jumps at
            # midnight that cause wrong daily consumption values.
            ha_midnight_sum = await self._get_ha_sum_before_date(
                statistic_id, start_of_today_utc
            )

            if ha_midnight_sum is None:
                # First-time import or no prior statistics — fall back
                # to the current sum.  Slightly inaccurate for today
                # but self-corrects on the next daily sync.
                ha_midnight_sum = await self._get_ha_current_sum(statistic_id)
                _LOGGER.info(
                    "No midnight statistics found for %s, "
                    "using current sum as fallback: %.2f Wh",
                    statistic_id, ha_midnight_sum,
                )

            # STEP 3: Calculate total from filtered historical data
            shelly_total = sum(delta for _, delta in historical_data)

            # STEP 4: Calculate alignment offset
            # Last imported point (yesterday's last hour) should match
            # the HA midnight sum so today's recorder data connects
            # seamlessly.
            offset = ha_midnight_sum - shelly_total

            _LOGGER.info(
                "Sum alignment for %s: midnight=%.2f, Shelly=%.2f, offset=%.2f",
                statistic_id, ha_midnight_sum, shelly_total, offset,
            )

            # STEP 5: Build metadata
            metadata = StatisticMetaData(
                statistic_id=statistic_id,
                source="recorder",
                name=f"Shelly Energy ({statistic_id})",
                unit_of_measurement="Wh",
                has_sum=True,
                has_mean=False,
                mean_type=StatisticMeanType.NONE,
            )

            # STEP 6: Build statistics with aligned cumulative sum
            statistics: list[StatisticData] = []
            cumulative_sum = offset  # Start from offset, not 0!

            for dt_utc, delta in historical_data:
                cumulative_sum += delta
                statistics.append(
                    StatisticData(
                        start=dt_utc,
                        sum=cumulative_sum,
                        state=delta,
                    )
                )

            # STEP 7: Import using HA's native API
            async_import_statistics(self._hass, metadata, statistics)

            _LOGGER.info(
                "Imported %d statistics for %s "
                "(final sum: %.2f Wh, aligned with midnight)",
                len(statistics), statistic_id, cumulative_sum,
            )
            return True
            
        except Exception as err:
            _LOGGER.error("Native import failed for %s: %s", statistic_id, err)
            return False

    def cancel_auto_sync(self) -> None:
        """Cancel automatic sync."""
        if self._cancel_interval:
            self._cancel_interval()
            self._cancel_interval = None

    async def sync_data(
        self,
        gateway_url: str,
        device_id: str | None = None,
    ) -> list[str]:
        """Sync historical data from EM devices.
        
        Downloads CSV data from gateway and imports directly to HA statistics
        using the native recorder API (no 65-minute timestamp restriction).

        Args:
            gateway_url: Base gateway URL
            device_id: Optional specific device ID

        Returns:
            List of successfully imported statistic IDs
        """
        if not gateway_url:
            _LOGGER.error("No gateway URL provided")
            return []

        gateway_url = gateway_url.rstrip("/")
        _LOGGER.info("Starting sync from %s", gateway_url)

        imported_stats: list[str] = []
        em_devices = self._find_em_devices(device_id)

        if not em_devices:
            _LOGGER.info("No EM devices found")
            return []

        for dev_id, device_data in em_devices:
            hostname = self._get_device_hostname(device_data)
            if not hostname:
                _LOGGER.warning("No hostname for device %s", dev_id)
                continue

            device_code = device_data.get("device_code", "SHEM")
            num_channels = 3 if device_code in ("SHEM-3", "SPEM-003CEBEU") else 2

            session = async_get_clientsession(self._hass)
            for channel in range(num_channels):
                csv_data = await fetch_csv_from_gateway(
                    gateway_url, hostname, channel, session=session
                )
                if not csv_data:
                    continue

                # Parse CSV data
                data = parse_shelly_csv_for_import(csv_data)
                if not data:
                    _LOGGER.warning(
                        "No valid data for %s channel %d", hostname, channel
                    )
                    continue

                # Build statistic ID
                statistic_id = build_statistic_id(hostname, channel)
                
                # Import directly to HA statistics (native API)
                success = await self._import_statistics_native(statistic_id, data)
                if success:
                    imported_stats.append(statistic_id)

        _LOGGER.info("Sync complete. Imported %d statistics", len(imported_stats))
        return imported_stats

    def _find_em_devices(
        self,
        device_id: str | None = None,
    ) -> list[tuple[str, dict]]:
        """Find EM devices in coordinator."""
        em_devices = []
        for dev_id, device_data in self._coordinator.devices.items():
            if device_id and dev_id != device_id:
                continue
            device_code = device_data.get("device_code", "")
            if device_code in EM_DEVICE_CODES:
                em_devices.append((dev_id, device_data))
        return em_devices

    def _get_device_hostname(self, device_data: dict) -> str | None:
        """Get device hostname from device data."""
        status = device_data.get("status", {})
        getinfo = status.get("getinfo", {}).get("fw_info", {})
        return getinfo.get("device") or device_data.get("name")
