"""Tests for energiedaten.at historical sensors."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant

from homeassistant_historical_sensor import HistoricalSensor

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energiedaten.const import DOMAIN
from custom_components.energiedaten.sensor import EnergiedatenSensor


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator."""
    coord = MagicMock()
    coord.data = {"meter-1": []}
    coord.async_add_listener = MagicMock(return_value=MagicMock())
    coord.async_request_refresh = AsyncMock()
    return coord


@pytest.fixture
def meter_config():
    """Return a sample meter configuration dict."""
    return {
        "uuid": "meter-1",
        "metering_point": "AT0030000000000000000000000054321",
        "energy_direction": "consumption",
        "label": "Wohnung",
    }


@pytest.fixture
def mock_entry():
    """Return a mock config entry for sensor construction."""
    entry = MagicMock()
    entry.entry_id = "test-entry-id"
    return entry


def test_sensor_attributes(mock_coordinator, mock_entry, meter_config):
    """Sensor should have correct device_class, unit, and name."""
    sensor = EnergiedatenSensor(mock_coordinator, mock_entry, meter_config)

    assert sensor.device_class == SensorDeviceClass.ENERGY
    assert sensor.native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR
    assert sensor.name == "Consumption"
    assert sensor.unique_id == "test-entry-id_meter-1"


def test_sensor_no_state_class(mock_coordinator, mock_entry, meter_config):
    """Sensor must NOT set state_class — ha-historical-sensor manages statistics."""
    sensor = EnergiedatenSensor(mock_coordinator, mock_entry, meter_config)
    assert not hasattr(sensor, "_attr_state_class") or sensor._attr_state_class is None


def test_sensor_device_info(mock_coordinator, mock_entry, meter_config):
    """Sensor should have correct device info for HA device registry."""
    sensor = EnergiedatenSensor(mock_coordinator, mock_entry, meter_config)
    info = sensor.device_info

    assert (DOMAIN, "meter-1") in info["identifiers"]
    assert info["manufacturer"] == "energiedaten.at"
    assert info["model"] == "Smart Meter"


def test_sensor_extra_attributes(mock_coordinator, mock_entry, meter_config):
    """Sensor should expose metering_point and granularity as attributes."""
    sensor = EnergiedatenSensor(mock_coordinator, mock_entry, meter_config)
    attrs = sensor.extra_state_attributes

    assert attrs["metering_point"] == "AT0030000000000000000000000054321"
    assert attrs["energy_direction"] == "consumption"
    assert attrs["granularity"] == "quarter_hour"


def test_sensor_feed_in_naming(mock_coordinator, mock_entry):
    """Feed-in meter should have correct name."""
    meter = {
        "uuid": "meter-2",
        "metering_point": "AT0030000000000000000000000054322",
        "energy_direction": "feed_in",
        "label": "PV Anlage",
    }
    sensor = EnergiedatenSensor(mock_coordinator, mock_entry, meter)
    assert sensor.name == "Feed-in"


def test_sensor_no_label_device_uses_zaehlpunkt_suffix(mock_coordinator, mock_entry):
    """Meter without label should use last 6 chars of Zählpunkt for device name."""
    meter = {
        "uuid": "meter-3",
        "metering_point": "AT0030000000000000000000000054321",
        "energy_direction": "consumption",
        "label": None,
    }
    sensor = EnergiedatenSensor(mock_coordinator, mock_entry, meter)
    assert sensor.device_info["name"] == "054321"


def test_get_statistic_metadata_has_sum(mock_coordinator, mock_entry, meter_config):
    """Statistic metadata must have has_sum=True for energy dashboard."""
    sensor = EnergiedatenSensor(mock_coordinator, mock_entry, meter_config)

    with patch.object(
        HistoricalSensor,
        "get_statistic_metadata",
        return_value={"has_sum": False},
    ):
        meta = sensor.get_statistic_metadata()
    assert meta["has_sum"] is True


async def test_process_readings_updates_extra_attributes(
    mock_coordinator, mock_entry, meter_config
):
    """After processing readings, data_quality and last_data_at should be set."""
    sensor = EnergiedatenSensor(mock_coordinator, mock_entry, meter_config)
    sensor.hass = MagicMock()

    mock_coordinator.data = {
        "meter-1": [
            {
                "timestamp": "2026-03-15T14:00:00+00:00",
                "timestamp_end": "2026-03-15T14:15:00+00:00",
                "value": 0.3,
                "quality": "measured",
            },
        ]
    }
    mock_coordinator.update_last_fetched = MagicMock()

    with patch.object(sensor, "async_write_historical", new_callable=AsyncMock):
        await sensor._async_process_readings()

    assert sensor.extra_state_attributes["data_quality"] == "measured"
    assert sensor.extra_state_attributes["last_data_at"] == "2026-03-15T14:15:00+00:00"
    mock_coordinator.update_last_fetched.assert_called_once_with(
        "meter-1", "2026-03-15T14:15:00+00:00"
    )


async def test_process_readings_does_not_persist_on_write_failure(
    mock_coordinator, mock_entry, meter_config
):
    """If async_write_historical fails, last_fetched must NOT be updated."""
    sensor = EnergiedatenSensor(mock_coordinator, mock_entry, meter_config)
    sensor.hass = MagicMock()

    mock_coordinator.data = {
        "meter-1": [
            {
                "timestamp": "2026-03-15T14:00:00+00:00",
                "timestamp_end": "2026-03-15T14:15:00+00:00",
                "value": 0.3,
                "quality": "measured",
            },
        ]
    }
    mock_coordinator.update_last_fetched = MagicMock()

    with patch.object(
        sensor,
        "async_write_historical",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Recorder unavailable"),
    ):
        await sensor._async_process_readings()

    mock_coordinator.update_last_fetched.assert_not_called()
