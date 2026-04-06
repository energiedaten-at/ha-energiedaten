"""Tests for energiedaten.at energy sensors."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import UnitOfEnergy

from custom_components.energiedaten.const import DOMAIN
from custom_components.energiedaten.sensor import EnergiedatenSensor


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator."""
    coord = MagicMock()
    coord.data = {"meter-1": []}
    coord.async_add_listener = MagicMock(return_value=MagicMock())
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
    sensor = EnergiedatenSensor(mock_coordinator, mock_entry, meter_config, None)

    assert sensor.device_class == SensorDeviceClass.ENERGY
    assert sensor.native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR
    assert sensor.name == "Consumption"
    assert sensor.unique_id == "test-entry-id_meter-1"


def test_sensor_no_state_class(mock_coordinator, mock_entry, meter_config):
    """Sensor must NOT set state_class — statistics are external."""
    sensor = EnergiedatenSensor(mock_coordinator, mock_entry, meter_config, None)
    assert not hasattr(sensor, "_attr_state_class") or sensor._attr_state_class is None


def test_sensor_device_info(mock_coordinator, mock_entry, meter_config):
    """Sensor should have correct device info for HA device registry."""
    sensor = EnergiedatenSensor(mock_coordinator, mock_entry, meter_config, None)
    info = sensor.device_info

    assert (DOMAIN, "meter-1") in info["identifiers"]
    assert info["manufacturer"] == "energiedaten.at"
    assert info["model"] == "Smart Meter"


def test_sensor_extra_attributes(mock_coordinator, mock_entry, meter_config):
    """Sensor should expose metering_point and granularity as attributes."""
    sensor = EnergiedatenSensor(mock_coordinator, mock_entry, meter_config, None)
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
    sensor = EnergiedatenSensor(mock_coordinator, mock_entry, meter, None)
    assert sensor.name == "Feed-in"


def test_sensor_no_label_device_uses_zaehlpunkt_suffix(mock_coordinator, mock_entry):
    """Meter without label should use last 6 chars of Zählpunkt for device name."""
    meter = {
        "uuid": "meter-3",
        "metering_point": "AT0030000000000000000000000054321",
        "energy_direction": "consumption",
        "label": None,
    }
    sensor = EnergiedatenSensor(mock_coordinator, mock_entry, meter, None)
    assert sensor.device_info["name"] == "054321"


def test_sensor_with_obis_code_naming(mock_coordinator, mock_entry, meter_config):
    """Sensor with OBIS code should include OBIS label in name."""
    sensor = EnergiedatenSensor(
        mock_coordinator, mock_entry, meter_config, "1-1:1.9.0 G.01"
    )
    assert sensor.name == "Consumption Measured"
    assert "g_01" in sensor.unique_id
    assert sensor.extra_state_attributes["obis_code"] == "1-1:1.9.0 G.01"


def test_sensor_with_obis_code_grid(mock_coordinator, mock_entry):
    """Feed-in sensor with P.01 OBIS code should show 'Grid' label."""
    meter = {
        "uuid": "meter-2",
        "metering_point": "AT0030000000000000000000000054322",
        "energy_direction": "feed_in",
        "label": "PV Anlage",
    }
    sensor = EnergiedatenSensor(
        mock_coordinator, mock_entry, meter, "1-1:2.9.0 P.01"
    )
    assert sensor.name == "Feed-in Grid"


def test_native_value_returns_latest_reading(mock_coordinator, mock_entry, meter_config):
    """native_value should return the value from the latest reading."""
    mock_coordinator.data = {
        "meter-1": [
            {"timestamp_end": "2026-03-15T14:15:00+00:00", "value": 0.3, "quality": "measured"},
            {"timestamp_end": "2026-03-15T14:30:00+00:00", "value": 0.5, "quality": "measured"},
        ]
    }
    sensor = EnergiedatenSensor(mock_coordinator, mock_entry, meter_config, None)
    assert sensor.native_value == 0.5


def test_native_value_none_when_no_data(mock_coordinator, mock_entry, meter_config):
    """native_value should return None when coordinator has no data."""
    mock_coordinator.data = {"meter-1": []}
    sensor = EnergiedatenSensor(mock_coordinator, mock_entry, meter_config, None)
    assert sensor.native_value is None


def test_native_value_filters_by_obis_code(mock_coordinator, mock_entry, meter_config):
    """native_value should only use readings matching this sensor's OBIS code."""
    mock_coordinator.data = {
        "meter-1": [
            {
                "timestamp_end": "2026-03-15T14:15:00+00:00",
                "value": 0.3,
                "obis_code": "1-1:1.9.0 G.01",
                "quality": "measured",
            },
            {
                "timestamp_end": "2026-03-15T14:15:00+00:00",
                "value": 0.1,
                "obis_code": "1-1:1.9.0 P.01",
                "quality": "estimated",
            },
        ]
    }
    sensor = EnergiedatenSensor(
        mock_coordinator, mock_entry, meter_config, "1-1:1.9.0 G.01"
    )
    assert sensor.native_value == 0.3


def test_native_value_updates_dynamic_attributes(
    mock_coordinator, mock_entry, meter_config
):
    """Accessing native_value should update data_quality and last_data_at."""
    mock_coordinator.data = {
        "meter-1": [
            {
                "timestamp_end": "2026-03-15T14:15:00+00:00",
                "value": 0.3,
                "quality": "measured",
            },
        ]
    }
    sensor = EnergiedatenSensor(mock_coordinator, mock_entry, meter_config, None)
    _ = sensor.native_value

    assert sensor.extra_state_attributes["data_quality"] == "measured"
    assert sensor.extra_state_attributes["last_data_at"] == "2026-03-15T14:15:00+00:00"
