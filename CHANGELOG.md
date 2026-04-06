# Changelog

All notable changes to this project will be documented in this file.

## [0.3.0] - 2026-04-06

### Breaking

- **Statistics engine replaced.** Dropped `homeassistant-historical-sensor` in
  favour of direct `async_add_external_statistics()` calls. Statistic IDs change
  from entity-based (`sensor.energiedaten_...`) to external
  (`energiedaten:{metering_point}_{obis_name}`). After upgrading you will need
  to reconfigure the Energy Dashboard to select the new statistics.

### Added

- Automatic config entry migration (v1 → v2) clears watermarks on upgrade so
  the first sync re-fetches all available history — no manual removal needed.
- Correction detection: when the grid operator re-sends corrected readings
  (e.g. estimated → measured), affected days are automatically re-fetched and
  statistics are recomputed with correct cumulative sums.
- `native_value` on sensors — each sensor now exposes the latest quarter-hour
  reading as its state value.

### Changed

- Sensors are now pure `CoordinatorEntity + SensorEntity` (no longer inherit
  from `HistoricalSensor`). Statistics writing moved entirely to the coordinator.
- Delta sync with anchored cumulative sums: normal syncs query the recorder for
  the latest sum and accumulate forward, preventing sum corruption.

### Removed

- `homeassistant-historical-sensor` dependency.

## [0.2.2] - 2026-03-28

### Added

- Brand icon and logo from energiedaten.at.

### Fixed

- Synced pyproject.toml version.

## [0.2.1] - 2026-03-27

### Added

- One sensor per OBIS code to handle multi-series meters.

### Fixed

- Process initial coordinator data when sensor is added to HA.
- Use direction only as entity name to avoid duplication with device name.
- Skip HA state write to prevent phantom data point at import time.

## [0.2.0] - 2026-03-26

### Added

- Refresh button entity.
- Full integration entry with sensor and button platforms.
- Translations (English, German).

### Fixed

- Remove separate device for refresh button to avoid phantom third device.
- Update manifest URLs and codeowner to energiedaten-at org.

## [0.1.0] - 2026-03-25

### Added

- Initial release.
- Async API client with authentication, meter listing, and paginated data fetch.
- Config flow with team validation and meter selection.
- DataUpdateCoordinator with 6-hour polling and delta sync via `updated_since`.
- Historical energy sensor with cumulative statistics for the HA Energy Dashboard.
