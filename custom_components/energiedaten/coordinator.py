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
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import (
    AuthenticationError,
    EnergiedatenApiClient,
    InvalidRequestError,
    MeterDataResult,
    RateLimitError,
)
from .const import CONF_CURSORS, CONF_METERS, DOMAIN

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
    mp = metering_point.lower()
    if obis_code:
        suffix = _obis_suffix(obis_code)
        name = _OBIS_STAT_NAMES.get(suffix, suffix.lower().replace(".", "_"))
        return f"{DOMAIN}:{mp}_{name}"
    return f"{DOMAIN}:{mp}"


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
        self._pending_cursors: dict[str, str] = {}

    async def _async_update_data(self) -> dict[str, list[dict[str, Any]]]:
        """Fetch new readings for each meter and write statistics."""
        meters = self.config_entry.data.get(CONF_METERS, [])
        cursors: dict[str, str] = dict(self.config_entry.data.get(CONF_CURSORS, {}))
        now = datetime.now(timezone.utc)
        result: dict[str, list[dict[str, Any]]] = {}
        self._pending_cursors = {}

        for meter in meters:
            uuid = meter["uuid"]
            metering_point = meter["metering_point"]
            cursor = cursors.get(uuid)

            try:
                meter_result, cursor = await self._fetch_meter_data(uuid, cursor, now)
            except AuthenticationError as err:
                raise ConfigEntryAuthFailed from err
            except RateLimitError as err:
                raise UpdateFailed("Rate limited, will retry next cycle") from err
            except InvalidRequestError as err:
                raise UpdateFailed(f"Server rejected the data request: {err}") from err

            result[uuid] = meter_result.readings

            if not meter_result.readings:
                continue

            if cursor is not None:
                split = await self._detect_corrections(
                    meter_result.readings, metering_point
                )
                if split["corrections"]:
                    # Re-fetch affected days
                    affected_days = self._affected_day_range(split["corrections"])
                    day_result = await self.client.async_get_meter_data(
                        uuid, affected_days[0], affected_days[1]
                    )
                    await self._write_correction_statistics(
                        day_result.readings, metering_point
                    )

                # Process new data via normal path
                if split["new"]:
                    await self._write_new_statistics(
                        split["new"], metering_point, anchor_from_recorder=True
                    )
            else:
                # First sync — everything is new, anchor=0
                await self._write_new_statistics(
                    meter_result.readings, metering_point, anchor_from_recorder=False
                )

            # Only advance the cursor after a successful statistics write.
            # A null next_cursor means the page was empty — keep the old one.
            if meter_result.next_cursor:
                self._pending_cursors[uuid] = meter_result.next_cursor
                self._persist_cursor(uuid)

        return result

    async def _fetch_meter_data(
        self, meter_uuid: str, cursor: str | None, now: datetime
    ) -> tuple[MeterDataResult, str | None]:
        """Fetch one meter's data, returning the result and the cursor used.

        The returned cursor is None when this was a window read — either the
        first sync, or a fallback after the server rejected a stored cursor.
        Callers use it to decide between the correction-detection path and the
        anchor-at-zero first-sync path.
        """
        if cursor is None:
            # Window read over all history. Its next_cursor bridges us into
            # the change feed from here on.
            return await self.client.async_get_meter_data(
                meter_uuid, _HISTORY_START, now
            ), None

        # Sync read: cursor alone. Adding from/to would filter the change feed
        # by timestamp and drop exactly the late revisions it exists to deliver.
        try:
            return await self.client.async_get_meter_data(
                meter_uuid, cursor=cursor
            ), cursor
        except InvalidRequestError:
            # The server won't take this cursor. Retrying it every cycle would
            # wedge the entry, so drop it and rebuild from a window read.
            _LOGGER.warning(
                "Sync cursor for meter %s was rejected; "
                "falling back to a full history read",
                meter_uuid,
            )
            self._forget_cursor(meter_uuid)
            return await self.client.async_get_meter_data(
                meter_uuid, _HISTORY_START, now
            ), None

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

    def _forget_cursor(self, meter_uuid: str) -> None:
        """Drop a rejected cursor so the next poll starts from a window read."""
        cursors = dict(self.config_entry.data.get(CONF_CURSORS, {}))
        if cursors.pop(meter_uuid, None) is None:
            return
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={**self.config_entry.data, CONF_CURSORS: cursors},
        )

    def _persist_cursor(self, meter_uuid: str) -> None:
        """Persist the sync resume cursor for a meter."""
        cursor = self._pending_cursors.get(meter_uuid)
        if not cursor:
            return
        cursors = dict(self.config_entry.data.get(CONF_CURSORS, {}))
        cursors[meter_uuid] = cursor
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={**self.config_entry.data, CONF_CURSORS: cursors},
        )

    async def _detect_corrections(
        self,
        readings: list[dict[str, Any]],
        metering_point: str,
    ) -> dict[str, list[dict[str, Any]]]:
        """Split readings into new data and corrections.

        Returns dict with keys 'new' and 'corrections'.
        Corrections are readings whose hour already has statistics in the recorder.
        """
        obis_groups: dict[str | None, list[dict[str, Any]]] = {}
        for reading in readings:
            key = reading.get("obis_code")
            obis_groups.setdefault(key, []).append(reading)

        new_readings: list[dict[str, Any]] = []
        correction_readings: list[dict[str, Any]] = []

        for obis_code, obis_readings in obis_groups.items():
            stat_id = _statistic_id(metering_point, obis_code)
            last_row = await self._get_last_sum_row(stat_id)

            if not last_row:
                # No existing stats — everything is new
                new_readings.extend(obis_readings)
                continue

            # last_row["start"] is a UNIX timestamp
            latest_hour_ts = last_row["start"]

            for reading in obis_readings:
                reading_hour = _hour_key(reading)
                reading_ts = reading_hour.timestamp()
                if reading_ts <= latest_hour_ts:
                    correction_readings.append(reading)
                else:
                    new_readings.append(reading)

        return {"new": new_readings, "corrections": correction_readings}

    def _affected_day_range(
        self, corrections: list[dict[str, Any]]
    ) -> tuple[datetime, datetime]:
        """Compute the day range that needs re-fetching for corrections."""
        timestamps = [
            datetime.fromisoformat(r["timestamp"]) for r in corrections
        ]
        earliest = min(timestamps)
        latest = max(timestamps)
        start_of_day = earliest.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = (latest.replace(hour=0, minute=0, second=0, microsecond=0)
                      + timedelta(days=1))
        return start_of_day, end_of_day

    async def _write_correction_statistics(
        self,
        day_readings: list[dict[str, Any]],
        metering_point: str,
    ) -> None:
        """Recompute and upsert statistics for corrected day data."""
        obis_groups: dict[str | None, list[dict[str, Any]]] = {}
        for reading in day_readings:
            key = reading.get("obis_code")
            obis_groups.setdefault(key, []).append(reading)

        for obis_code, readings in obis_groups.items():
            stat_id = _statistic_id(metering_point, obis_code)

            # Anchor: get sum from the hour before the earliest reading
            earliest_hour = _hour_key(readings[0])
            anchor_sum = await self._get_sum_before(stat_id, earliest_hour)

            hourly = _build_hourly_statistics(readings, anchor_sum)
            if not hourly:
                continue

            metadata = self._build_metadata(stat_id)
            stat_data = [
                StatisticData(start=h["start"], state=h["state"], sum=h["sum"])
                for h in hourly
            ]
            async_add_external_statistics(self.hass, metadata, stat_data)

    async def _write_new_statistics(
        self,
        readings: list[dict[str, Any]],
        metering_point: str,
        *,
        anchor_from_recorder: bool,
    ) -> None:
        """Compute and write statistics for new (non-correction) readings."""
        obis_groups: dict[str | None, list[dict[str, Any]]] = {}
        for reading in readings:
            key = reading.get("obis_code")
            obis_groups.setdefault(key, []).append(reading)

        for obis_code, group_readings in obis_groups.items():
            stat_id = _statistic_id(metering_point, obis_code)

            anchor_sum = 0.0
            if anchor_from_recorder:
                row = await self._get_last_sum_row(stat_id)
                anchor_sum = (row.get("sum", 0.0) or 0.0) if row else 0.0

            hourly = _build_hourly_statistics(group_readings, anchor_sum)
            if not hourly:
                continue

            metadata = self._build_metadata(stat_id)
            stat_data = [
                StatisticData(start=h["start"], state=h["state"], sum=h["sum"])
                for h in hourly
            ]
            async_add_external_statistics(self.hass, metadata, stat_data)

    async def _get_sum_before(self, statistic_id: str, before: datetime) -> float:
        """Get the cumulative sum at the hour before `before`."""
        result = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            _HISTORY_START,
            before,
            {statistic_id},
            "hour",
            None,
            {"sum"},
        )
        rows = result.get(statistic_id, [])
        if rows:
            return rows[-1].get("sum", 0.0) or 0.0
        return 0.0
