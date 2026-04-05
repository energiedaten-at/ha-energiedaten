# Statistics Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drop `homeassistant-historical-sensor` and write statistics directly via `async_add_external_statistics()` in the coordinator, with delta sync and day-level correction re-fetch.

**Architecture:** The coordinator owns both data fetching (delta sync via `updated_since`) and statistics writing (via `async_add_external_statistics`). Sensors become pure `CoordinatorEntity + SensorEntity` that expose the latest reading as `native_value` and extra attributes. Corrections are detected by comparing fetched record timestamps against the recorder, then re-fetching affected days from the API.

**Tech Stack:** Home Assistant core (`async_add_external_statistics`, `get_last_statistics`, `statistics_during_period`), `aiohttp`, Python 3.13.

**Spec:** `docs/superpowers/specs/2026-04-06-statistics-refactor-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `custom_components/energiedaten/coordinator.py` | Rewrite | Fetch data, detect corrections, compute hourly statistics, write via `async_add_external_statistics`, manage watermarks |
| `custom_components/energiedaten/sensor.py` | Rewrite | Pure state-exposing `CoordinatorEntity + SensorEntity` — `native_value`, attributes, device info |
| `custom_components/energiedaten/manifest.json` | Modify | Remove `homeassistant-historical-sensor` from requirements |
| `custom_components/energiedaten/const.py` | No change | Already has `CONF_WATERMARKS` |
| `custom_components/energiedaten/__init__.py` | No change | Already correct |
| `custom_components/energiedaten/api.py` | No change | Already has `MeterDataResult` and `updated_since` |
| `custom_components/energiedaten/button.py` | No change | Already correct |
| `tests/test_coordinator.py` | Rewrite | Test statistics computation, correction detection, anchored sums, watermark persistence |
| `tests/test_sensor.py` | Rewrite | Test entity attributes, `native_value`, device info, OBIS naming — no statistics tests |
| `tests/conftest.py` | Modify | Update recorder mock for new API |

---

## Task 1: Simplify sensor to pure state entity

Remove `HistoricalSensor` and all statistics logic from the sensor. The sensor becomes a simple `CoordinatorEntity + SensorEntity`.

**Files:**
- Modify: `custom_components/energiedaten/sensor.py`

- [ ] **Step 1: Rewrite sensor.py**

Replace the entire file. Remove `HistoricalSensor`, `HistoricalState`, `group_by_interval`, `get_statistic_metadata`, `async_calculate_statistic_data`, `_async_process_readings`, `_handle_coordinator_update`, `async_update_historical`, and `async_added_to_hass`. Keep `OBIS_LABELS`, `_obis_suffix`, `async_setup_entry`, device info, naming, and attributes. Add `native_value`.

```python
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
```

- [ ] **Step 2: Run existing sensor tests to see what breaks**

Run: `/opt/homebrew/Caskroom/miniconda/base/envs/ha-energiedaten/bin/python -m pytest tests/test_sensor.py -v`

Expected: Multiple failures — tests reference `HistoricalSensor`, `_async_process_readings`, `async_write_historical`, `update_watermark`, etc.

- [ ] **Step 3: Commit sensor simplification**

```bash
git add custom_components/energiedaten/sensor.py
git commit -m "refactor: simplify sensor to pure CoordinatorEntity + SensorEntity

Remove HistoricalSensor base class and all statistics-related methods.
Sensor now exposes native_value from coordinator data and extra
attributes. Statistics writing moves to the coordinator."
```

---

## Task 2: Rewrite sensor tests

Remove all statistics-related tests. Keep entity attribute, naming, and device info tests. Add `native_value` tests.

**Files:**
- Rewrite: `tests/test_sensor.py`

- [ ] **Step 1: Rewrite test_sensor.py**

```python
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
```

- [ ] **Step 2: Run sensor tests**

Run: `/opt/homebrew/Caskroom/miniconda/base/envs/ha-energiedaten/bin/python -m pytest tests/test_sensor.py -v`

Expected: All 12 tests PASS.

- [ ] **Step 3: Commit sensor tests**

```bash
git add tests/test_sensor.py
git commit -m "test: rewrite sensor tests for pure state entity

Remove all statistics-related tests (process_readings, write_historical,
watermark assertions). Add native_value tests with OBIS filtering and
dynamic attribute updates."
```

---

## Task 3: Remove `homeassistant-historical-sensor` dependency

**Files:**
- Modify: `custom_components/energiedaten/manifest.json`

- [ ] **Step 1: Remove the requirement from manifest.json**

In `manifest.json`, change:
```json
"requirements": ["homeassistant-historical-sensor>=3.0.0a3"],
```
to:
```json
"requirements": [],
```

- [ ] **Step 2: Run all tests to confirm nothing still imports the library**

Run: `/opt/homebrew/Caskroom/miniconda/base/envs/ha-energiedaten/bin/python -m pytest tests/ -v`

Expected: All tests pass. No import errors for `homeassistant_historical_sensor`.

- [ ] **Step 3: Commit**

```bash
git add custom_components/energiedaten/manifest.json
git commit -m "chore: remove homeassistant-historical-sensor dependency

Statistics are now written directly via async_add_external_statistics
in the coordinator."
```

---

## Task 4: Add statistics computation helper to coordinator

Add the core logic for grouping quarter-hour readings into hourly statistics with cumulative sums. This is a pure function that's easy to test in isolation.

**Files:**
- Modify: `custom_components/energiedaten/coordinator.py`
- Create: `tests/test_statistics.py`

- [ ] **Step 1: Write failing tests for `_build_hourly_statistics`**

Create `tests/test_statistics.py`:

```python
"""Tests for hourly statistics computation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from custom_components.energiedaten.coordinator import _build_hourly_statistics


def test_single_hour_four_readings():
    """Four quarter-hour readings in one hour produce one statistic."""
    readings = [
        {"timestamp": "2026-03-15T14:00:00+00:00", "value": 0.1},
        {"timestamp": "2026-03-15T14:15:00+00:00", "value": 0.2},
        {"timestamp": "2026-03-15T14:30:00+00:00", "value": 0.3},
        {"timestamp": "2026-03-15T14:45:00+00:00", "value": 0.4},
    ]
    stats = _build_hourly_statistics(readings, anchor_sum=0.0)

    assert len(stats) == 1
    assert stats[0]["start"] == datetime(2026, 3, 15, 14, tzinfo=timezone.utc)
    assert stats[0]["state"] == pytest.approx(1.0)  # 0.1+0.2+0.3+0.4
    assert stats[0]["sum"] == pytest.approx(1.0)     # 0 + 1.0


def test_two_hours():
    """Readings spanning two hours produce two statistics with running sum."""
    readings = [
        {"timestamp": "2026-03-15T14:00:00+00:00", "value": 0.25},
        {"timestamp": "2026-03-15T14:15:00+00:00", "value": 0.25},
        {"timestamp": "2026-03-15T14:30:00+00:00", "value": 0.25},
        {"timestamp": "2026-03-15T14:45:00+00:00", "value": 0.25},
        {"timestamp": "2026-03-15T15:00:00+00:00", "value": 0.5},
        {"timestamp": "2026-03-15T15:15:00+00:00", "value": 0.5},
        {"timestamp": "2026-03-15T15:30:00+00:00", "value": 0.5},
        {"timestamp": "2026-03-15T15:45:00+00:00", "value": 0.5},
    ]
    stats = _build_hourly_statistics(readings, anchor_sum=0.0)

    assert len(stats) == 2
    assert stats[0]["state"] == pytest.approx(1.0)
    assert stats[0]["sum"] == pytest.approx(1.0)
    assert stats[1]["state"] == pytest.approx(2.0)
    assert stats[1]["sum"] == pytest.approx(3.0)  # 1.0 + 2.0


def test_anchor_sum_offsets_cumulative():
    """Non-zero anchor sum should offset all cumulative sums."""
    readings = [
        {"timestamp": "2026-03-15T14:00:00+00:00", "value": 0.5},
        {"timestamp": "2026-03-15T14:15:00+00:00", "value": 0.5},
    ]
    stats = _build_hourly_statistics(readings, anchor_sum=100.0)

    assert len(stats) == 1
    assert stats[0]["state"] == pytest.approx(1.0)
    assert stats[0]["sum"] == pytest.approx(101.0)  # 100 + 1.0


def test_empty_readings():
    """Empty readings list should return empty statistics."""
    stats = _build_hourly_statistics([], anchor_sum=0.0)
    assert stats == []


def test_partial_hour():
    """Incomplete hour (fewer than 4 readings) should still produce a statistic."""
    readings = [
        {"timestamp": "2026-03-15T14:00:00+00:00", "value": 0.3},
    ]
    stats = _build_hourly_statistics(readings, anchor_sum=5.0)

    assert len(stats) == 1
    assert stats[0]["state"] == pytest.approx(0.3)
    assert stats[0]["sum"] == pytest.approx(5.3)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/opt/homebrew/Caskroom/miniconda/base/envs/ha-energiedaten/bin/python -m pytest tests/test_statistics.py -v`

Expected: FAIL — `ImportError: cannot import name '_build_hourly_statistics' from 'custom_components.energiedaten.coordinator'`

- [ ] **Step 3: Implement `_build_hourly_statistics` in coordinator.py**

Add this function to `coordinator.py` (before the class definition):

```python
from datetime import datetime, timedelta, timezone
from itertools import groupby


def _hour_key(reading: dict[str, Any]) -> datetime:
    """Return the start-of-hour for a reading's timestamp."""
    ts = datetime.fromisoformat(reading["timestamp"])
    return ts.replace(minute=0, second=0, microsecond=0)


def _build_hourly_statistics(
    readings: list[dict[str, Any]],
    anchor_sum: float,
) -> list[dict[str, Any]]:
    """Group quarter-hour readings into hourly statistics with cumulative sum.

    Returns a list of dicts with keys: start (datetime), state (float), sum (float).
    """
    if not readings:
        return []

    result: list[dict[str, Any]] = []
    accumulated = anchor_sum

    for hour_start, group in groupby(readings, key=_hour_key):
        hour_sum = sum(float(r["value"]) for r in group)
        accumulated += hour_sum
        result.append({
            "start": hour_start,
            "state": hour_sum,
            "sum": accumulated,
        })

    return result
```

Also add `from itertools import groupby` to the imports at the top of `coordinator.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `/opt/homebrew/Caskroom/miniconda/base/envs/ha-energiedaten/bin/python -m pytest tests/test_statistics.py -v`

Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/energiedaten/coordinator.py tests/test_statistics.py
git commit -m "feat: add hourly statistics computation with anchored sums

Pure function that groups quarter-hour readings into hourly statistics
with cumulative sum. Anchor sum parameter allows correct continuation
from existing recorder data."
```

---

## Task 5: Add statistics writing to the coordinator

Wire up `async_add_external_statistics` in the coordinator's `_async_update_data`. For this task, handle the "first sync" and "normal sync" cases (no corrections yet).

**Files:**
- Modify: `custom_components/energiedaten/coordinator.py`
- Modify: `tests/test_coordinator.py`

- [ ] **Step 1: Write failing test for first sync statistics writing**

Add to `tests/test_coordinator.py`:

```python
from unittest.mock import patch, call
from custom_components.energiedaten.coordinator import _build_hourly_statistics


async def test_first_sync_writes_statistics(coordinator, mock_client):
    """First sync should compute statistics from scratch and write them."""
    readings = [
        {
            "timestamp": "2026-03-15T14:00:00+00:00",
            "timestamp_end": "2026-03-15T14:15:00+00:00",
            "value": 0.3,
            "obis_code": "1-1:1.9.0 G.01",
        },
        {
            "timestamp": "2026-03-15T14:15:00+00:00",
            "timestamp_end": "2026-03-15T14:30:00+00:00",
            "value": 0.4,
            "obis_code": "1-1:1.9.0 G.01",
        },
    ]
    mock_client.async_get_meter_data.return_value = MeterDataResult(
        readings=readings,
        max_updated_at="2026-03-15T15:00:00+00:00",
    )

    with patch(
        "custom_components.energiedaten.coordinator.async_add_external_statistics"
    ) as mock_add_stats:
        await coordinator._async_update_data()

    # Should write statistics for the discovered OBIS group
    assert mock_add_stats.call_count >= 1
    meta = mock_add_stats.call_args_list[0][0][1]  # second positional arg
    assert meta["has_sum"] is True
    assert meta["source"] == "energiedaten"
    assert "AT0030000000000000000000000054321" in meta["statistic_id"]
```

- [ ] **Step 2: Write failing test for normal sync with anchored sum**

Add to `tests/test_coordinator.py`:

```python
async def test_normal_sync_anchors_sum_from_recorder(hass, mock_client):
    """Normal sync should query recorder for anchor and accumulate forward."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "token": "t",
            "team_slug": "s",
            "meters": [
                {
                    "uuid": "m1",
                    "metering_point": "AT0030000000000000000000000054321",
                    "energy_direction": "consumption",
                    "label": "X",
                }
            ],
            "watermarks": {"m1": "2026-03-15T14:30:00+00:00"},
        },
    )
    entry.add_to_hass(hass)
    coord = EnergiedatenCoordinator(hass, entry, mock_client)

    mock_client.async_get_meter_data.return_value = MeterDataResult(
        readings=[
            {
                "timestamp": "2026-03-15T15:00:00+00:00",
                "timestamp_end": "2026-03-15T15:15:00+00:00",
                "value": 0.5,
                "obis_code": "1-1:1.9.0 G.01",
            },
        ],
        max_updated_at="2026-03-15T16:00:00+00:00",
    )

    # Mock get_last_statistics to return an existing sum of 100.0
    mock_last_stats = {
        "energiedaten:AT0030000000000000000000000054321_measured": [
            {"start": 1742050800.0, "sum": 100.0}
        ]
    }

    with (
        patch(
            "custom_components.energiedaten.coordinator.async_add_external_statistics"
        ) as mock_add_stats,
        patch(
            "custom_components.energiedaten.coordinator.get_last_statistics",
            return_value=mock_last_stats,
        ),
        patch(
            "custom_components.energiedaten.coordinator.get_instance"
        ) as mock_get_instance,
    ):
        mock_get_instance.return_value.async_add_executor_job = AsyncMock(
            return_value=mock_last_stats
        )
        await coord._async_update_data()

    # Should write with anchored sum: 100.0 + 0.5 = 100.5
    assert mock_add_stats.call_count >= 1
    stats_data = mock_add_stats.call_args_list[0][0][2]  # third positional arg
    stats_list = list(stats_data)
    assert stats_list[0]["sum"] == pytest.approx(100.5)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `/opt/homebrew/Caskroom/miniconda/base/envs/ha-energiedaten/bin/python -m pytest tests/test_coordinator.py::test_first_sync_writes_statistics tests/test_coordinator.py::test_normal_sync_anchors_sum_from_recorder -v`

Expected: FAIL — `async_add_external_statistics` not yet called in coordinator.

- [ ] **Step 4: Implement statistics writing in coordinator**

Rewrite `coordinator.py` to add statistics writing after data fetching. The full updated file:

```python
"""DataUpdateCoordinator for energiedaten.at."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from itertools import groupby
import logging
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import AuthenticationError, EnergiedatenApiClient, RateLimitError
from .const import CONF_METERS, CONF_WATERMARKS, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Far-past date for history import — API clamps to retention window
_HISTORY_START = datetime(2020, 1, 1, tzinfo=timezone.utc)

# Human-readable labels for OBIS code suffixes (for statistic IDs)
_OBIS_STAT_NAMES: dict[str, str] = {
    "G.01": "measured",
    "G.01T": "measured_ec",
    "G.02": "ec_generation_share",
    "G.03": "self_coverage_ec",
    "G.03R": "self_coverage_renewable",
    "P.01": "grid",
    "P.01T": "grid_ec",
}


def _obis_suffix(obis_code: str) -> str:
    """Extract the suffix from an OBIS code like '1-1:2.9.0 G.01' → 'G.01'."""
    return obis_code.rsplit(" ", 1)[-1] if " " in obis_code else obis_code


def _hour_key(reading: dict[str, Any]) -> datetime:
    """Return the start-of-hour for a reading's timestamp."""
    ts = datetime.fromisoformat(reading["timestamp"])
    return ts.replace(minute=0, second=0, microsecond=0)


def _build_hourly_statistics(
    readings: list[dict[str, Any]],
    anchor_sum: float,
) -> list[dict[str, Any]]:
    """Group quarter-hour readings into hourly statistics with cumulative sum.

    Returns a list of dicts with keys: start (datetime), state (float), sum (float).
    """
    if not readings:
        return []

    result: list[dict[str, Any]] = []
    accumulated = anchor_sum

    for hour_start, group in groupby(readings, key=_hour_key):
        hour_sum = sum(float(r["value"]) for r in group)
        accumulated += hour_sum
        result.append({
            "start": hour_start,
            "state": hour_sum,
            "sum": accumulated,
        })

    return result


def _statistic_id(metering_point: str, obis_code: str | None) -> str:
    """Build external statistic ID from metering point and OBIS code."""
    if obis_code:
        suffix = _obis_suffix(obis_code)
        name = _OBIS_STAT_NAMES.get(suffix, suffix.lower().replace(".", "_"))
        return f"{DOMAIN}:{metering_point}_{name}"
    return f"{DOMAIN}:{metering_point}"


class EnergiedatenCoordinator(DataUpdateCoordinator[dict[str, list[dict[str, Any]]]]):
    """Fetch meter data from energiedaten.at every 6 hours."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: EnergiedatenApiClient,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=config_entry,
            update_interval=timedelta(hours=6),
        )
        self.client = client
        self._pending_watermarks: dict[str, str] = {}

    async def _async_update_data(self) -> dict[str, list[dict[str, Any]]]:
        """Fetch new readings for each meter and write statistics."""
        meters = self.config_entry.data.get(CONF_METERS, [])
        watermarks: dict[str, str] = dict(
            self.config_entry.data.get(CONF_WATERMARKS, {})
        )
        now = datetime.now(timezone.utc)
        result: dict[str, list[dict[str, Any]]] = {}
        self._pending_watermarks = {}

        for meter in meters:
            uuid = meter["uuid"]
            metering_point = meter["metering_point"]
            has_watermark = uuid in watermarks

            try:
                meter_result = await self.client.async_get_meter_data(
                    uuid,
                    _HISTORY_START,
                    now,
                    updated_since=watermarks.get(uuid),
                )
            except AuthenticationError as err:
                raise ConfigEntryAuthFailed from err
            except RateLimitError as err:
                raise UpdateFailed("Rate limited, will retry next cycle") from err

            result[uuid] = meter_result.readings

            if not meter_result.readings:
                continue

            # Group readings by OBIS code
            obis_groups: dict[str | None, list[dict[str, Any]]] = {}
            for reading in meter_result.readings:
                key = reading.get("obis_code")
                obis_groups.setdefault(key, []).append(reading)

            # Write statistics for each OBIS group
            for obis_code, readings in obis_groups.items():
                stat_id = _statistic_id(metering_point, obis_code)

                # Determine anchor sum
                anchor_sum = 0.0
                if has_watermark:
                    row = await self._get_last_sum_row(stat_id)
                    anchor_sum = (row.get("sum", 0.0) or 0.0) if row else 0.0

                hourly = _build_hourly_statistics(readings, anchor_sum)
                if not hourly:
                    continue

                metadata = StatisticMetaData(
                    has_sum=True,
                    mean_type=StatisticMeanType.NONE,
                    name=None,
                    source=DOMAIN,
                    statistic_id=stat_id,
                    unit_class="energy",
                    unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                )
                stat_data = [
                    StatisticData(
                        start=h["start"],
                        state=h["state"],
                        sum=h["sum"],
                    )
                    for h in hourly
                ]
                async_add_external_statistics(self.hass, metadata, stat_data)

            # Only advance watermark after successful statistics write
            if meter_result.max_updated_at:
                self._pending_watermarks[uuid] = meter_result.max_updated_at
                self._persist_watermark(uuid)

        return result

    async def _get_last_sum_row(self, statistic_id: str) -> dict | None:
        """Query recorder for the latest statistic row (start + sum)."""
        result = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, statistic_id, False, {"sum"}
        )
        rows = result.get(statistic_id, [])
        return rows[0] if rows else None

    def _persist_watermark(self, meter_uuid: str) -> None:
        """Persist the delta-sync watermark for a meter."""
        watermark = self._pending_watermarks.get(meter_uuid)
        if not watermark:
            return
        watermarks = dict(
            self.config_entry.data.get(CONF_WATERMARKS, {})
        )
        watermarks[meter_uuid] = watermark
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={**self.config_entry.data, CONF_WATERMARKS: watermarks},
        )
```

- [ ] **Step 5: Run all coordinator tests**

Run: `/opt/homebrew/Caskroom/miniconda/base/envs/ha-energiedaten/bin/python -m pytest tests/test_coordinator.py -v`

Expected: New tests PASS. Some old tests may need minor updates (the `update_watermark` method was renamed to `_persist_watermark` and is now called internally). Fix those in the next step.

- [ ] **Step 6: Update existing coordinator tests for new API**

The old `test_update_watermark_persists` and `test_update_watermark_noop_without_pending` tests reference the removed public `update_watermark` method. Update `tests/test_coordinator.py` to replace them:

```python
async def test_watermark_persisted_after_statistics_write(coordinator, mock_client):
    """Watermark should be saved after successful statistics write."""
    mock_client.async_get_meter_data.return_value = MeterDataResult(
        readings=[
            {
                "timestamp": "2026-03-15T14:00:00+00:00",
                "timestamp_end": "2026-03-15T14:15:00+00:00",
                "value": 0.3,
                "obis_code": "1-1:1.9.0 G.01",
            },
        ],
        max_updated_at="2026-03-15T15:00:00+00:00",
    )

    with patch(
        "custom_components.energiedaten.coordinator.async_add_external_statistics"
    ):
        await coordinator._async_update_data()

    watermarks = coordinator.config_entry.data.get("watermarks", {})
    assert watermarks.get("meter-1") == "2026-03-15T15:00:00+00:00"


async def test_no_watermark_on_empty_response(coordinator, mock_client):
    """No watermark should be persisted when there are no readings."""
    mock_client.async_get_meter_data.return_value = MeterDataResult(
        readings=[], max_updated_at=None
    )
    await coordinator._async_update_data()
    assert "watermarks" not in coordinator.config_entry.data
```

Also remove the old `test_update_watermark_persists` and `test_update_watermark_noop_without_pending` tests.

- [ ] **Step 7: Run all tests**

Run: `/opt/homebrew/Caskroom/miniconda/base/envs/ha-energiedaten/bin/python -m pytest tests/ -v`

Expected: All tests PASS.

- [ ] **Step 8: Commit**

```bash
git add custom_components/energiedaten/coordinator.py tests/test_coordinator.py
git commit -m "feat: write external statistics from coordinator

Coordinator now calls async_add_external_statistics after fetching data.
First sync computes statistics from scratch (anchor=0). Normal sync
queries recorder for latest sum as anchor. Watermark is persisted
internally after successful write."
```

---

## Task 6: Add correction detection and day-level re-fetch

When delta sync returns records whose timestamps fall within already-imported hours, detect this as a correction and re-fetch the affected day(s) from the API.

**Files:**
- Modify: `custom_components/energiedaten/coordinator.py`
- Modify: `tests/test_coordinator.py`

- [ ] **Step 1: Write failing test for correction detection**

Add to `tests/test_coordinator.py`:

```python
async def test_correction_triggers_day_refetch(hass, mock_client):
    """Records within already-imported hours should trigger day re-fetch."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "token": "t",
            "team_slug": "s",
            "meters": [
                {
                    "uuid": "m1",
                    "metering_point": "AT0030000000000000000000000054321",
                    "energy_direction": "consumption",
                    "label": "X",
                }
            ],
            "watermarks": {"m1": "2026-03-15T14:30:00+00:00"},
        },
    )
    entry.add_to_hass(hass)
    coord = EnergiedatenCoordinator(hass, entry, mock_client)

    # Delta sync returns a record at 14:00 — within an already-imported hour
    delta_readings = [
        {
            "timestamp": "2026-03-15T14:00:00+00:00",
            "timestamp_end": "2026-03-15T14:15:00+00:00",
            "value": 0.35,
            "obis_code": "1-1:1.9.0 G.01",
        },
    ]
    # Full day re-fetch returns complete data for that day
    day_readings = [
        {
            "timestamp": "2026-03-15T14:00:00+00:00",
            "timestamp_end": "2026-03-15T14:15:00+00:00",
            "value": 0.35,
            "obis_code": "1-1:1.9.0 G.01",
        },
        {
            "timestamp": "2026-03-15T14:15:00+00:00",
            "timestamp_end": "2026-03-15T14:30:00+00:00",
            "value": 0.4,
            "obis_code": "1-1:1.9.0 G.01",
        },
    ]

    mock_client.async_get_meter_data.side_effect = [
        MeterDataResult(readings=delta_readings, max_updated_at="2026-03-15T16:00:00+00:00"),
        MeterDataResult(readings=day_readings, max_updated_at=None),
    ]

    # Mock recorder: latest stat is at hour 14 → correction detected
    mock_last_stats = {
        "energiedaten:AT0030000000000000000000000054321_measured": [
            {"start": 1742050800.0, "sum": 50.0}  # 2026-03-15T15:00:00
        ]
    }

    with (
        patch(
            "custom_components.energiedaten.coordinator.async_add_external_statistics"
        ) as mock_add_stats,
        patch(
            "custom_components.energiedaten.coordinator.get_last_statistics",
            return_value=mock_last_stats,
        ),
        patch(
            "custom_components.energiedaten.coordinator.get_instance"
        ) as mock_get_instance,
        patch(
            "custom_components.energiedaten.coordinator.statistics_during_period",
        ) as mock_stats_period,
    ):
        mock_get_instance.return_value.async_add_executor_job = AsyncMock(
            side_effect=[mock_last_stats, {}]  # get_last_statistics, then stats_during_period
        )
        await coord._async_update_data()

    # Should have called API twice: once for delta, once for day re-fetch
    assert mock_client.async_get_meter_data.call_count == 2
    # Second call should be for the affected day (no updated_since)
    second_call = mock_client.async_get_meter_data.call_args_list[1]
    assert second_call.kwargs.get("updated_since") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniconda/base/envs/ha-energiedaten/bin/python -m pytest tests/test_coordinator.py::test_correction_triggers_day_refetch -v`

Expected: FAIL — correction detection not yet implemented.

- [ ] **Step 3: Implement correction detection in coordinator**

Add correction detection logic to `_async_update_data`. After fetching delta data, check if any readings fall within already-imported hours. If so, re-fetch the affected day(s).

Add to the coordinator class (note: `_get_last_sum_row` already exists from Task 5):

```python
async def _detect_corrections(
    self,
    readings: list[dict[str, Any]],
    metering_point: str,
) -> dict[str, list[dict[str, Any]]]:
    """Split readings into new data and corrections.

    Returns dict with keys 'new' and 'corrections'.
    Corrections are readings whose hour already has statistics in the recorder.
    """
    obis_groups: dict[str | None, list[dict[str, Any]]] = {}
    for reading in readings:
        key = reading.get("obis_code")
        obis_groups.setdefault(key, []).append(reading)

    new_readings: list[dict[str, Any]] = []
    correction_readings: list[dict[str, Any]] = []

    for obis_code, obis_readings in obis_groups.items():
        stat_id = _statistic_id(metering_point, obis_code)
        last_stats = await self._get_last_sum_row(stat_id)

        if not last_stats:
            # No existing stats — everything is new
            new_readings.extend(obis_readings)
            continue

        # last_stats["start"] is a UNIX timestamp
        latest_hour_ts = last_stats["start"]

        for reading in obis_readings:
            reading_hour = _hour_key(reading)
            reading_ts = reading_hour.timestamp()
            if reading_ts <= latest_hour_ts:
                correction_readings.append(reading)
            else:
                new_readings.append(reading)

    return {"new": new_readings, "corrections": correction_readings}
```

Update `_async_update_data` to use correction detection. After the delta fetch, when a watermark existed:

```python
# Inside the meter loop, after fetching meter_result:
if has_watermark and meter_result.readings:
    split = await self._detect_corrections(
        meter_result.readings, metering_point
    )
    if split["corrections"]:
        # Re-fetch affected days
        affected_days = self._affected_day_range(split["corrections"])
        day_result = await self.client.async_get_meter_data(
            uuid, affected_days[0], affected_days[1]
        )
        await self._write_correction_statistics(
            day_result.readings, metering_point
        )

    # Process new data via normal path
    if split["new"]:
        await self._write_new_statistics(
            split["new"], metering_point, anchor_from_recorder=True
        )
else:
    # First sync — everything is new, anchor=0
    await self._write_new_statistics(
        meter_result.readings, metering_point, anchor_from_recorder=False
    )
```

Add helper methods:

```python
def _affected_day_range(
    self, corrections: list[dict[str, Any]]
) -> tuple[datetime, datetime]:
    """Compute the day range that needs re-fetching for corrections."""
    timestamps = [
        datetime.fromisoformat(r["timestamp"]) for r in corrections
    ]
    earliest = min(timestamps)
    latest = max(timestamps)
    start_of_day = earliest.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = (latest.replace(hour=0, minute=0, second=0, microsecond=0)
                  + timedelta(days=1))
    return start_of_day, end_of_day


async def _write_correction_statistics(
    self,
    day_readings: list[dict[str, Any]],
    metering_point: str,
) -> None:
    """Recompute and upsert statistics for corrected day data."""
    obis_groups: dict[str | None, list[dict[str, Any]]] = {}
    for reading in day_readings:
        key = reading.get("obis_code")
        obis_groups.setdefault(key, []).append(reading)

    for obis_code, readings in obis_groups.items():
        stat_id = _statistic_id(metering_point, obis_code)

        # Anchor: get sum from the hour before the earliest reading
        earliest_hour = _hour_key(readings[0])
        anchor_sum = await self._get_sum_before(stat_id, earliest_hour)

        hourly = _build_hourly_statistics(readings, anchor_sum)
        if not hourly:
            continue

        metadata = self._build_metadata(stat_id)
        stat_data = [
            StatisticData(start=h["start"], state=h["state"], sum=h["sum"])
            for h in hourly
        ]
        async_add_external_statistics(self.hass, metadata, stat_data)


async def _get_sum_before(self, statistic_id: str, before: datetime) -> float:
    """Get the cumulative sum at the hour before `before`."""
    # Query from far past to just before the target hour
    result = await get_instance(self.hass).async_add_executor_job(
        statistics_during_period,
        self.hass,
        _HISTORY_START,
        before,
        {statistic_id},
        "hour",
        None,
        {"sum"},
    )
    rows = result.get(statistic_id, [])
    if rows:
        return rows[-1].get("sum", 0.0) or 0.0
    return 0.0
```

Also add `statistics_during_period` to the imports:

```python
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
```

Refactor `_write_new_statistics` and `_build_metadata` as shared helpers:

```python
async def _write_new_statistics(
    self,
    readings: list[dict[str, Any]],
    metering_point: str,
    *,
    anchor_from_recorder: bool,
) -> None:
    """Compute and write statistics for new (non-correction) readings."""
    obis_groups: dict[str | None, list[dict[str, Any]]] = {}
    for reading in readings:
        key = reading.get("obis_code")
        obis_groups.setdefault(key, []).append(reading)

    for obis_code, group_readings in obis_groups.items():
        stat_id = _statistic_id(metering_point, obis_code)

        anchor_sum = 0.0
        if anchor_from_recorder:
            row = await self._get_last_sum_row(stat_id)
            anchor_sum = (row.get("sum", 0.0) or 0.0) if row else 0.0

        hourly = _build_hourly_statistics(group_readings, anchor_sum)
        if not hourly:
            continue

        metadata = self._build_metadata(stat_id)
        stat_data = [
            StatisticData(start=h["start"], state=h["state"], sum=h["sum"])
            for h in hourly
        ]
        async_add_external_statistics(self.hass, metadata, stat_data)


def _build_metadata(self, statistic_id: str) -> StatisticMetaData:
    """Build StatisticMetaData for a given statistic ID."""
    return StatisticMetaData(
        has_sum=True,
        mean_type=StatisticMeanType.NONE,
        name=None,
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_class="energy",
        unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    )
```

Update `_async_update_data` to use the new helper methods instead of inline statistics writing.

- [ ] **Step 4: Run correction test**

Run: `/opt/homebrew/Caskroom/miniconda/base/envs/ha-energiedaten/bin/python -m pytest tests/test_coordinator.py::test_correction_triggers_day_refetch -v`

Expected: PASS.

- [ ] **Step 5: Run all tests**

Run: `/opt/homebrew/Caskroom/miniconda/base/envs/ha-energiedaten/bin/python -m pytest tests/ -v`

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add custom_components/energiedaten/coordinator.py tests/test_coordinator.py
git commit -m "feat: add correction detection with day-level re-fetch

Delta sync results are checked against the recorder. Records within
already-imported hours trigger a full day re-fetch from the API.
Corrected days are recomputed with anchored sums and upserted."
```

---

## Task 7: Update conftest and run full integration

Update the test conftest to properly support the new recorder mocking needs.

**Files:**
- Modify: `tests/conftest.py`

- [ ] **Step 1: Update conftest.py recorder mock**

The comment in conftest still references `homeassistant-historical-sensor`. Update:

```python
"""Global test fixtures for energiedaten.at."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energiedaten.const import DOMAIN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations in all tests."""
    yield


@pytest.fixture
def mock_recorder_before_hass():
    """Mock the recorder component so the recorder dependency is satisfied.

    The integration declares recorder as a dependency for writing external
    statistics.  The real recorder requires a live database, which is
    unavailable in the unit-test environment.  We patch async_setup to
    initialise the recorder data structures and return True.
    """

    async def _mock_recorder_setup(hass, config):
        from homeassistant.helpers.recorder import async_initialize_recorder

        async_initialize_recorder(hass)
        return True

    with patch(
        "homeassistant.components.recorder.async_setup",
        side_effect=_mock_recorder_setup,
    ):
        yield


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="mein-haushalt",
        data={
            "token": "test-token",
            "team_slug": "mein-haushalt",
            "meters": [
                {
                    "uuid": "meter-1",
                    "metering_point": "AT0030000000000000000000000054321",
                    "energy_direction": "consumption",
                    "label": "Wohnung",
                },
            ],
        },
    )
```

- [ ] **Step 2: Run full test suite**

Run: `/opt/homebrew/Caskroom/miniconda/base/envs/ha-energiedaten/bin/python -m pytest tests/ -v`

Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "chore: update conftest recorder mock comment

Remove reference to homeassistant-historical-sensor. Recorder mock
now serves async_add_external_statistics and get_last_statistics."
```

---

## Task 8: Final cleanup and version bump

Remove any remaining references to the old library and bump the version.

**Files:**
- Modify: `custom_components/energiedaten/manifest.json`

- [ ] **Step 1: Verify no remaining references to homeassistant-historical-sensor**

Search the codebase:

```bash
grep -r "homeassistant_historical_sensor\|homeassistant-historical-sensor\|HistoricalSensor\|HistoricalState\|group_by_interval\|async_write_historical\|async_update_historical\|get_statistic_metadata\|async_calculate_statistic_data" custom_components/ tests/
```

Expected: No matches.

- [ ] **Step 2: Bump version in manifest.json**

Change version from `"0.2.2"` to `"0.3.0"` in `manifest.json` (this is a breaking change — statistics are now external).

- [ ] **Step 3: Run full test suite one final time**

Run: `/opt/homebrew/Caskroom/miniconda/base/envs/ha-energiedaten/bin/python -m pytest tests/ -v`

Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add custom_components/energiedaten/manifest.json
git commit -m "chore: bump version to 0.3.0

Breaking: statistics are now external (async_add_external_statistics)
instead of written via homeassistant-historical-sensor. Existing
long-term statistics will need to be re-imported on first sync."
```
