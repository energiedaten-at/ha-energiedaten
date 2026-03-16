"""The energiedaten.at integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

# Type alias used by sensor and other platform modules.
# Task 6 will replace this with a fully-typed ConfigEntry subclass once
# runtime_data is wired up.
type EnergiedatenConfigEntry = ConfigEntry


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up energiedaten.at from a config entry."""
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return True
