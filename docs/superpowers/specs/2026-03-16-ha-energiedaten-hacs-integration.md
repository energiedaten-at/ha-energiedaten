# Home Assistant HACS Integration for energiedaten.at

**Date:** 2026-03-16
**Status:** Draft
**Repo:** `energiedaten/ha-energiedaten` (GitHub, to be created)
**License:** MIT
**Support level:** Official, minimal scope, no SLA ("beta")

> **Superseded sections (2026-05-18):** the API client section, config-flow Team Slug field, and cursor-pagination description are out of date. See `docs/superpowers/plans/2026-05-18-api-keys-and-no-team-slug.md` and its companion implementation plan for the current contract. The rest of this spec still applies.

## Problem

energiedaten.at users with smart meters want to see their Austrian energy data in Home Assistant's Energy Dashboard. Currently there is no integration — users have API access but no way to connect it to HA without building something themselves.

## Target User

Home users (primarily Community plan, 1 meter free) who run Home Assistant and want their consumption and/or feed-in data in the Energy Dashboard. Must also work for Starter+ users with multiple meters.

## Constraints

- EDA data is batch/historical — delivered once daily, quarter-hourly resolution, always with delay. No real-time power data exists.
- Community plan: 1 meter, 90-day retention, 100 req/hr rate limit.
- energiedaten.at API is REST + Sanctum bearer tokens, team-scoped.
- One physical smart meter can have two metering points (Zählpunkte): consumption + feed-in.

## Non-Goals

- Real-time power monitoring (not possible with EDA data)
- Energy community (Energiegemeinschaft) data
- Write operations (creating meters, requesting consent) — read-only integration
- Cost tracking or tariff data
- Automations or services beyond the refresh button

---

## Architecture

### Approach

Standalone HACS custom integration built on the `ha-historical-sensor` library. This library provides `HistoricalSensor` and `PollUpdateMixin` for the specific pattern of importing batch historical data from a cloud API into HA's long-term statistics database.

### Why `ha-historical-sensor`

- Purpose-built for cloud-API-to-statistics import (our exact use case)
- Handles cumulative sum calculation, deduplication, and HA statistics API interaction
- Reduces code and maintenance burden for a minimal-scope integration
- If the library is abandoned, migration to raw `async_add_external_statistics` calls is straightforward — the underlying HA APIs are the same

### Repository Structure

```
ha-energiedaten/
├── README.md
├── LICENSE
├── hacs.json
├── custom_components/
│   └── energiedaten/
│       ├── __init__.py          # async_setup_entry, platform forwarding
│       ├── manifest.json        # domain, iot_class, requirements
│       ├── config_flow.py       # Token + team slug + meter selection
│       ├── const.py             # DOMAIN, defaults, API base URL
│       ├── api.py               # Async API client
│       ├── coordinator.py       # DataUpdateCoordinator (6h polling)
│       ├── sensor.py            # HistoricalSensor entities
│       ├── button.py            # Manual refresh button
│       ├── strings.json         # Config flow UI strings
│       └── translations/
│           ├── en.json
│           └── de.json
```

### `manifest.json`

```json
{
  "domain": "energiedaten",
  "name": "energiedaten.at",
  "codeowners": ["@energiedaten"],
  "config_flow": true,
  "documentation": "https://github.com/energiedaten/ha-energiedaten",
  "iot_class": "cloud_polling",
  "issue_tracker": "https://github.com/energiedaten/ha-energiedaten/issues",
  "requirements": ["ha-historical-sensor"],
  "version": "0.1.0"
}
```

### `hacs.json`

```json
{
  "name": "energiedaten.at",
  "homeassistant": "2024.1.0",
  "render_readme": true
}
```

---

## API Client (`api.py`)

Thin async wrapper around the energiedaten.at REST API v1. Uses `aiohttp` (bundled with HA). No external HTTP library.

### Class: `EnergiedatenApiClient`

```
__init__(session: aiohttp.ClientSession, token: str)
```

Base URL: `https://energiedaten.at/api/v1`

> Updated 2026-05-18: dropped `team_slug` parameter; team is derived from the API key.

### Methods

**`async_validate() -> bool`**
- `GET /meters`
- Returns `True` if 200, raises `AuthenticationError` on 401/403, raises `TeamNotFoundError` on 404.
- Used by config flow to validate credentials.

**`async_get_meters() -> list[dict]`**
- `GET /meters`
- Returns all meters. Caller filters by status.
- Response fields used: `id` (UUID), `metering_point`, `label`, `display_name`, `energy_direction`, `granularity`, `status`, `latest_data_at`.

**`async_get_meter_data(meter_uuid: str, from_dt: datetime, to_dt: datetime, updated_since: str | None = None) -> MeterDataResult`**
- `GET /meters/{meter_uuid}/data?from={from_dt}&to={to_dt}&order=asc` (and `&updated_since=…` when paging)
- Response envelope: `{ "object": "data_window", "data": [...], "is_truncated": bool, "max_updated_at": "…" }`. Server cap is 50 000 records; when `is_truncated=true`, re-request with `updated_since=<max_updated_at>` until it flips to `false`.
- `limit` and `cursor` are no longer accepted by the server (FailOnUnknownFields → 422).
- Returns a `MeterDataResult` with the flat reading list and the final `max_updated_at` watermark.

> Updated 2026-05-18: replaced cursor pagination with `updated_since` walk per API.md §7.1.

### Authentication

All requests include `Authorization: Bearer {token}` header.

**Required token abilities:** `meters:read` and `data:read`. The config flow documentation (and README) should tell users to create a token with at least these two scopes. A wildcard (`*`) token also works.

### Error Handling

- 401/403 → `AuthenticationError` (triggers HA reauth flow)
- 404 → `TeamNotFoundError` or `MeterNotFoundError`
- 429 → `RateLimitError` (log warning, retry on next cycle)
- 5xx / network errors → let `aiohttp` exceptions propagate (HA coordinator handles retry)

---

## Config Flow (`config_flow.py`)

Two-step UI wizard.

### Step 1: Credentials

| Field | Type | Required | Notes |
|---|---|---|---|
| API Key | password input | yes | Sanctum personal access key with `meters:read` and `data:read` scopes |

> Updated 2026-05-18: removed Team Slug field — team is derived from the API key.

**Validation:** Calls `async_validate()`. Errors:
- "Invalid API token" — 401/403
- "Team not found" — 404
- "Cannot connect" — network/timeout

### Step 2: Meter Selection

- Calls `async_get_meters()`, filters to `status=connected` only.
- Displays multi-select checkbox list.
- Each entry shows: `"{label} ({truncated_zaehlpunkt})" — {Consumption|Feed-in}`. If no label, just the truncated Zählpunkt.
- All connected meters pre-selected by default.
- User deselects any meters they don't want.

### Config Entry Data

```python
{
    "token": "...",
    "meters": [
        {"uuid": "abc-123", "metering_point": "AT003...", "energy_direction": "consumption"},
        {"uuid": "def-456", "metering_point": "AT003...", "energy_direction": "feed_in"},
    ]
}
```

> Updated 2026-05-18: dropped `team_slug` from stored config-entry data. v2→v3 migration strips it from existing entries.

### Re-authentication

If the API returns 401 during polling, HA's standard reauth flow is triggered — user sees a "Reconfigure" notification and re-enters their token.

### Reconfiguration

To add/remove meters, the user reconfigures the integration (standard HA pattern). No separate options flow for MVP.

---

## Sensor Design (`sensor.py`)

One `HistoricalSensor` entity per selected meter.

### Entity Naming

| Energy Direction | Entity ID | Friendly Name |
|---|---|---|
| Consumption | `sensor.energiedaten_{slug}_consumption` | `{label} Consumption` |
| Feed-in | `sensor.energiedaten_{slug}_feed_in` | `{label} Feed-in` |

`{slug}` is derived from the meter label (slugified) or last 6 digits of the Zählpunkt if no label is set.

### Sensor Attributes

| Attribute | Value | Notes |
|---|---|---|
| `device_class` | `SensorDeviceClass.ENERGY` | Required for Energy Dashboard |
| `state_class` | `SensorStateClass.TOTAL_INCREASING` | Cumulative energy counter (see note below) |
| `native_unit_of_measurement` | `kWh` | |

### Extra State Attributes

| Attribute | Example | Purpose |
|---|---|---|
| `metering_point` | `AT0030000000000000000000000054321` | Full 33-char Zählpunkt |
| `granularity` | `quarter_hour` | Data resolution |
| `data_quality` | `measured` | Quality of last reading |
| `last_data_at` | `2026-03-15T23:45:00Z` | When energiedaten.at last received data |

### HA Device Mapping

- One HA device per meter (per Zählpunkt)
- Device name: meter label if set, otherwise truncated Zählpunkt
- `DeviceInfo`:
  - `identifiers`: `{("energiedaten", meter_uuid)}`
  - `manufacturer`: `"energiedaten.at"`
  - `model`: `"Smart Meter"`
  - `configuration_url`: `"https://energiedaten.at"`

### Energy Dashboard Mapping

Users manually configure the Energy Dashboard (standard HA workflow):
- Consumption sensors → "Grid consumption" slot
- Feed-in sensors → "Return to grid" slot

### Interval vs. Cumulative Data

The energiedaten.at API returns **interval energy** — each reading is the kWh consumed/fed-in during that 15-minute period, NOT a cumulative meter counter. However, HA's Energy Dashboard expects `state_class=TOTAL_INCREASING` (a monotonically increasing counter).

The `ha-historical-sensor` library handles this conversion: it accumulates the interval readings into a running cumulative sum before writing to HA's statistics database. If the library dependency is ever removed, this conversion must be implemented manually.

### HA Statistics Resolution

HA's long-term statistics store hourly data only. Quarter-hourly readings are imported and HA aggregates them into hourly buckets automatically. Short-term state history (~10 days) retains the raw 15-min resolution for users who inspect the entity directly.

---

## Coordinator & Data Flow (`coordinator.py`)

### Initial Setup (First Run)

1. For each selected meter, make an initial data request with `from` set far in the past (e.g., `2020-01-01`). The API clamps the response to the team's retention window and returns `meta.earliest_available` in the response metadata.
2. Fetch full available history via `async_get_meter_data(from=earliest_available, to=now)`.
3. Paginate through all data (cursor-based, up to 10,000 records per page).
4. Feed readings into `ha-historical-sensor` which converts interval kWh values to cumulative sums and writes to HA statistics.
5. Store `last_fetched_timestamp` per meter UUID in the config entry data (via `hass.config_entries.async_update_entry()`).

### Ongoing Polling (Every 6 Hours)

1. For each meter, call `async_get_meter_data(from=last_fetched_timestamp, to=now)`.
2. Feed new readings into the historical sensor.
3. Update `last_fetched_timestamp`.
4. If no new data (EDA hasn't delivered yet), no-op — not an error.

### Manual Refresh (Button Press)

Same logic as ongoing poll, triggered immediately via `coordinator.async_request_refresh()`.

### Deduplication

- `ha-historical-sensor` handles deduplication — overlapping time ranges don't double-count.
- We minimize overlap by tracking `last_fetched_timestamp`, but it's safe if ranges overlap slightly.

### Error Handling

| Error | Behavior |
|---|---|
| 401 (auth expired) | Trigger HA reauth flow |
| 429 (rate limit) | Log warning, retry on next 6h cycle |
| API unreachable | HA coordinator's built-in exponential backoff |
| No new data | Silent no-op |

### State Persistence

- `last_fetched_timestamp` per meter stored in config entry data (via `hass.config_entries.async_update_entry()`), NOT in `runtime_data` (which is in-memory only and lost on restart).
- Survives HA restarts.
- If integration is removed and re-added, re-imports from scratch (HA deduplicates).

---

## Manual Refresh Button (`button.py`)

One `ButtonEntity` per integration config entry (not per meter).

| Property | Value |
|---|---|
| Entity ID | `button.energiedaten_refresh` |
| Name | "energiedaten.at Refresh" |
| Icon | `mdi:refresh` |
| Press handler | `coordinator.async_request_refresh()` |

Refreshes all meters at once — they share the same daily EDA delivery cycle.

---

## Translations

German (primary) and English. Covers:
- Config flow step titles and field labels
- Config flow error messages
- Entity names and descriptions

### `strings.json` (excerpt)

```json
{
  "config": {
    "step": {
      "user": {
        "title": "Connect to energiedaten.at",
        "data": {
          "token": "API Token",
          "team_slug": "Team Slug"
        },
        "data_description": {
          "token": "Create a token at energiedaten.at under Settings → API Tokens",
          "team_slug": "Your team identifier, visible in the app URL"
        }
      },
      "meters": {
        "title": "Select Meters",
        "data": {
          "meters": "Meters to import"
        }
      }
    },
    "error": {
      "invalid_auth": "Invalid API token",
      "team_not_found": "Team not found",
      "cannot_connect": "Cannot connect to energiedaten.at"
    }
  }
}
```

> Updated 2026-05-18: `team_slug` field and `team_not_found` error key removed from strings.

---

## Testing Strategy

Minimal for MVP:
- **Unit tests** for `api.py`: mock HTTP responses, verify parsing and pagination
- **Unit tests** for config flow: mock API client, verify step transitions and error handling
- **Integration test**: mock API, verify sensors are created with correct attributes after setup

Use `pytest` + `pytest-homeassistant-custom-component` (standard HA testing toolchain).

---

## Future Considerations (Not in MVP)

- Energy community (EC) data support
- `/v1/me` or `/v1/teams` endpoint for auto-discovery (removes team slug from config flow)
- Webhook/push delivery instead of polling (energiedaten.at already has output connections)
- Cost tracking with Austrian tariff data
- Diagnostics download for troubleshooting
- HACS default repository listing (requires community traction)
