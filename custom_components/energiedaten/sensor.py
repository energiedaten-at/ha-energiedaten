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
from homeassistant.util import slugify

from homeassistant_historical_sensor import (
    HistoricalSensor,
    HistoricalState,
    group_by_interval,
)

from . import EnergiedatenConfigEntry
from .const import DOMAIN
from .coordinator import EnergiedatenCoordinator

_LOGGER = logging.getLogger(__name__)

# Human-readable labels for OBIS code suffixes
OBIS_LABELS: dict[str, str] = {
    "G.01": "Measured",
    "G.01T": "Measured (EC)",
    "G.02": "EC Generation Share",
    "G.03": "Self-coverage (EC)",
    "G.03R": "Self-coverage Renewable",
    "P.01": "Grid",
    "P.01T": "Grid (EC)",
}


def _obis_suffix(obis_code: str) -> str:
    """Extract the suffix from an OBIS code like '1-1:2.9.0 G.01' → 'G.01'."""
    return obis_code.rsplit(" ", 1)[-1] if " " in obis_code else obis_code


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnergiedatenConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities from a config entry.

    Creates one sensor per (meter, obis_code) combination discovered
    in the coordinator's initial data fetch.
    """
    coordinator = entry.runtime_data.coordinator
    sensors: list[EnergiedatenSensor] = []

    for meter in entry.data["meters"]:
        uuid = meter["uuid"]
        readings = (coordinator.data or {}).get(uuid, [])

        # Discover distinct OBIS codes from the data
        obis_codes = sorted({r["obis_code"] for r in readings if "obis_code" in r})

        if obis_codes:
            for obis_code in obis_codes:
                sensors.append(
                    EnergiedatenSensor(coordinator, entry, meter, obis_code)
                )
        else:
            # No OBIS code in data (or no data yet) — create a single sensor
            sensors.append(
                EnergiedatenSensor(coordinator, entry, meter, None)
            )

    async_add_entities(sensors)


class EnergiedatenSensor(
    CoordinatorEntity[EnergiedatenCoordinator],
    HistoricalSensor,
    SensorEntity,
):
    """Historical energy sensor for a single meter/Zählpunkt + OBIS code."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    # DO NOT set _attr_state_class — ha-historical-sensor manages statistics directly

    def __init__(
        self,
        coordinator: EnergiedatenCoordinator,
        entry: EnergiedatenConfigEntry,
        meter: dict[str, Any],
        obis_code: str | None,
    ) -> None:
        """Initialize the historical sensor."""
        CoordinatorEntity.__init__(self, coordinator)
        HistoricalSensor.__init__(self)
        self._meter = meter
        self._meter_uuid: str = meter["uuid"]
        self._obis_code: str | None = obis_code

        direction = meter["energy_direction"]
        label = meter.get("label") or meter["metering_point"][-6:]
        direction_label = "Consumption" if direction == "consumption" else "Feed-in"

        if obis_code:
            suffix = _obis_suffix(obis_code)
            obis_label = OBIS_LABELS.get(suffix, suffix)
            self._attr_name = f"{direction_label} {obis_label}"
            self._attr_unique_id = f"{entry.entry_id}_{self._meter_uuid}_{slugify(suffix)}"
        else:
            self._attr_name = direction_label
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
        if obis_code:
            self._attr_extra_state_attributes["obis_code"] = obis_code

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
        """Process new data when coordinator updates.

        Intentionally skip super() to avoid async_write_ha_state() which
        would record a state entry at 'now' — this sensor only writes to
        long-term statistics via async_write_historical().
        """
        if self.coordinator.data:
            self.hass.async_create_task(self._async_process_readings())

    async def _async_process_readings(self) -> None:
        """Convert coordinator readings to HistoricalState and write to statistics."""
        all_readings = self.coordinator.data.get(self._meter_uuid, [])
        if not all_readings:
            return

        # Filter to this sensor's OBIS code
        if self._obis_code:
            readings = [r for r in all_readings if r.get("obis_code") == self._obis_code]
        else:
            readings = all_readings

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
