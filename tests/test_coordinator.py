"""Tests for the energiedaten.at data coordinator."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energiedaten.api import (
    AuthenticationError,
    EnergiedatenApiClient,
    MeterDataResult,
    RateLimitError,
)
from custom_components.energiedaten.const import DOMAIN
from custom_components.energiedaten.coordinator import EnergiedatenCoordinator


@pytest.fixture
def mock_client() -> AsyncMock:
    """Create a mock API client."""
    client = AsyncMock(spec=EnergiedatenApiClient)
    client.async_get_meter_data = AsyncMock(
        return_value=MeterDataResult(readings=[], max_updated_at=None)
    )
    return client


@pytest.fixture
def coordinator(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> EnergiedatenCoordinator:
    """Create a coordinator with mock dependencies."""
    mock_config_entry.add_to_hass(hass)
    return EnergiedatenCoordinator(hass, mock_config_entry, mock_client)


async def test_initial_fetch_has_no_updated_since(coordinator, mock_client):
    """First fetch (no watermark) should not pass updated_since."""
    with patch(
        "custom_components.energiedaten.coordinator.async_add_external_statistics"
    ):
        await coordinator._async_update_data()

    mock_client.async_get_meter_data.assert_called_once()
    call_kwargs = mock_client.async_get_meter_data.call_args
    assert call_kwargs.kwargs.get("updated_since") is None


async def test_incremental_fetch_uses_watermark(hass: HomeAssistant, mock_client):
    """Subsequent fetch should pass stored watermark as updated_since."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "token": "t",
            "meters": [
                {
                    "uuid": "m1",
                    "metering_point": "AT...",
                    "energy_direction": "consumption",
                    "label": "X",
                }
            ],
            "watermarks": {"m1": "2026-03-15T14:30:00+00:00"},
        },
    )
    entry.add_to_hass(hass)
    coord = EnergiedatenCoordinator(hass, entry, mock_client)

    with patch(
        "custom_components.energiedaten.coordinator.async_add_external_statistics"
    ):
        await coord._async_update_data()

    call_kwargs = mock_client.async_get_meter_data.call_args
    assert call_kwargs.kwargs.get("updated_since") == "2026-03-15T14:30:00+00:00"


async def test_auth_error_raises_config_entry_auth_failed(coordinator, mock_client):
    """401 from API should trigger HA reauth via ConfigEntryAuthFailed."""
    mock_client.async_get_meter_data.side_effect = AuthenticationError
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_rate_limit_raises_update_failed(coordinator, mock_client):
    """429 from API should raise UpdateFailed for retry."""
    mock_client.async_get_meter_data.side_effect = RateLimitError
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_empty_response_is_not_error(coordinator, mock_client):
    """No new data (empty response) should not raise."""
    mock_client.async_get_meter_data.return_value = MeterDataResult(
        readings=[], max_updated_at=None
    )
    result = await coordinator._async_update_data()
    assert result["meter-1"] == []


async def test_returns_readings_per_meter(coordinator, mock_client):
    """Coordinator should return readings keyed by meter UUID."""
    readings = [
        {"timestamp": "2026-03-15T14:00:00Z", "timestamp_end": "2026-03-15T14:15:00Z", "value": 0.3},
    ]
    mock_client.async_get_meter_data.return_value = MeterDataResult(
        readings=readings,
        max_updated_at="2026-03-15T15:00:00+00:00",
    )

    with patch(
        "custom_components.energiedaten.coordinator.async_add_external_statistics"
    ):
        result = await coordinator._async_update_data()
    assert result["meter-1"] == readings


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
    assert "at0030000000000000000000000054321" in meta["statistic_id"]


async def test_normal_sync_anchors_sum_from_recorder(hass, mock_client):
    """Normal sync should query recorder for anchor and accumulate forward."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "token": "t",
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
        "energiedaten:at0030000000000000000000000054321_measured": [
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


async def test_correction_triggers_day_refetch(hass, mock_client):
    """Records within already-imported hours should trigger day re-fetch."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "token": "t",
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
        "energiedaten:at0030000000000000000000000054321_measured": [
            {"start": 1773586800.0, "sum": 50.0}  # 2026-03-15T15:00:00 UTC
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
            side_effect=[mock_last_stats, {}]  # get_last_statistics, then statistics_during_period
        )
        await coord._async_update_data()

    # Should have called API twice: once for delta, once for day re-fetch
    assert mock_client.async_get_meter_data.call_count == 2
    # Second call should be for the affected day (no updated_since)
    second_call = mock_client.async_get_meter_data.call_args_list[1]
    assert second_call.kwargs.get("updated_since") is None
