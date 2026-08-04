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
    InvalidRequestError,
    MeterDataResult,
    RateLimitError,
)
from custom_components.energiedaten.const import DOMAIN
from custom_components.energiedaten.coordinator import (
    _HISTORY_START,
    EnergiedatenCoordinator,
)


@pytest.fixture
def mock_client() -> AsyncMock:
    """Create a mock API client."""
    client = AsyncMock(spec=EnergiedatenApiClient)
    client.async_get_meter_data = AsyncMock(
        return_value=MeterDataResult(readings=[], next_cursor=None)
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


async def test_initial_fetch_is_a_window_read(coordinator, mock_client):
    """First fetch (no cursor) reads the history window."""
    with patch(
        "custom_components.energiedaten.coordinator.async_add_external_statistics"
    ):
        await coordinator._async_update_data()

    mock_client.async_get_meter_data.assert_called_once()
    call = mock_client.async_get_meter_data.call_args
    assert call.kwargs.get("cursor") is None
    assert call.args[1] == _HISTORY_START


async def test_incremental_fetch_sends_cursor_without_window(
    hass: HomeAssistant, mock_client
):
    """A stored cursor makes the next poll a pure sync read.

    Passing from/to alongside the cursor would filter the change feed by
    timestamp and drop the late revisions it exists to deliver.
    """
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
            "cursors": {"m1": "Y3Vyc29yLTE"},
        },
    )
    entry.add_to_hass(hass)
    coord = EnergiedatenCoordinator(hass, entry, mock_client)

    with patch(
        "custom_components.energiedaten.coordinator.async_add_external_statistics"
    ):
        await coord._async_update_data()

    call = mock_client.async_get_meter_data.call_args
    assert call.kwargs.get("cursor") == "Y3Vyc29yLTE"
    assert call.args == ("m1",)
    assert "from_dt" not in call.kwargs and "to_dt" not in call.kwargs


async def test_rejected_cursor_falls_back_to_window_read(
    hass: HomeAssistant, mock_client
):
    """A cursor the server rejects must not wedge the integration.

    Without this the entry retries the same bad cursor every 6 hours forever
    and only a remove/re-add clears it.
    """
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
            "cursors": {"m1": "stale-cursor"},
        },
    )
    entry.add_to_hass(hass)
    coord = EnergiedatenCoordinator(hass, entry, mock_client)

    mock_client.async_get_meter_data.side_effect = [
        InvalidRequestError("rejected"),
        MeterDataResult(readings=[], next_cursor=None),
    ]

    with patch(
        "custom_components.energiedaten.coordinator.async_add_external_statistics"
    ):
        await coord._async_update_data()

    assert mock_client.async_get_meter_data.call_count == 2
    retry = mock_client.async_get_meter_data.call_args_list[1]
    assert retry.kwargs.get("cursor") is None
    assert retry.args[1] == _HISTORY_START
    # The bad cursor is gone, so the next poll starts clean
    assert entry.data.get("cursors", {}).get("m1") is None


async def test_rejected_window_read_is_not_retried(coordinator, mock_client):
    """A rejected window read has no cursor to blame — fail rather than loop."""
    mock_client.async_get_meter_data.side_effect = InvalidRequestError("rejected")

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    assert mock_client.async_get_meter_data.call_count == 1


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


async def test_returns_readings_per_meter(coordinator, mock_client):
    """Coordinator should return readings keyed by meter UUID."""
    readings = [
        {"timestamp": "2026-03-15T14:00:00Z", "timestamp_end": "2026-03-15T14:15:00Z", "value": 0.3},
    ]
    mock_client.async_get_meter_data.return_value = MeterDataResult(
        readings=readings,
        next_cursor="Y3Vyc29yLTE",
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
        next_cursor="Y3Vyc29yLTE",
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
            "cursors": {"m1": "Y3Vyc29yLXNlZWQ"},
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
        next_cursor="Y3Vyc29yLTI",
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


async def test_cursor_persisted_after_statistics_write(coordinator, mock_client):
    """Cursor should be saved after successful statistics write."""
    mock_client.async_get_meter_data.return_value = MeterDataResult(
        readings=[
            {
                "timestamp": "2026-03-15T14:00:00+00:00",
                "timestamp_end": "2026-03-15T14:15:00+00:00",
                "value": 0.3,
                "obis_code": "1-1:1.9.0 G.01",
            },
        ],
        next_cursor="Y3Vyc29yLTE",
    )

    with patch(
        "custom_components.energiedaten.coordinator.async_add_external_statistics"
    ):
        await coordinator._async_update_data()

    cursors = coordinator.config_entry.data.get("cursors", {})
    assert cursors.get("meter-1") == "Y3Vyc29yLTE"


async def test_no_cursor_on_empty_response(coordinator, mock_client):
    """No cursor should be persisted when there are no readings."""
    mock_client.async_get_meter_data.return_value = MeterDataResult(
        readings=[], next_cursor=None
    )
    await coordinator._async_update_data()
    assert "cursors" not in coordinator.config_entry.data


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
            "cursors": {"m1": "Y3Vyc29yLXNlZWQ"},
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
        MeterDataResult(readings=delta_readings, next_cursor="Y3Vyc29yLTI"),
        MeterDataResult(readings=day_readings, next_cursor=None),
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

    # Should have called API twice: once for the sync read, once for day re-fetch
    assert mock_client.async_get_meter_data.call_count == 2
    # The re-fetch is a bounded window read, not a cursor resume
    second_call = mock_client.async_get_meter_data.call_args_list[1]
    assert second_call.kwargs.get("cursor") is None
    assert len(second_call.args) == 3  # uuid, from_dt, to_dt
