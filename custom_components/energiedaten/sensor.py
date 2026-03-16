"""Historical energy sensors for energiedaten.at."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from homeassistant_historical_sensor import (
    HistoricalSensor,
    HistoricalState,
    group_by_interval,
)

from . import EnergiedatenConfigEntry
from .const import DOMAIN
from .coordinator import EnergiedatenCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnergiedatenConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities from a config entry."""
    coordinator = entry.runtime_data.coordinator
    sensors = [
        EnergiedatenSensor(coordinator, entry, meter)
        for meter in entry.data["meters"]
    ]
    async_add_entities(sensors)


class EnergiedatenSensor(
    CoordinatorEntity[EnergiedatenCoordinator],
    HistoricalSensor,
    SensorEntity,
):
    """Historical energy sensor for a single meter/Zählpunkt."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    # DO NOT set _attr_state_class — ha-historical-sensor manages statistics directly

    def __init__(
        self,
        coordinator: EnergiedatenCoordinator,
        entry: EnergiedatenConfigEntry,
        meter: dict[str, Any],
    ) -> None:
        """Initialize the historical sensor."""
        CoordinatorEntity.__init__(self, coordinator)
        HistoricalSensor.__init__(self)
        self._meter = meter
        self._meter_uuid: str = meter["uuid"]

        direction = meter["energy_direction"]
        label = meter.get("label") or meter["metering_point"][-6:]
        direction_label = "Consumption" if direction == "consumption" else "Feed-in"

        self._attr_name = f"{label} {direction_label}"
        self._attr_unique_id = f"{entry.entry_id}_{self._meter_uuid}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._meter_uuid)},
            name=label,
            manufacturer="energiedaten.at",
            model="Smart Meter",
            configuration_url="https://energiedaten.at",
        )
        self._attr_extra_state_attributes = {
            "metering_point": meter["metering_point"],
            "energy_direction": direction,
            "granularity": "quarter_hour",
        }

    def get_statistic_metadata(self) -> StatisticMetaData:
        """Enable cumulative sum for the energy dashboard."""
        meta = super().get_statistic_metadata()
        meta["has_sum"] = True
        return meta

    async def async_calculate_statistic_data(
        self,
        hist_states: list[HistoricalState],
        *,
        latest: Any = None,
    ) -> list[StatisticData]:
        """Convert interval readings into hourly cumulative statistics."""
        accumulated = latest["sum"] if latest else 0

        result = []
        for block_ts, block_states_iter in group_by_interval(
            hist_states, granularity=60 * 60
        ):
            block_states = list(block_states_iter)
            hour_sum = sum(s.state for s in block_states)
            accumulated += hour_sum

            result.append(
                StatisticData(
                    start=datetime.fromtimestamp(block_ts, tz=ZoneInfo("UTC")),
                    state=hour_sum,
                    sum=accumulated,
                )
            )

        return result

    async def async_added_to_hass(self) -> None:
        """Process initial data when entity is added to HA."""
        await super().async_added_to_hass()
        if self.coordinator.data:
            await self._async_process_readings()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Process new data when coordinator updates."""
        super()._handle_coordinator_update()
        if self.coordinator.data:
            self.hass.async_create_task(self._async_process_readings())

    async def _async_process_readings(self) -> None:
        """Convert coordinator readings to HistoricalState and write to statistics."""
        readings = self.coordinator.data.get(self._meter_uuid, [])
        if not readings:
            return

        self._attr_historical_states = [
            HistoricalState(
                state=float(r["value"]),
                timestamp=datetime.fromisoformat(r["timestamp_end"]).timestamp(),
            )
            for r in readings
        ]

        # Update extra state attributes from latest reading
        last = readings[-1]
        self._attr_extra_state_attributes["data_quality"] = last.get(
            "quality", "unknown"
        )
        self._attr_extra_state_attributes["last_data_at"] = last.get("timestamp_end")

        try:
            await self.async_write_historical()
        except Exception:
            _LOGGER.exception(
                "Failed to write historical statistics for %s", self._meter_uuid
            )
            return  # Don't advance last_fetched on failure

        # Only persist last_fetched after successful write to prevent data loss
        self.coordinator.update_last_fetched(
            self._meter_uuid, readings[-1]["timestamp_end"]
        )

    async def async_update_historical(self) -> None:
        """Not used — coordinator drives data fetching."""
