# Changelog

All notable changes to this project will be documented in this file.

## [0.5.1] - 2026-05-25

### Fixed

- Setup no longer fails with "Unknown error occurred" on the meter-selection
  step. The `/smart-meters` response field is `metering_point_number`; the
  config flow was reading the old `metering_point` key and raising `KeyError`
  on Submit. Tests now mirror the live response shape so this doesn't
  regress.

## [0.5.0] - 2026-05-21

### Breaking

- **API path renamed** from `/api/v1/meters` to `/api/v1/smart-meters` to
  match the upstream rename on energiedaten.at. No configuration change is
  required — restart Home Assistant after upgrading. If the integration was
  failing to reach meters on the old path, this release restores it.

## [0.4.0] - 2026-05-18

### Breaking

- **Migrated to the canonical v1 API contract.** The `team_slug` segment is
  gone from the URL and from the config entry; requests now go to
  `https://energiedaten.at/api/v1/*` directly. Existing installs are
  upgraded automatically (config-entry schema v1 → v2 → v3) — no re-auth
  needed.

### Added

- `data_window` envelope handling: responses are walked via `updated_since`
  using the server's `is_truncated` / `max_updated_at` signals, so
  grid-operator backdated corrections are picked up on the next sync.

### Changed

- Terminology aligned with the upstream rename (token → key) across README,
  `strings.json`, and translations.
- README: API-key references now point at `/docs/api#tag/quickstart`;
  pricing link fixed to `/en/pricing`.

### CI

- Broadened hassfest / HACS workflow triggers so feature branches get
  validated.

## [0.3.0] - 2026-04-06

### Breaking

- **Statistics engine replaced.** Dropped `homeassistant-historical-sensor` in
  favour of direct `async_add_external_statistics()` calls. Statistic IDs change
  from entity-based (`sensor.energiedaten_...`) to external
  (`energiedaten:{metering_point}_{obis_name}`). After upgrading you will need
  to reconfigure the Energy Dashboard to select the new statistics.

### Added

- `energiedaten.reimport` service call to re-fetch all historical data on demand
  (Developer Tools → Services). Clears sync watermarks and triggers a full
  re-import; existing statistics are overwritten, not duplicated.
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
