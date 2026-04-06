"""Tests for energiedaten.at integration setup and migration."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energiedaten import async_migrate_entry
from custom_components.energiedaten.const import DOMAIN


async def test_migrate_v1_to_v2_clears_watermarks(hass: HomeAssistant):
    """Migration from v1 to v2 should remove watermarks for full re-fetch."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={
            "token": "t",
            "team_slug": "s",
            "meters": [{"uuid": "m1", "metering_point": "AT...", "energy_direction": "consumption", "label": "X"}],
            "watermarks": {"m1": "2026-03-15T14:30:00+00:00"},
        },
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)

    assert result is True
    assert entry.version == 2
    assert "watermarks" not in entry.data
    # Other data should be preserved
    assert entry.data["token"] == "t"
    assert entry.data["team_slug"] == "s"
    assert len(entry.data["meters"]) == 1


async def test_reimport_service_clears_watermarks(
    hass: HomeAssistant, mock_recorder_before_hass
):
    """The reimport service should clear watermarks and trigger a refresh."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
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
            "watermarks": {"m1": "2026-03-15T14:30:00+00:00"},
        },
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.energiedaten.coordinator.EnergiedatenCoordinator._async_update_data",
            return_value={},
        ),
        patch(
            "custom_components.energiedaten.EnergiedatenApiClient.async_validate",
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Watermarks present before reimport
        assert "watermarks" in entry.data

        await hass.services.async_call(DOMAIN, "reimport", blocking=True)
        await hass.async_block_till_done()

    assert "watermarks" not in entry.data


async def test_migrate_v1_without_watermarks(hass: HomeAssistant):
    """Migration should work even if no watermarks exist yet."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={
            "token": "t",
            "team_slug": "s",
            "meters": [{"uuid": "m1", "metering_point": "AT...", "energy_direction": "consumption", "label": "X"}],
        },
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)

    assert result is True
    assert entry.version == 2
    assert "watermarks" not in entry.data
