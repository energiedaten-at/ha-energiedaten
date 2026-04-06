"""Tests for energiedaten.at integration setup and migration."""

from __future__ import annotations

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
