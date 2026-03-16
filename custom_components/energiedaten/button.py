"""Refresh button for energiedaten.at."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import EnergiedatenConfigEntry
from .const import DOMAIN
from .coordinator import EnergiedatenCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnergiedatenConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the refresh button."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities([EnergiedatenRefreshButton(coordinator, entry)])


class EnergiedatenRefreshButton(
    CoordinatorEntity[EnergiedatenCoordinator], ButtonEntity
):
    """Button to manually trigger a data refresh for all meters."""

    _attr_icon = "mdi:refresh"
    _attr_has_entity_name = True
    _attr_name = "Refresh"

    def __init__(
        self,
        coordinator: EnergiedatenCoordinator,
        entry: EnergiedatenConfigEntry,
    ) -> None:
        """Initialize the refresh button."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_refresh"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "energiedaten.at",
            "manufacturer": "energiedaten.at",
        }

    async def async_press(self) -> None:
        """Handle button press — trigger immediate data refresh."""
        await self.coordinator.async_request_refresh()
