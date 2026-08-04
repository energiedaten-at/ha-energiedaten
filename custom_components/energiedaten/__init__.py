"""The energiedaten.at integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EnergiedatenApiClient
from .const import CONF_CURSORS, CONF_TOKEN, DOMAIN
from .coordinator import EnergiedatenCoordinator

# Legacy keys from older config entries — kept here as literals so const.py
# doesn't have to retain the constants. Used only by async_migrate_entry.
_LEGACY_TEAM_SLUG_KEY = "team_slug"
_LEGACY_WATERMARKS_KEY = "watermarks"

PLATFORMS = [Platform.SENSOR, Platform.BUTTON]


@dataclass
class EnergiedatenData:
    """Runtime data stored in the config entry."""

    coordinator: EnergiedatenCoordinator
    client: EnergiedatenApiClient


type EnergiedatenConfigEntry = ConfigEntry[EnergiedatenData]


async def async_migrate_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Migrate config entry to the latest version."""
    if entry.version < 2:
        # v2: statistics moved from homeassistant-historical-sensor to
        # async_add_external_statistics with new statistic IDs.
        # Clear watermarks so the first sync re-fetches all history.
        new_data = {
            k: v for k, v in entry.data.items() if k != _LEGACY_WATERMARKS_KEY
        }
        hass.config_entries.async_update_entry(entry, data=new_data, version=2)
    if entry.version < 3:
        # v3: team is derived from the API key; the `team_slug` field is
        # no longer collected or used. Strip it from existing entries.
        new_data = {k: v for k, v in entry.data.items() if k != _LEGACY_TEAM_SLUG_KEY}
        hass.config_entries.async_update_entry(entry, data=new_data, version=3)
    if entry.version < 4:
        # v4: the API dropped `updated_since` for an opaque `cursor`. Stored
        # watermarks are timestamps, not cursors, so they can't be carried
        # over — drop them and let the next sync do a fresh window read.
        new_data = {
            k: v for k, v in entry.data.items() if k != _LEGACY_WATERMARKS_KEY
        }
        hass.config_entries.async_update_entry(entry, data=new_data, version=4)
    return True


async def _async_handle_reimport(hass: HomeAssistant, _call: ServiceCall) -> None:
    """Handle the reimport service call."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        new_data = {k: v for k, v in entry.data.items() if k != CONF_CURSORS}
        hass.config_entries.async_update_entry(entry, data=new_data)
        if entry.runtime_data:
            await entry.runtime_data.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnergiedatenConfigEntry,
) -> bool:
    """Set up energiedaten.at from a config entry."""
    session = async_get_clientsession(hass)
    client = EnergiedatenApiClient(
        session=session,
        token=entry.data[CONF_TOKEN],
    )

    coordinator = EnergiedatenCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = EnergiedatenData(
        coordinator=coordinator,
        client=client,
    )

    if not hass.services.has_service(DOMAIN, "reimport"):

        async def handle_reimport(call: ServiceCall) -> None:
            await _async_handle_reimport(hass, call)

        hass.services.async_register(DOMAIN, "reimport", handle_reimport)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: EnergiedatenConfigEntry,
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
