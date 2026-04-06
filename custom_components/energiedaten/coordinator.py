"""DataUpdateCoordinator for energiedaten.at."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from itertools import groupby
import logging
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
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

# Human-readable labels for OBIS code suffixes (for statistic IDs)
_OBIS_STAT_NAMES: dict[str, str] = {
    "G.01": "measured",
    "G.01T": "measured_ec",
    "G.02": "ec_generation_share",
    "G.03": "self_coverage_ec",
    "G.03R": "self_coverage_renewable",
    "P.01": "grid",
    "P.01T": "grid_ec",
}


def _obis_suffix(obis_code: str) -> str:
    """Extract the suffix from an OBIS code like '1-1:2.9.0 G.01' → 'G.01'."""
    return obis_code.rsplit(" ", 1)[-1] if " " in obis_code else obis_code


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


def _statistic_id(metering_point: str, obis_code: str | None) -> str:
    """Build external statistic ID from metering point and OBIS code."""
    if obis_code:
        suffix = _obis_suffix(obis_code)
        name = _OBIS_STAT_NAMES.get(suffix, suffix.lower().replace(".", "_"))
        return f"{DOMAIN}:{metering_point}_{name}"
    return f"{DOMAIN}:{metering_point}"


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
        """Fetch new readings for each meter and write statistics."""
        meters = self.config_entry.data.get(CONF_METERS, [])
        watermarks: dict[str, str] = dict(
            self.config_entry.data.get(CONF_WATERMARKS, {})
        )
        now = datetime.now(timezone.utc)
        result: dict[str, list[dict[str, Any]]] = {}
        self._pending_watermarks = {}

        for meter in meters:
            uuid = meter["uuid"]
            metering_point = meter["metering_point"]
            has_watermark = uuid in watermarks

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

            if not meter_result.readings:
                continue

            # Group readings by OBIS code
            obis_groups: dict[str | None, list[dict[str, Any]]] = {}
            for reading in meter_result.readings:
                key = reading.get("obis_code")
                obis_groups.setdefault(key, []).append(reading)

            # Write statistics for each OBIS group
            for obis_code, readings in obis_groups.items():
                stat_id = _statistic_id(metering_point, obis_code)

                # Determine anchor sum
                anchor_sum = 0.0
                if has_watermark:
                    row = await self._get_last_sum_row(stat_id)
                    anchor_sum = (row.get("sum", 0.0) or 0.0) if row else 0.0

                hourly = _build_hourly_statistics(readings, anchor_sum)
                if not hourly:
                    continue

                metadata = self._build_metadata(stat_id)
                stat_data = [
                    StatisticData(
                        start=h["start"],
                        state=h["state"],
                        sum=h["sum"],
                    )
                    for h in hourly
                ]
                async_add_external_statistics(self.hass, metadata, stat_data)

            # Only advance watermark after successful statistics write
            if meter_result.max_updated_at:
                self._pending_watermarks[uuid] = meter_result.max_updated_at
                self._persist_watermark(uuid)

        return result

    async def _get_last_sum_row(self, statistic_id: str) -> dict | None:
        """Query recorder for the latest statistic row (start + sum)."""
        result = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, statistic_id, False, {"sum"}
        )
        rows = result.get(statistic_id, [])
        return rows[0] if rows else None

    def _build_metadata(self, statistic_id: str) -> StatisticMetaData:
        """Build StatisticMetaData for a given statistic ID."""
        return StatisticMetaData(
            has_sum=True,
            mean_type=StatisticMeanType.NONE,
            name=None,
            source=DOMAIN,
            statistic_id=statistic_id,
            unit_class="energy",
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        )

    def _persist_watermark(self, meter_uuid: str) -> None:
        """Persist the delta-sync watermark for a meter."""
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
