"""Historical data sync service for Shelly Integrator.

Handles fetching and importing historical energy data from EM devices.
Uses Home Assistant's native statistics API for direct import.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.helpers.event import async_track_time_interval

from ..const import CONF_LOCAL_GATEWAY_URL, HISTORICAL_SYNC_INTERVAL
from ..utils.csv_converter import (
    build_statistic_id,
    parse_shelly_csv_for_import,
)
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

    async def _import_statistics_native(
        self,
        statistic_id: str,
        data: list[tuple[datetime, float]],
    ) -> bool:
        """Import statistics using Home Assistant's native API.
        
        This bypasses the import_statistics HACS integration and its
        65-minute timestamp restriction.
        
        Args:
            statistic_id: The entity ID (e.g., sensor.shellyem_xxx_energy)
            data: List of (datetime_utc, delta_wh) tuples
            
        Returns:
            True if import was successful
        """
        if not data:
            return False

        try:
            # Build StatisticMetaData
            from homeassistant.components.recorder.models import (
                StatisticData,
                StatisticMeanType,
                StatisticMetaData,
            )
            
            metadata = StatisticMetaData(
                statistic_id=statistic_id,
                source="recorder",
                name=f"Shelly Energy ({statistic_id})",
                unit_of_measurement="Wh",
                has_sum=True,
                has_mean=False,
                mean_type=StatisticMeanType.NONE,
            )
            
            # Convert to StatisticData objects with cumulative sum
            statistics: list[StatisticData] = []
            cumulative_sum = 0.0
            
            for dt_utc, delta in data:
                cumulative_sum += delta
                statistics.append(
                    StatisticData(
                        start=dt_utc,
                        sum=cumulative_sum,
                        state=delta,
                    )
                )
            
            # Import using HA's native API - no 65-minute restriction!
            async_import_statistics(self._hass, metadata, statistics)
            
            _LOGGER.info(
                "Imported %d statistics for %s (total: %.2f Wh)",
                len(statistics), statistic_id, cumulative_sum
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

            for channel in range(num_channels):
                csv_data = await fetch_csv_from_gateway(
                    gateway_url, hostname, channel
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
