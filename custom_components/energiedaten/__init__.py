"""The energiedaten.at integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EnergiedatenApiClient
from .const import CONF_TEAM_SLUG, CONF_TOKEN
from .coordinator import EnergiedatenCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BUTTON]


@dataclass
class EnergiedatenData:
    """Runtime data stored in the config entry."""

    coordinator: EnergiedatenCoordinator
    client: EnergiedatenApiClient


type EnergiedatenConfigEntry = ConfigEntry[EnergiedatenData]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnergiedatenConfigEntry,
) -> bool:
    """Set up energiedaten.at from a config entry."""
    session = async_get_clientsession(hass)
    client = EnergiedatenApiClient(
        session=session,
        token=entry.data[CONF_TOKEN],
        team_slug=entry.data[CONF_TEAM_SLUG],
    )

    coordinator = EnergiedatenCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = EnergiedatenData(
        coordinator=coordinator,
        client=client,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: EnergiedatenConfigEntry,
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
