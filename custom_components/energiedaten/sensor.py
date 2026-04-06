"""Energy sensors for energiedaten.at."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

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
    SensorEntity,
):
    """Energy sensor for a single meter/Zählpunkt + OBIS code."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    # No state_class — statistics are external, not derived from state changes

    def __init__(
        self,
        coordinator: EnergiedatenCoordinator,
        entry: EnergiedatenConfigEntry,
        meter: dict[str, Any],
        obis_code: str | None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
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
            self._attr_unique_id = (
                f"{entry.entry_id}_{self._meter_uuid}_{slugify(suffix)}"
            )
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
        self._attr_extra_state_attributes: dict[str, Any] = {
            "metering_point": meter["metering_point"],
            "energy_direction": direction,
            "granularity": "quarter_hour",
        }
        if obis_code:
            self._attr_extra_state_attributes["obis_code"] = obis_code

    @property
    def native_value(self) -> float | None:
        """Return the latest reading value from coordinator data."""
        all_readings = (self.coordinator.data or {}).get(self._meter_uuid, [])
        if not all_readings:
            return None

        if self._obis_code:
            readings = [
                r for r in all_readings if r.get("obis_code") == self._obis_code
            ]
        else:
            readings = all_readings

        if not readings:
            return None

        last = readings[-1]
        # Update dynamic attributes from latest reading
        self._attr_extra_state_attributes["data_quality"] = last.get(
            "quality", "unknown"
        )
        self._attr_extra_state_attributes["last_data_at"] = last.get("timestamp_end")

        return float(last["value"])
