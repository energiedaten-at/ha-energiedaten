"""DataUpdateCoordinator for energiedaten.at."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import AuthenticationError, EnergiedatenApiClient, RateLimitError
from .const import CONF_LAST_FETCHED, CONF_METERS, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Far-past date for initial history import — API clamps to retention window
_HISTORY_START = datetime(2020, 1, 1, tzinfo=timezone.utc)


class EnergiedatenCoordinator(DataUpdateCoordinator[dict[str, list[dict[str, Any]]]]):
    """Fetch meter data from energiedaten.at every 6 hours."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: EnergiedatenApiClient,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=config_entry,
            update_interval=timedelta(hours=6),
        )
        self.client = client

    async def _async_update_data(self) -> dict[str, list[dict[str, Any]]]:
        """Fetch new readings for each meter."""
        meters = self.config_entry.data.get(CONF_METERS, [])
        last_fetched: dict[str, str] = dict(
            self.config_entry.data.get(CONF_LAST_FETCHED, {})
        )
        now = datetime.now(timezone.utc)
        result: dict[str, list[dict[str, Any]]] = {}

        for meter in meters:
            uuid = meter["uuid"]
            from_dt = (
                datetime.fromisoformat(last_fetched[uuid])
                if uuid in last_fetched
                else _HISTORY_START
            )

            try:
                readings = await self.client.async_get_meter_data(uuid, from_dt, now)
            except AuthenticationError as err:
                raise ConfigEntryAuthFailed from err
            except RateLimitError as err:
                raise UpdateFailed("Rate limited, will retry next cycle") from err

            result[uuid] = readings

        return result

    def update_last_fetched(self, meter_uuid: str, timestamp: str) -> None:
        """Persist last_fetched for a meter after successful statistics write."""
        last_fetched = dict(
            self.config_entry.data.get(CONF_LAST_FETCHED, {})
        )
        last_fetched[meter_uuid] = timestamp
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={**self.config_entry.data, CONF_LAST_FETCHED: last_fetched},
        )
