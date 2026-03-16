"""Tests for the energiedaten.at data coordinator."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energiedaten.api import (
    AuthenticationError,
    EnergiedatenApiClient,
    RateLimitError,
)
from custom_components.energiedaten.const import DOMAIN
from custom_components.energiedaten.coordinator import EnergiedatenCoordinator


@pytest.fixture
def mock_client() -> AsyncMock:
    """Create a mock API client."""
    client = AsyncMock(spec=EnergiedatenApiClient)
    client.async_get_meter_data = AsyncMock(return_value=[])
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


async def test_initial_fetch_uses_far_past_date(coordinator, mock_client):
    """First fetch (no last_fetched) should request from 2020-01-01."""
    await coordinator._async_update_data()

    mock_client.async_get_meter_data.assert_called_once()
    call_args = mock_client.async_get_meter_data.call_args[0]
    assert call_args[0] == "meter-1"  # meter UUID
    from_dt = call_args[1]
    assert from_dt.year == 2020
    assert from_dt.month == 1
    assert from_dt.day == 1


async def test_incremental_fetch_uses_last_fetched(
    hass: HomeAssistant, mock_client
):
    """Subsequent fetch should use stored last_fetched timestamp."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "token": "t",
            "team_slug": "s",
            "meters": [
                {
                    "uuid": "m1",
                    "metering_point": "AT...",
                    "energy_direction": "consumption",
                    "label": "X",
                }
            ],
            "last_fetched": {"m1": "2026-03-15T23:45:00+00:00"},
        },
    )
    entry.add_to_hass(hass)
    coord = EnergiedatenCoordinator(hass, entry, mock_client)

    await coord._async_update_data()

    call_args = mock_client.async_get_meter_data.call_args[0]
    from_dt = call_args[1]
    assert from_dt == datetime.fromisoformat("2026-03-15T23:45:00+00:00")


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


async def test_update_last_fetched_persists(coordinator):
    """update_last_fetched should save timestamp in config entry data."""
    coordinator.update_last_fetched("meter-1", "2026-03-15T14:15:00Z")

    last_fetched = coordinator.config_entry.data["last_fetched"]
    assert last_fetched["meter-1"] == "2026-03-15T14:15:00Z"


async def test_empty_response_is_not_error(coordinator, mock_client):
    """No new data (empty response) should not raise."""
    mock_client.async_get_meter_data.return_value = []
    result = await coordinator._async_update_data()
    assert result["meter-1"] == []


async def test_returns_readings_per_meter(coordinator, mock_client):
    """Coordinator should return readings keyed by meter UUID."""
    readings = [
        {"timestamp": "2026-03-15T14:00:00Z", "timestamp_end": "2026-03-15T14:15:00Z", "value": 0.3},
    ]
    mock_client.async_get_meter_data.return_value = readings
    result = await coordinator._async_update_data()
    assert result["meter-1"] == readings
