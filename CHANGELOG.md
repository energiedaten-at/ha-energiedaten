# Changelog

All notable changes to this project will be documented in this file.

## [0.6.0] - 2026-08-04

### Breaking

- The `data_quality` sensor attribute is now a label instead of an integer:
  `measured` (1), `estimated` (2), `unreliable` (3). Templates and automations
  comparing it to a number must be updated. Unrecognised codes are passed
  through unchanged.

### Fixed

- Sensors are no longer dropped on restart. Entities were built from the last
  poll's readings, but a cursor sync returns an empty page when nothing has
  changed, so a restart at that point left the meter with no sensors and
  registered a placeholder stuck at `unavailable`. OBIS codes are now stored
  in the config entry under `obis_codes` and the sensor set is built from
  those. Statistics were not affected. Leftover placeholders are removed on
  the next start.

- A meter with no stored OBIS codes does one full history read to discover
  them, then resumes incremental sync.

### Added

- Service device for the account. Meter devices are linked to it via
  `via_device`.

### Changed

- The refresh button is attached to the account device instead of having no
  device. New installs get `button.energiedaten_at_refresh`. Existing installs
  keep `button.refresh` so automations referencing it continue to work.

## [0.5.3] - 2026-08-04

### Fixed

- **Incremental sync no longer fails after the first import** ([#3]). The API
  removed the `updated_since` parameter on 2026-05-21 in favour of an opaque
  `cursor`, and rejects it outright — so every poll after the first import
  failed and dropped the entry into "Failed setup, will retry". The
  integration now resumes via `next_cursor`. This affected every plan, not
  just Community; remove/re-add only helped because it cleared the stored
  value, buying exactly one more successful import.

  The server rejects the old parameter two different ways, and both are now
  handled: `400 bad_request` ("Either from+to or cursor is required") when
  `updated_since` is sent alone, and `422 validation_failed` ("Updated since
  ist unzulässig") when it accompanies `from`/`to`.

- A cursor the server rejects is now discarded and the sync falls back to a
  full history read, so a corrupted resume token can't wedge the integration
  until someone re-adds it by hand.

### Changed

- Config-entry schema v3 → v4. Stored `watermarks` held timestamps, which are
  not valid cursors, so they are dropped on upgrade. **The first poll after
  updating does a full history read** — expect one larger-than-usual request
  per meter, after which sync resumes incrementally. Existing statistics are
  overwritten in place, not duplicated.

- Page-cap pagination follows `next_cursor` instead of `updated_since`, so
  first imports larger than the server's 50 000-record cap complete correctly.

[#3]: https://github.com/energiedaten-at/ha-energiedaten/issues/3

## [0.5.2] - 2026-05-25

### Added

- **Reconfigure flow.** A "Reconfigure" entry on the integration card lets
  you swap the API key and/or change which meters are imported without
  removing and re-adding the integration. Delta-sync watermarks for
  meters that stay selected are preserved across the reconfigure.

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
