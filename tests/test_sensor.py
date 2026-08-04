"""Tests for energiedaten.at energy sensors."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import Platform, UnitOfEnergy

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.energiedaten.const import CONF_OBIS_CODES, DOMAIN
from custom_components.energiedaten.sensor import (
    EnergiedatenSensor,
    async_setup_entry,
)


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


def test_meter_device_hangs_off_the_account_device(
    mock_coordinator, mock_entry, meter_config
):
    """Meters are children of the account device, so HA nests them in the UI."""
    sensor = EnergiedatenSensor(mock_coordinator, mock_entry, meter_config, None)
    assert sensor.device_info["via_device"] == (DOMAIN, "test-entry-id")


def test_sensor_extra_attributes(mock_coordinator, mock_entry, meter_config):
    """Sensor should expose metering_point and granularity as attributes."""
    sensor = EnergiedatenSensor(mock_coordinator, mock_entry, meter_config, None)
    attrs = sensor.extra_state_attributes

    assert attrs["metering_point"] == "AT0030000000000000000000000054321"
    assert attrs["energy_direction"] == "consumption"
    assert attrs["granularity"] == "quarter_hour"


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
            {"timestamp_end": "2026-03-15T14:15:00+00:00", "value": 0.3, "quality": 1},
            {"timestamp_end": "2026-03-15T14:30:00+00:00", "value": 0.5, "quality": 1},
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
                "quality": 1,
            },
            {
                "timestamp_end": "2026-03-15T14:15:00+00:00",
                "value": 0.1,
                "obis_code": "1-1:1.9.0 P.01",
                "quality": 2,
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
                "quality": 1,
            },
        ]
    }
    sensor = EnergiedatenSensor(mock_coordinator, mock_entry, meter_config, None)
    _ = sensor.native_value

    assert sensor.extra_state_attributes["data_quality"] == "measured"
    assert sensor.extra_state_attributes["last_data_at"] == "2026-03-15T14:15:00+00:00"


@pytest.mark.parametrize(
    ("code", "label"),
    [(1, "measured"), (2, "estimated"), (3, "unreliable")],
)
def test_quality_code_is_mapped_to_a_readable_label(
    mock_coordinator, mock_entry, meter_config, code, label
):
    """The API sends quality as an int; templates shouldn't have to decode it."""
    mock_coordinator.data = {
        "meter-1": [
            {"timestamp_end": "2026-03-15T14:15:00+00:00", "value": 0.3, "quality": code}
        ]
    }
    sensor = EnergiedatenSensor(mock_coordinator, mock_entry, meter_config, None)
    _ = sensor.native_value

    assert sensor.extra_state_attributes["data_quality"] == label


def test_unrecognised_quality_code_is_passed_through(
    mock_coordinator, mock_entry, meter_config
):
    """A future quality level must stay diagnosable, not collapse to 'unknown'."""
    mock_coordinator.data = {
        "meter-1": [
            {"timestamp_end": "2026-03-15T14:15:00+00:00", "value": 0.3, "quality": 4}
        ]
    }
    sensor = EnergiedatenSensor(mock_coordinator, mock_entry, meter_config, None)
    _ = sensor.native_value

    assert sensor.extra_state_attributes["data_quality"] == 4


def test_missing_quality_reports_unknown(mock_coordinator, mock_entry, meter_config):
    """A reading with no quality field at all is reported as unknown."""
    mock_coordinator.data = {
        "meter-1": [{"timestamp_end": "2026-03-15T14:15:00+00:00", "value": 0.3}]
    }
    sensor = EnergiedatenSensor(mock_coordinator, mock_entry, meter_config, None)
    _ = sensor.native_value

    assert sensor.extra_state_attributes["data_quality"] == "unknown"


async def test_sensors_are_built_from_the_entry_not_the_last_poll(
    hass: HomeAssistant, mock_coordinator, meter_config
):
    """A poll returning nothing must not wipe out the meter's sensors.

    Cursor sync legitimately returns an empty page when nothing changed, which
    is the normal steady state. Deriving the sensor set from coordinator.data
    meant a restart at that moment registered one useless bare sensor and
    dropped every real one.
    """
    entry = MagicMock()
    entry.entry_id = "test-entry-id"
    entry.data = {
        "meters": [meter_config],
        CONF_OBIS_CODES: {"meter-1": ["1-1:1.9.0 G.01", "1-1:1.9.0 P.01"]},
    }
    mock_coordinator.data = {}
    entry.runtime_data.coordinator = mock_coordinator

    added: list[EnergiedatenSensor] = []
    await async_setup_entry(hass, entry, added.extend)

    assert sorted(s._obis_code for s in added) == [
        "1-1:1.9.0 G.01",
        "1-1:1.9.0 P.01",
    ]


async def test_meter_with_no_known_obis_codes_gets_a_single_sensor(
    hass: HomeAssistant, mock_coordinator, meter_config
):
    """A meter that has never reported an OBIS code still gets one sensor."""
    entry = MagicMock()
    entry.entry_id = "test-entry-id"
    entry.data = {"meters": [meter_config]}
    mock_coordinator.data = {}
    entry.runtime_data.coordinator = mock_coordinator

    added: list[EnergiedatenSensor] = []
    await async_setup_entry(hass, entry, added.extend)

    assert [s._obis_code for s in added] == [None]


async def test_stale_bare_sensor_is_removed_once_obis_codes_are_known(
    hass: HomeAssistant, mock_coordinator, meter_config
):
    """Clean up the dead sensor older versions registered during an empty poll.

    It has no OBIS code, so it can never match a reading and stays
    `unavailable` forever. Leaving it behind means every affected install
    carries a phantom entity the user has to delete by hand.
    """
    registry = er.async_get(hass)
    stale = registry.async_get_or_create(
        Platform.SENSOR, DOMAIN, "test-entry-id_meter-1"
    )

    entry = MagicMock()
    entry.entry_id = "test-entry-id"
    entry.data = {
        "meters": [meter_config],
        CONF_OBIS_CODES: {"meter-1": ["1-1:1.9.0 G.01"]},
    }
    mock_coordinator.data = {}
    entry.runtime_data.coordinator = mock_coordinator

    await async_setup_entry(hass, entry, [].extend)

    assert registry.async_get(stale.entity_id) is None


async def test_bare_sensor_is_kept_when_no_obis_codes_are_known(
    hass: HomeAssistant, mock_coordinator, meter_config
):
    """A meter that genuinely reports no OBIS code keeps its single sensor."""
    registry = er.async_get(hass)
    kept = registry.async_get_or_create(
        Platform.SENSOR, DOMAIN, "test-entry-id_meter-1"
    )

    entry = MagicMock()
    entry.entry_id = "test-entry-id"
    entry.data = {"meters": [meter_config], CONF_OBIS_CODES: {"meter-1": []}}
    mock_coordinator.data = {}
    entry.runtime_data.coordinator = mock_coordinator

    await async_setup_entry(hass, entry, [].extend)

    assert registry.async_get(kept.entity_id) is not None
