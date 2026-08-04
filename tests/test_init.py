"""Tests for energiedaten.at integration setup and migration."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energiedaten import async_migrate_entry
from custom_components.energiedaten.const import DOMAIN


@pytest.mark.parametrize("start_version", [1, 2, 3])
async def test_migration_ends_at_v4_without_legacy_keys(
    hass: HomeAssistant, start_version: int
):
    """Every supported schema migrates to v4, dropping team_slug and watermarks.

    `watermarks` held `updated_since` timestamps, which are not valid cursors —
    carrying them over would send the very parameter the API now rejects. The
    entry must fall back to a fresh window read instead.
    """
    data = {
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
    }
    if start_version < 3:
        # v3 is the step that strips it, so a genuine v3 entry never has one
        data["team_slug"] = "mein-haushalt"

    entry = MockConfigEntry(domain=DOMAIN, version=start_version, data=data)
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True

    assert entry.version == 4
    assert "watermarks" not in entry.data
    assert "cursors" not in entry.data
    assert "team_slug" not in entry.data
    # Everything else survives untouched
    assert entry.data["token"] == "t"
    assert entry.data["meters"][0]["uuid"] == "m1"


async def test_setup_registers_account_device_with_meters_beneath_it(
    hass: HomeAssistant, mock_recorder_before_hass
):
    """The config entry gets a service device that owns the button and meters.

    The account device has to exist in the registry before the meter devices
    reference it via `via_device`, otherwise the link is silently dropped.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=4,
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
        },
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.energiedaten.coordinator.EnergiedatenCoordinator._async_update_data",
            return_value={},
        ),
        patch("custom_components.energiedaten.EnergiedatenApiClient.async_validate"),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    registry = dr.async_get(hass)

    account = registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert account is not None
    assert account.entry_type is dr.DeviceEntryType.SERVICE

    meter = registry.async_get_device(identifiers={(DOMAIN, "m1")})
    assert meter is not None
    assert meter.via_device_id == account.id

    # The payoff: the button is no longer squatting on the global `button.refresh`
    assert hass.states.get("button.energiedaten_at_refresh") is not None


async def test_reimport_service_clears_cursors(
    hass: HomeAssistant, mock_recorder_before_hass
):
    """The reimport service should clear cursors and trigger a refresh."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=4,
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

        assert "cursors" in entry.data

        await hass.services.async_call(DOMAIN, "reimport", blocking=True)
        await hass.async_block_till_done()

    assert "cursors" not in entry.data
