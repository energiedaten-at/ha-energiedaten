# Statistics Refactor: Direct External Statistics with Delta Sync

**Date**: 2026-04-06
**Status**: Approved

## Problem

The integration uses `homeassistant-historical-sensor` to write energy statistics.
This library has a cutoff filter that silently discards data for timestamps that
already have statistics, making it impossible to apply corrections (e.g., estimated
→ measured). The cumulative sum calculation also starts from `latest["sum"]` and
only appends forward — it cannot rewrite existing rows.

Core HA energy integrations (opower, octopus_energy) don't use this library. They
call `async_add_external_statistics()` directly, which supports upsert and allows
corrections when paired with proper sum anchoring.

## Solution

Drop `homeassistant-historical-sensor`. Write statistics directly via
`async_add_external_statistics()` in the coordinator, using delta sync
(`updated_since` watermark) for efficient fetching and day-level re-fetch for
corrections.

## Architecture

### Before

```
Coordinator (fetch) → Sensor (filter by OBIS, write statistics via homeassistant-historical-sensor)
```

### After

```
Coordinator (fetch + write statistics via async_add_external_statistics)
       ↓
Sensor (read coordinator data, expose state + attributes)
```

The coordinator owns both fetching and statistics writing. Sensors become pure
`CoordinatorEntity` + `SensorEntity` — they expose the latest reading value and
attributes but don't touch the recorder.

## Statistic IDs

External statistics use the format:

```
energiedaten:{metering_point}_{normalised_obis_name}
```

For example: `energiedaten:AT0030000000000000000000000054321_measured`

- Use the normalised OBIS name from the API if the field exists in the response.
- Fall back to the slugified OBIS suffix (e.g., `g_01`) if the API doesn't provide it yet.
- For meters without OBIS codes: `energiedaten:{metering_point}`.

The metering point is used instead of the internal UUID because it's the stable
real-world identifier — statistics survive if the user deletes and re-creates the
config entry.

## Fetch and Sync Logic

### First sync (no watermark)

1. Call API without `updated_since` — fetches all available data.
2. Discover OBIS codes from the records.
3. Group readings by (meter, obis_code).
4. Compute hourly statistics (sum of quarter-hour readings per hour) with cumulative
   sum starting at 0.
5. Write via `async_add_external_statistics()`.
6. Persist watermark from `meta.max_updated_at`.

### Normal sync — new data only (common case)

1. Call API with `updated_since=stored_watermark` for each meter.
2. All returned records have timestamps after the latest statistic → new data.
3. Group readings by (meter, obis_code).
4. For each group: query `statistics_during_period()` for the latest cumulative sum
   (the anchor). Accumulate hourly statistics forward from that anchor.
5. Write via `async_add_external_statistics()`.
6. Persist the new watermark from `meta.max_updated_at`.

### Correction sync (uncommon)

1. Same initial fetch as normal sync.
2. Some records have timestamps within already-imported hours → corrections detected.
3. Separate the response into new data (after latest statistic) and corrections
   (within already-imported hours).
4. Process new data via the normal sync path (anchor + append).
5. For corrections: identify the affected calendar day(s). Grid operators re-send
   complete days, so day boundaries are the natural unit.
6. Re-fetch from API: `from=start_of_earliest_affected_day`,
   `to=end_of_latest_affected_day`, no `updated_since` — gets complete data for
   those days.
7. Query `statistics_during_period()` for the cumulative sum at the hour before the
   earliest affected hour (the anchor).
8. Recompute all hourly statistics for the affected days from that anchor.
9. Write via `async_add_external_statistics()` — upserts overwrite existing rows with
   correct `state` and `sum` values.
10. Persist the new watermark.

### Detecting corrections

Query the recorder via `statistics_during_period()` for the latest statistic per
(metering_point, obis_code). Compare each returned record's timestamp against this.
If a record falls within an already-imported hour, it's a correction.

No extra persisted state is needed — the recorder is queried each time. This is a
cheap single-row lookup and avoids maintaining a separate `latest_statistics` dict
that could drift.

## Sensor Entities

Sensors become simple state-exposing entities:

- One sensor per (meter, obis_code) pair, discovered from coordinator data.
- `SensorDeviceClass.ENERGY`, unit `kWh`.
- `native_value` returns the latest reading's value.
- No `state_class` — statistics are external, not derived from state changes.
- Device info grouped by meter (metering point).
- Extra state attributes: `metering_point`, `energy_direction`, `granularity`,
  `data_quality`, `last_data_at`, `obis_code`.

### Removed from sensor

- `HistoricalSensor` base class and all statistics-related methods:
  `get_statistic_metadata`, `async_calculate_statistic_data`,
  `async_write_historical`, `_async_process_readings`, `async_update_historical`.
- Custom `_handle_coordinator_update` override — standard coordinator behavior
  (calling `async_write_ha_state()`) is now correct.

### Naming

Use the normalised OBIS name from the API if available. Fall back to the existing
`OBIS_LABELS` mapping until the API provides it.

## Dependency Changes

### Removed

- `homeassistant-historical-sensor` from `manifest.json` requirements.
- All `HistoricalSensor` imports and usage.
- `OBIS_LABELS` dict (eventually, once API provides normalised names).

### Added

- `homeassistant.components.recorder.statistics`: `async_add_external_statistics`,
  `statistics_during_period`, `StatisticMetaData`, `StatisticData`.

### Config entry data

- `watermarks` dict stays (delta sync watermark per meter).
- `last_fetched` key from older installs is ignored (harmless, not cleaned up).

## Testing Strategy

### API tests (`test_api.py`)

Already updated for `MeterDataResult` and `updated_since`. No changes needed.

### Coordinator tests (`test_coordinator.py`)

- First sync: full fetch, all statistics computed from scratch with sum starting at 0.
- Normal sync: new data appended with correct anchored sums.
- Correction detection: delta sync returns records within already-imported hours →
  triggers day-level re-fetch.
- Correction recomputation: re-fetched day data produces correct anchored sums,
  upserts overwrite existing statistics.
- Watermark not advanced on statistics write failure.
- Auth error / rate limit handling (existing tests, updated for new return type).

### Sensor tests (`test_sensor.py`)

- Remove all statistics-related tests (`_async_process_readings`,
  `update_watermark` assertions, write failure tests).
- Keep: entity attributes, naming, device info, OBIS code handling.
- Add: `native_value` returns latest reading value.

### Mocking

Mock `async_add_external_statistics` and `statistics_during_period` from HA's
recorder in coordinator tests. No more mocking `async_write_historical`.
