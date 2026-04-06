"""DataUpdateCoordinator for energiedaten.at."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from itertools import groupby
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
from .const import CONF_METERS, CONF_WATERMARKS, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Far-past date for history import — API clamps to retention window
_HISTORY_START = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _hour_key(reading: dict[str, Any]) -> datetime:
    """Return the start-of-hour for a reading's timestamp."""
    ts = datetime.fromisoformat(reading["timestamp"])
    return ts.replace(minute=0, second=0, microsecond=0)


def _build_hourly_statistics(
    readings: list[dict[str, Any]],
    anchor_sum: float,
) -> list[dict[str, Any]]:
    """Group quarter-hour readings into hourly statistics with cumulative sum.

    Returns a list of dicts with keys: start (datetime), state (float), sum (float).
    """
    if not readings:
        return []

    result: list[dict[str, Any]] = []
    accumulated = anchor_sum

    for hour_start, group in groupby(readings, key=_hour_key):
        hour_sum = sum(float(r["value"]) for r in group)
        accumulated += hour_sum
        result.append({
            "start": hour_start,
            "state": hour_sum,
            "sum": accumulated,
        })

    return result


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
        self._pending_watermarks: dict[str, str] = {}

    async def _async_update_data(self) -> dict[str, list[dict[str, Any]]]:
        """Fetch new readings for each meter using delta sync."""
        meters = self.config_entry.data.get(CONF_METERS, [])
        watermarks: dict[str, str] = dict(
            self.config_entry.data.get(CONF_WATERMARKS, {})
        )
        now = datetime.now(timezone.utc)
        result: dict[str, list[dict[str, Any]]] = {}
        self._pending_watermarks = {}

        for meter in meters:
            uuid = meter["uuid"]

            try:
                meter_result = await self.client.async_get_meter_data(
                    uuid,
                    _HISTORY_START,
                    now,
                    updated_since=watermarks.get(uuid),
                )
            except AuthenticationError as err:
                raise ConfigEntryAuthFailed from err
            except RateLimitError as err:
                raise UpdateFailed("Rate limited, will retry next cycle") from err

            result[uuid] = meter_result.readings
            if meter_result.max_updated_at:
                self._pending_watermarks[uuid] = meter_result.max_updated_at

        return result

    def update_watermark(self, meter_uuid: str) -> None:
        """Persist the delta-sync watermark for a meter after successful statistics write."""
        watermark = self._pending_watermarks.get(meter_uuid)
        if not watermark:
            return
        watermarks = dict(
            self.config_entry.data.get(CONF_WATERMARKS, {})
        )
        watermarks[meter_uuid] = watermark
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={**self.config_entry.data, CONF_WATERMARKS: watermarks},
        )
