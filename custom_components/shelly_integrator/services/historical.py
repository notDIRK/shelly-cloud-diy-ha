"""Historical data sync service for Shelly Integrator.

Handles fetching and importing historical energy data from EM devices.
Uses Home Assistant's native statistics API for direct import.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from homeassistant.components.recorder import get_instance as get_recorder_instance
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import CONF_LOCAL_GATEWAY_URL, HISTORICAL_SYNC_INTERVAL
from ..utils.csv_converter import parse_shelly_csv_for_import
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

        # Run initial sync once HA is fully started (event-driven,
        # no arbitrary delay).  If HA is already started when this
        # is called, the callback fires immediately.
        async_at_started(self._hass, self._on_ha_started)

        # Schedule recurring daily sync
        self._cancel_interval = async_track_time_interval(
            self._hass,
            self._run_auto_sync,
            timedelta(seconds=HISTORICAL_SYNC_INTERVAL),
        )

        _LOGGER.info(
            "Scheduled auto sync every %d hours",
            HISTORICAL_SYNC_INTERVAL // 3600
        )

    async def _on_ha_started(self, _hass: HomeAssistant) -> None:
        """Run initial historical sync after HA startup completes."""
        _LOGGER.info("HA started, running initial historical sync")
        await self._run_auto_sync()

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

    async def _get_recorder_sum(self, statistic_id: str) -> float | None:
        """Get the latest cumulative sum from HA's statistics database.

        Runs in the recorder's executor thread to avoid blocking the
        event loop (``statistics_during_period`` is a synchronous
        function that performs database I/O).

        Returns:
            Latest sum value, or ``None`` if no statistics exist yet.
        """
        try:
            instance = get_recorder_instance(self._hass)
            start_time = datetime.now(timezone.utc) - timedelta(hours=48)

            stats: dict = await instance.async_add_executor_job(
                statistics_during_period,
                self._hass,
                start_time,
                None,           # end_time
                {statistic_id},
                "5minute",
                None,           # units
                {"sum"},
            )

            if statistic_id in stats and stats[statistic_id]:
                latest = stats[statistic_id][-1]
                ha_sum = latest.get("sum", 0) or 0
                _LOGGER.debug(
                    "Recorder latest sum for %s: %.2f Wh",
                    statistic_id, ha_sum,
                )
                return ha_sum

        except Exception as err:
            _LOGGER.warning(
                "Could not query recorder for %s: %s", statistic_id, err
            )

        return None

    async def _import_statistics_native(
        self,
        statistic_id: str,
        data: list[tuple[datetime, float]],
    ) -> bool:
        """Import statistics using Home Assistant's native API.

        **Approach — CSV is the source of truth:**

        1. Import ALL data from the CSV (including today).
           ``async_import_statistics`` upserts, so existing rows for
           the same timestamps are overwritten with the correct values.
        2. Align the cumulative sum with the live recorder's tracking
           so there is no discontinuity at the transition between
           imported and live data.
        3. Self-healing: each sync run overwrites the data with the
           latest CSV values, correcting any previous misalignment.

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

            # STEP 1: Build raw cumulative sums from CSV deltas.
            csv_total = 0.0
            cumulative_list: list[float] = []
            for _, delta in data:
                csv_total += delta
                cumulative_list.append(csv_total)

            # STEP 2: Determine alignment offset.
            #
            # The live recorder maintains:
            #   sum = sensor_value − base_offset
            # where base_offset is the raw sensor value at entity creation.
            #
            # To align, we set:
            #   offset = sum_now − csv_total
            #
            # This makes the last imported sum ≈ sum_now, so the
            # recorder continues seamlessly.  The small error (energy
            # consumed between CSV's last timestamp and "now") is
            # self-corrected on the next sync when the CSV extends.
            sum_now = await self._get_recorder_sum(statistic_id)

            if sum_now is not None:
                offset = sum_now - csv_total
                _LOGGER.info(
                    "Alignment for %s: sum_now=%.2f, csv_total=%.2f, "
                    "offset=%.2f",
                    statistic_id, sum_now, csv_total, offset,
                )
            else:
                # First-time import — no recorder data yet.
                # Set offset so that the final imported sum = 0,
                # matching the recorder's initial sum at entity creation.
                offset = -csv_total
                _LOGGER.info(
                    "First import for %s (no prior stats), "
                    "csv_total=%.2f, offset=%.2f",
                    statistic_id, csv_total, offset,
                )

            # STEP 3: Build metadata
            metadata = StatisticMetaData(
                statistic_id=statistic_id,
                source="recorder",
                name=f"Shelly Energy ({statistic_id})",
                unit_of_measurement="Wh",
                has_sum=True,
                has_mean=False,
                mean_type=StatisticMeanType.NONE,
            )

            # STEP 4: Build statistics with aligned cumulative sums
            statistics: list[StatisticData] = []
            for i, (dt_utc, delta) in enumerate(data):
                statistics.append(
                    StatisticData(
                        start=dt_utc,
                        sum=cumulative_list[i] + offset,
                        state=delta,
                    )
                )

            # STEP 5: Import — upserts, overwriting existing rows
            async_import_statistics(self._hass, metadata, statistics)

            final_sum = cumulative_list[-1] + offset
            _LOGGER.info(
                "Imported %d statistics for %s "
                "(range: %s → %s, final sum: %.2f Wh)",
                len(statistics),
                statistic_id,
                data[0][0].isoformat(),
                data[-1][0].isoformat(),
                final_sum,
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
            num_channels = (
                3 if device_code in ("SHEM-3", "SPEM-003CEBEU")
                else 2
            )

            session = async_get_clientsession(self._hass)
            for channel in range(num_channels):
                # Resolve the actual entity_id from the entity
                # registry so the statistic_id always matches the
                # current naming, regardless of past renames.
                statistic_id = self._resolve_energy_entity_id(
                    dev_id, channel
                )
                if not statistic_id:
                    _LOGGER.warning(
                        "No energy entity for %s ch %d",
                        dev_id, channel,
                    )
                    continue

                csv_data = await fetch_csv_from_gateway(
                    gateway_url, hostname, channel,
                    session=session,
                )
                if not csv_data:
                    continue

                data = parse_shelly_csv_for_import(csv_data)
                if not data:
                    _LOGGER.warning(
                        "No valid data for %s channel %d",
                        hostname, channel,
                    )
                    continue

                success = await self._import_statistics_native(
                    statistic_id, data
                )
                if success:
                    imported_stats.append(statistic_id)

        _LOGGER.info("Sync complete. Imported %d statistics", len(imported_stats))
        return imported_stats

    def _resolve_energy_entity_id(
        self,
        device_id: str,
        channel: int,
    ) -> str | None:
        """Look up the current entity_id for an energy sensor.

        Uses the entity registry so the statistic_id always
        matches the live entity, regardless of naming changes.

        The unique_id pattern for emeter energy sensors is:
            ``{device_id}_emeter|energy_{channel}``
        """
        unique_id = f"{device_id}_emeter|energy_{channel}"
        ent_reg = er.async_get(self._hass)
        entry = ent_reg.async_get_entity_id(
            "sensor", "shelly_integrator", unique_id
        )
        if entry:
            _LOGGER.debug(
                "Resolved energy entity for %s ch %d: %s",
                device_id, channel, entry,
            )
        return entry

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
        """Get device hostname from available sources.

        Priority:
        1. status.getinfo.fw_info.device  (Gen1 local info)
        2. settings.device.hostname       (Shelly:Settings event)
        3. device_data.name               (DeviceVerify name)
        """
        # Priority 1: Gen1 status info
        status = device_data.get("status", {})
        getinfo = status.get("getinfo", {}).get("fw_info", {})
        hostname = getinfo.get("device")
        if hostname:
            return hostname

        # Priority 2: Settings from Shelly:Settings event
        settings = device_data.get("settings", {})
        if settings:
            dev = settings.get("device", {})
            hostname = dev.get("hostname")
            if hostname:
                return hostname

        # Priority 3: Name from DeviceVerify
        return device_data.get("name")
