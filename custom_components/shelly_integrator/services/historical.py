"""Historical data sync service for Shelly Integrator.

Handles fetching and converting historical energy data from EM devices.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.helpers.event import async_track_time_interval

from ..const import CONF_LOCAL_GATEWAY_URL, DOMAIN, HISTORICAL_SYNC_INTERVAL
from ..utils.csv_converter import convert_channel_csv
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
        """Handle convert_historical_data service call.

        Args:
            call: Service call data
        """
        gateway_url = call.data.get("gateway_url") or self.gateway_url
        device_id = call.data.get("device_id")

        if not gateway_url:
            _LOGGER.error("No gateway URL provided")
            self._notifications.show_gateway_url_missing()
            return

        output_files = await self.sync_data(gateway_url, device_id)

        if output_files:
            # Auto-import after conversion
            await self._auto_import_statistics(output_files)
            self._notifications.show_historical_success(output_files)
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
            output_files = await self.sync_data(self.gateway_url)
            if output_files:
                _LOGGER.info("Sync complete: %s", ", ".join(output_files))
                await self._auto_import_statistics(output_files)
            else:
                _LOGGER.warning("Sync complete: No files created")
        except Exception as err:
            _LOGGER.error("Auto sync failed: %s", err)

    async def _auto_import_statistics(self, csv_files: list[str]) -> None:
        """Import statistics if homeassistant-statistics is available."""
        if not self._hass.services.has_service("import_statistics", "import_from_file"):
            _LOGGER.info("import_statistics service not available, skipping auto-import")
            return

        for csv_file in csv_files:
            try:
                # Extract just filename - import_statistics prepends /config/
                from pathlib import Path
                filename = Path(csv_file).name
                
                # Parameters passed directly, not nested
                # Include timezone so HA interprets timestamps correctly
                await self._hass.services.async_call(
                    "import_statistics",
                    "import_from_file",
                    {
                        "filename": filename,
                        "delimiter": ",",
                        "decimal": "dot ('.')",
                        "datetime_format": "%d.%m.%Y %H:%M",
                        "timezone_identifier": str(self._hass.config.time_zone),
                    },
                    blocking=True,
                )
                _LOGGER.info("Successfully imported: %s", csv_file)
            except Exception as err:
                _LOGGER.error("Import failed for %s: %s", csv_file, err)

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

        Args:
            gateway_url: Base gateway URL
            device_id: Optional specific device ID

        Returns:
            List of created file paths
        """
        if not gateway_url:
            _LOGGER.error("No gateway URL provided")
            return []

        gateway_url = gateway_url.rstrip("/")
        _LOGGER.info("Starting sync from %s", gateway_url)

        output_files: list[str] = []
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

                output_file = await convert_channel_csv(
                    hass=self._hass,
                    csv_data=csv_data,
                    hostname=hostname,
                    channel=channel,
                    config_path=self._hass.config.path(),
                )
                if output_file:
                    output_files.append(output_file)

        _LOGGER.info("Sync complete. Created %d files", len(output_files))
        return output_files

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
