# API migration — drop team slug, adopt key-scoped endpoints + `data_window` pagination

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update the HACS integration to the canonical V1 API contract — team is derived from the API key, paths drop `/teams/{slug}`, and `/meters/{id}/data` uses the new `data_window` envelope with record-cap pagination instead of cursors.

**Architecture:** Two-phase rollout. Phase A (Task 1) is a P0 hotfix: only `api.py` changes so production sync starts working again on the legacy 308-redirect URL. Phase B (Tasks 2–9) is the canonical migration: drop `team_slug` from the constructor / config entry / UI / docs, add a v2→v3 entry migration, and bump the manifest version.

**Tech Stack:** Python 3.12+, Home Assistant `custom_components` framework, `aiohttp` (bundled with HA), `pytest` + `pytest-homeassistant-custom-component`, `voluptuous` for config-flow schemas.

**Spec:** `docs/superpowers/plans/2026-05-18-api-keys-and-no-team-slug.md` (yes, the spec lives in `plans/` — it predates the plan/spec split). Upstream API contract: `../energiedaten/docs/technical/API.md` §7.1 (data_window envelope) and §3.3 (key-derived team).

---

## File map

| File | Action | Why |
|---|---|---|
| `custom_components/energiedaten/api.py` | Modify | New envelope shape; drop `team_slug` ctor param; canonical base URL; remove `TeamNotFoundError` |
| `custom_components/energiedaten/const.py` | Modify | Remove `CONF_TEAM_SLUG` |
| `custom_components/energiedaten/config_flow.py` | Modify | Drop `team_slug` field, drop `TeamNotFoundError` path, bump `VERSION` to 3, fix reauth plumbing |
| `custom_components/energiedaten/__init__.py` | Modify | Add v2→v3 migration that strips `team_slug`; drop `team_slug=` kwarg in client construction |
| `custom_components/energiedaten/strings.json` | Modify | Remove `team_slug` field + `team_not_found` error; update token blurb to mention key expiry |
| `custom_components/energiedaten/translations/en.json` | Modify | Mirror `strings.json` |
| `custom_components/energiedaten/translations/de.json` | Modify | Mirror `strings.json` in German |
| `custom_components/energiedaten/manifest.json` | Modify | Bump `version` to `0.4.0` |
| `README.md` | Modify | Remove team-slug step; mention key expiry caps |
| `docs/superpowers/specs/2026-03-16-ha-energiedaten-hacs-integration.md` | Modify | Annotate superseded sections |
| `tests/test_api.py` | Modify | New envelope/pagination shape; canonical base URL; drop `team_not_found` |
| `tests/test_config_flow.py` | Modify | Drop `team_slug` from inputs; drop `team_not_found` test; assert `VERSION == 3` |
| `tests/test_init.py` | Modify | Add v2→v3 migration test that strips `team_slug` and preserves watermarks |

No new files. The integration is small enough that each module already has one clear responsibility — splitting further would be premature.

---

## Migration ordering rationale

Task 1 ships independently as a patch release because it is the minimum needed to unblock existing users currently broken by `422 validation_error`. Everything from Task 2 onward is shipped as one minor release because they form a single breaking change (config-entry schema + UI strings) that must land together with the v2→v3 migration.

---

## Task 1: P0 hotfix — `data_window` envelope and `updated_since` pagination

**Files:**
- Modify: `custom_components/energiedaten/api.py:88-122`
- Modify: `tests/test_api.py:92-186`

Only `api.py` changes here. Base URL stays on the legacy `…/teams/{team_slug}` path (the server still 308-redirects it). The goal is just to stop sending the rejected `limit`/`cursor` and to read the new top-level `is_truncated`/`max_updated_at` fields.

- [ ] **Step 1: Update `test_get_meter_data_single_page` for the new envelope**

Replace the response body and the existing assertions in `tests/test_api.py`:

```python
async def test_get_meter_data_single_page(client, mock_session):
    readings = [
        {"timestamp": "2026-03-15T14:00:00Z", "timestamp_end": "2026-03-15T14:15:00Z", "value": 0.3},
    ]
    mock_session.get.return_value = _mock_response(
        200,
        {
            "object": "data_window",
            "data": readings,
            "is_truncated": False,
            "max_updated_at": "2026-03-15T15:00:00+00:00",
        },
    )
    result = await client.async_get_meter_data(
        "m1",
        datetime(2026, 3, 15, tzinfo=timezone.utc),
        datetime(2026, 3, 16, tzinfo=timezone.utc),
    )
    assert isinstance(result, MeterDataResult)
    assert result.readings == readings
    assert result.max_updated_at == "2026-03-15T15:00:00+00:00"
```

- [ ] **Step 2: Replace `test_get_meter_data_pagination` with a `updated_since` walk**

```python
async def test_get_meter_data_pagination(client, mock_session):
    """When is_truncated=true, the client re-requests with updated_since=max_updated_at."""
    page1 = _mock_response(
        200,
        {
            "object": "data_window",
            "data": [{"timestamp": "2026-03-15T14:00:00Z", "value": 0.3}],
            "is_truncated": True,
            "max_updated_at": "2026-03-15T15:00:00+00:00",
        },
    )
    page2 = _mock_response(
        200,
        {
            "object": "data_window",
            "data": [{"timestamp": "2026-03-15T14:15:00Z", "value": 0.4}],
            "is_truncated": False,
            "max_updated_at": "2026-03-15T15:15:00+00:00",
        },
    )
    mock_session.get.side_effect = [page1, page2]

    result = await client.async_get_meter_data(
        "m1",
        datetime(2026, 3, 15, tzinfo=timezone.utc),
        datetime(2026, 3, 16, tzinfo=timezone.utc),
    )

    assert len(result.readings) == 2
    assert result.max_updated_at == "2026-03-15T15:15:00+00:00"
    assert mock_session.get.call_count == 2

    second_call_params = mock_session.get.call_args_list[1].kwargs["params"]
    assert second_call_params["updated_since"] == "2026-03-15T15:00:00+00:00"
    assert "order" not in second_call_params  # server forces ASC when updated_since is set
    assert "cursor" not in second_call_params
    assert "limit" not in second_call_params
```

- [ ] **Step 3: Replace `test_get_meter_data_sends_correct_params`**

```python
async def test_get_meter_data_sends_correct_params(client, mock_session):
    mock_session.get.return_value = _mock_response(
        200,
        {"object": "data_window", "data": [], "is_truncated": False},
    )
    from_dt = datetime(2026, 3, 15, tzinfo=timezone.utc)
    to_dt = datetime(2026, 3, 16, tzinfo=timezone.utc)
    await client.async_get_meter_data("m1", from_dt, to_dt)

    params = mock_session.get.call_args.kwargs["params"]
    assert params["from"] == from_dt.isoformat()
    assert params["to"] == to_dt.isoformat()
    assert params["order"] == "asc"
    assert "limit" not in params           # MeterDataRequest rejects it
    assert "cursor" not in params          # legacy pagination
    assert "updated_since" not in params   # not supplied on entry
```

- [ ] **Step 4: Update `test_get_meter_data_sends_updated_since` to drop `order`**

```python
async def test_get_meter_data_sends_updated_since(client, mock_session):
    """When updated_since is provided, it should be sent and `order` should be omitted."""
    mock_session.get.return_value = _mock_response(
        200,
        {"object": "data_window", "data": [], "is_truncated": False},
    )
    from_dt = datetime(2026, 3, 15, tzinfo=timezone.utc)
    to_dt = datetime(2026, 3, 16, tzinfo=timezone.utc)
    await client.async_get_meter_data(
        "m1", from_dt, to_dt, updated_since="2026-03-15T12:00:00+00:00"
    )

    params = mock_session.get.call_args.kwargs["params"]
    assert params["updated_since"] == "2026-03-15T12:00:00+00:00"
    assert "order" not in params
```

- [ ] **Step 5: Update `test_get_meter_data_empty_response_has_no_watermark`**

```python
async def test_get_meter_data_empty_response_has_no_watermark(client, mock_session):
    """Empty response without max_updated_at should return None watermark."""
    mock_session.get.return_value = _mock_response(
        200,
        {"object": "data_window", "data": [], "is_truncated": False},
    )
    result = await client.async_get_meter_data(
        "m1",
        datetime(2026, 3, 15, tzinfo=timezone.utc),
        datetime(2026, 3, 16, tzinfo=timezone.utc),
    )
    assert result.readings == []
    assert result.max_updated_at is None
```

- [ ] **Step 6: Run the updated tests and confirm they FAIL**

Run: `pytest tests/test_api.py -v`
Expected: the five tests above fail (still sending `limit`, still reading `meta.next_cursor`, etc.).

- [ ] **Step 7: Rewrite `async_get_meter_data` in `api.py`**

Replace lines 88–122 of `custom_components/energiedaten/api.py` with:

```python
    async def async_get_meter_data(
        self,
        meter_uuid: str,
        from_dt: datetime,
        to_dt: datetime,
        updated_since: str | None = None,
    ) -> MeterDataResult:
        """Get meter readings, walking `data_window` pages via `updated_since`.

        The server caps each response at 50 000 records. When `is_truncated`
        is true, re-request with `updated_since=<max_updated_at>` until the
        server reports `is_truncated=false`. The caller persists the final
        `max_updated_at` as the next delta-sync watermark.
        """
        readings: list[dict[str, Any]] = []
        params: dict[str, Any] = {
            "from": from_dt.isoformat(),
            "to": to_dt.isoformat(),
            "order": "asc",
        }
        if updated_since is not None:
            params["updated_since"] = updated_since
            params.pop("order", None)  # server forces ASC when updated_since is set

        max_updated_at: str | None = None
        while True:
            data = await self._get(f"/meters/{meter_uuid}/data", params=params)
            readings.extend(data["data"])
            max_updated_at = data.get("max_updated_at", max_updated_at)

            if not data.get("is_truncated"):
                break

            # Record-cap pagination: re-request from the last seen watermark.
            params["updated_since"] = max_updated_at
            params.pop("order", None)

        return MeterDataResult(readings=readings, max_updated_at=max_updated_at)
```

- [ ] **Step 8: Run the API tests and confirm they PASS**

Run: `pytest tests/test_api.py -v`
Expected: all tests pass.

- [ ] **Step 9: Run the full suite as a regression check**

Run: `pytest -v`
Expected: all tests pass. If `tests/test_coordinator.py` or `tests/test_statistics.py` fail, they likely contain inline fixtures with the old envelope — fix them to use `"object": "data_window"`, `"is_truncated"`, top-level `"max_updated_at"`, and drop `meta.next_cursor`.

- [ ] **Step 10: Commit the P0 fix**

```bash
git add custom_components/energiedaten/api.py tests/test_api.py tests/test_coordinator.py tests/test_statistics.py
git commit -m "fix(api): adopt data_window envelope and updated_since pagination

Drop limit (server rejects it with FailOnUnknownFields), read
is_truncated/max_updated_at from the top level instead of meta,
and page forward with updated_since instead of cursor. Omit
order when updated_since is set — the server forces ASC anyway.
Unblocks data sync on the legacy /teams/{slug} 308-redirect path."
```

(Only stage the test files that actually changed.)

---

## Task 2: API client — canonical base URL, drop `team_slug`, retire `TeamNotFoundError`

**Files:**
- Modify: `custom_components/energiedaten/api.py:19-21, 39-76`
- Modify: `tests/test_api.py:11-46, 69-72, 188-194`

- [ ] **Step 1: Update the `client` fixture in `tests/test_api.py`**

```python
@pytest.fixture
def client(mock_session: AsyncMock) -> EnergiedatenApiClient:
    """Create an API client with mocked session."""
    return EnergiedatenApiClient(mock_session, "test-token")
```

(Drops the third positional `"test-team"` arg.)

- [ ] **Step 2: Replace `test_validate_team_not_found` with a 404→auth test**

Delete the existing test (`tests/test_api.py:69-72`) and add:

```python
async def test_validate_404_is_auth_error(client, mock_session):
    """A 404 on /meters has no team-route meaning anymore — treat as auth."""
    mock_session.get.return_value = _mock_response(404)
    with pytest.raises(AuthenticationError):
        await client.async_validate()
```

- [ ] **Step 3: Drop the `TeamNotFoundError` import in `tests/test_api.py`**

Change:

```python
from custom_components.energiedaten.api import (
    AuthenticationError,
    EnergiedatenApiClient,
    MeterDataResult,
    RateLimitError,
    TeamNotFoundError,
)
```

to:

```python
from custom_components.energiedaten.api import (
    AuthenticationError,
    EnergiedatenApiClient,
    MeterDataResult,
    RateLimitError,
)
```

- [ ] **Step 4: Add a URL-shape regression test**

Append to `tests/test_api.py`:

```python
async def test_client_uses_canonical_base_url(client, mock_session):
    """Base URL must be /api/v1 with no /teams/{slug} segment."""
    mock_session.get.return_value = _mock_response(200, {"data": []})
    await client.async_validate()
    called_url = mock_session.get.call_args.args[0]
    assert called_url == "https://energiedaten.at/api/v1/meters"
```

- [ ] **Step 5: Run the API tests and confirm they FAIL**

Run: `pytest tests/test_api.py -v`
Expected: fixture errors (`__init__()` gets unexpected/missing arg) plus the canonical-URL test failing.

- [ ] **Step 6: Update `EnergiedatenApiClient.__init__` and `_get`**

In `custom_components/energiedaten/api.py`, replace lines 39–76 with:

```python
class EnergiedatenApiClient:
    """Async client for the energiedaten.at REST API v1."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token: str,
    ) -> None:
        self._session = session
        self._token = token
        self._base_url = "https://energiedaten.at/api/v1"

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }

    async def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Make an authenticated GET request with common error handling."""
        url = f"{self._base_url}{path}"
        resp = await self._session.get(url, headers=self._headers, params=params)

        if resp.status in (401, 403):
            raise AuthenticationError(f"Authentication failed: {resp.status}")
        if resp.status == 404:
            if "/meters/" in path:
                raise MeterNotFoundError(f"Meter not found: {path}")
            # No team-scoped routes anymore; 404 on /meters means the key
            # doesn't resolve to a team.
            raise AuthenticationError("Key did not resolve to a team")
        if resp.status == 429:
            raise RateLimitError("Rate limit exceeded")

        resp.raise_for_status()
        return await resp.json()
```

- [ ] **Step 7: Delete the `TeamNotFoundError` class**

Remove lines 19–21 of `custom_components/energiedaten/api.py`:

```python
class TeamNotFoundError(Exception):
    """Raised on 404 for team endpoints."""

```

- [ ] **Step 8: Run the API tests and confirm they PASS**

Run: `pytest tests/test_api.py -v`
Expected: all pass.

- [ ] **Step 9: Confirm no other file still imports `TeamNotFoundError`**

Run: `grep -rn "TeamNotFoundError" custom_components tests`
Expected: only `config_flow.py` still references it (we fix that in Task 5). If anywhere else uses it, fix the import.

- [ ] **Step 10: Commit**

```bash
git add custom_components/energiedaten/api.py tests/test_api.py
git commit -m "refactor(api): use canonical /api/v1 base URL, drop team_slug

The team is now derived from the API key server-side. Constructor
loses the team_slug parameter; base URL becomes https://energiedaten.at/api/v1.
Removes TeamNotFoundError (no team route exists); a 404 on /meters
is now reported as AuthenticationError. config_flow still imports
TeamNotFoundError and will break — fixed in the next commit."
```

(The follow-up Task 3/4/5 commits will land before any release, so the temporary import break is internal-only.)

---

## Task 3: `const.py` — remove `CONF_TEAM_SLUG`

**Files:**
- Modify: `custom_components/energiedaten/const.py:8`

- [ ] **Step 1: Delete the constant**

In `custom_components/energiedaten/const.py`, remove the line:

```python
CONF_TEAM_SLUG: Final = "team_slug"
```

Resulting file:

```python
"""Constants for the energiedaten.at integration."""

from typing import Final

DOMAIN: Final = "energiedaten"

CONF_TOKEN: Final = "token"
CONF_METERS: Final = "meters"
CONF_WATERMARKS: Final = "watermarks"
```

- [ ] **Step 2: Don't commit yet**

`config_flow.py` and `__init__.py` still import `CONF_TEAM_SLUG`; committing now leaves the integration unimportable. Continue to Task 4 and Task 5 before committing.

---

## Task 4: `__init__.py` — v2→v3 migration and drop `team_slug=` kwarg

**Files:**
- Modify: `custom_components/energiedaten/__init__.py:13, 30-41, 53-63`
- Modify: `tests/test_init.py:40-82` and add a new test

- [ ] **Step 1: Write the v2→v3 migration test**

Append to `tests/test_init.py`:

```python
async def test_migrate_v2_to_v3_strips_team_slug_preserves_watermarks(
    hass: HomeAssistant,
):
    """v2→v3 removes team_slug but keeps watermarks intact."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={
            "token": "t",
            "team_slug": "mein-haushalt",
            "meters": [
                {
                    "uuid": "m1",
                    "metering_point": "AT...",
                    "energy_direction": "consumption",
                    "label": "X",
                }
            ],
            "watermarks": {"m1": "2026-03-15T14:30:00+00:00"},
        },
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)

    assert result is True
    assert entry.version == 3
    assert "team_slug" not in entry.data
    assert entry.data["token"] == "t"
    assert entry.data["meters"][0]["uuid"] == "m1"
    # Watermarks survive — new pagination uses the same `updated_since` semantics
    assert entry.data["watermarks"] == {"m1": "2026-03-15T14:30:00+00:00"}


async def test_migrate_v1_to_v3_chains_both_steps(hass: HomeAssistant):
    """A v1 entry should end up at v3 with no watermarks and no team_slug."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={
            "token": "t",
            "team_slug": "mein-haushalt",
            "meters": [],
            "watermarks": {"m1": "2026-03-15T14:30:00+00:00"},
        },
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)

    assert result is True
    assert entry.version == 3
    assert "team_slug" not in entry.data
    assert "watermarks" not in entry.data
```

- [ ] **Step 2: Update the existing reimport test to use v3 + no `team_slug`**

In `tests/test_init.py`, replace the body of `test_reimport_service_clears_watermarks`:

```python
async def test_reimport_service_clears_watermarks(
    hass: HomeAssistant, mock_recorder_before_hass
):
    """The reimport service should clear watermarks and trigger a refresh."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=3,
        data={
            "token": "t",
            "meters": [
                {
                    "uuid": "m1",
                    "metering_point": "AT...",
                    "energy_direction": "consumption",
                    "label": "X",
                }
            ],
            "watermarks": {"m1": "2026-03-15T14:30:00+00:00"},
        },
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.energiedaten.coordinator.EnergiedatenCoordinator._async_update_data",
            return_value={},
        ),
        patch(
            "custom_components.energiedaten.EnergiedatenApiClient.async_validate",
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert "watermarks" in entry.data

        await hass.services.async_call(DOMAIN, "reimport", blocking=True)
        await hass.async_block_till_done()

    assert "watermarks" not in entry.data
```

- [ ] **Step 3: Update `test_migrate_v1_to_v2_clears_watermarks` to end at v3**

Rename and update in `tests/test_init.py`:

```python
async def test_migrate_v1_clears_watermarks_on_way_to_v3(hass: HomeAssistant):
    """v1 entry: watermarks are cleared during v1→v2 and team_slug stripped during v2→v3."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={
            "token": "t",
            "team_slug": "s",
            "meters": [
                {
                    "uuid": "m1",
                    "metering_point": "AT...",
                    "energy_direction": "consumption",
                    "label": "X",
                }
            ],
            "watermarks": {"m1": "2026-03-15T14:30:00+00:00"},
        },
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)

    assert result is True
    assert entry.version == 3
    assert "watermarks" not in entry.data
    assert "team_slug" not in entry.data
    assert entry.data["token"] == "t"
```

- [ ] **Step 4: Update `test_migrate_v1_without_watermarks` to end at v3**

```python
async def test_migrate_v1_without_watermarks(hass: HomeAssistant):
    """Migration should work even if no watermarks exist yet."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={
            "token": "t",
            "team_slug": "s",
            "meters": [
                {
                    "uuid": "m1",
                    "metering_point": "AT...",
                    "energy_direction": "consumption",
                    "label": "X",
                }
            ],
        },
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)

    assert result is True
    assert entry.version == 3
    assert "team_slug" not in entry.data
```

- [ ] **Step 5: Run the init tests and confirm they FAIL**

Run: `pytest tests/test_init.py -v`
Expected: failures — migration still stops at v2 and still leaves `team_slug` in data.

- [ ] **Step 6: Rewrite the imports and migration in `__init__.py`**

In `custom_components/energiedaten/__init__.py`, change the import line 13 from:

```python
from .const import CONF_TEAM_SLUG, CONF_TOKEN, CONF_WATERMARKS, DOMAIN
```

to:

```python
from .const import CONF_TOKEN, CONF_WATERMARKS, DOMAIN

# Legacy key from <v3 config entries — kept here as a literal so const.py
# doesn't have to retain the constant. Used only by async_migrate_entry.
_LEGACY_TEAM_SLUG_KEY = "team_slug"
```

Then replace `async_migrate_entry` (lines 30–41) with:

```python
async def async_migrate_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Migrate config entry to the latest version."""
    if entry.version < 2:
        # v2: statistics moved from homeassistant-historical-sensor to
        # async_add_external_statistics with new statistic IDs.
        # Clear watermarks so the first sync re-fetches all history.
        new_data = {k: v for k, v in entry.data.items() if k != CONF_WATERMARKS}
        hass.config_entries.async_update_entry(entry, data=new_data, version=2)
    if entry.version < 3:
        # v3: team is derived from the API key; the `team_slug` field is
        # no longer collected or used. Strip it from existing entries.
        # Watermarks stay: new `updated_since` pagination uses identical semantics.
        new_data = {k: v for k, v in entry.data.items() if k != _LEGACY_TEAM_SLUG_KEY}
        hass.config_entries.async_update_entry(entry, data=new_data, version=3)
    return True
```

- [ ] **Step 7: Drop `team_slug=` from the client construction in `async_setup_entry`**

In `custom_components/energiedaten/__init__.py`, replace lines 53–63:

```python
async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnergiedatenConfigEntry,
) -> bool:
    """Set up energiedaten.at from a config entry."""
    session = async_get_clientsession(hass)
    client = EnergiedatenApiClient(
        session=session,
        token=entry.data[CONF_TOKEN],
    )
```

- [ ] **Step 8: Run the init tests and confirm they PASS**

Run: `pytest tests/test_init.py -v`
Expected: all pass.

- [ ] **Step 9: Don't commit yet**

`config_flow.py` is still broken (imports `CONF_TEAM_SLUG` and `TeamNotFoundError`). Continue to Task 5.

---

## Task 5: `config_flow.py` — drop `team_slug` field, drop `TeamNotFoundError`, bump `VERSION`

**Files:**
- Modify: `custom_components/energiedaten/config_flow.py` (entire flow)
- Modify: `tests/test_config_flow.py` (entire suite)

- [ ] **Step 1: Update the mock fixture in `tests/test_config_flow.py`**

Remove the `TeamNotFoundError` import. Change line 15 from:

```python
from custom_components.energiedaten.api import AuthenticationError, TeamNotFoundError
```

to:

```python
from custom_components.energiedaten.api import AuthenticationError
```

- [ ] **Step 2: Remove the `team_not_found` test**

Delete `test_step_user_team_not_found` (lines 106–117).

- [ ] **Step 3: Update step-1 success test to drop `team_slug` and assert title**

Replace `test_step_user_success_advances_to_meters`:

```python
async def test_step_user_success_advances_to_meters(
    hass: HomeAssistant, mock_api
) -> None:
    """Valid credentials should advance to meter selection."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"token": "valid-token"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "meters"
```

- [ ] **Step 4: Update step-1 error tests to drop `team_slug`**

In `test_step_user_invalid_auth` and `test_step_user_cannot_connect`, change `user_input={"token": "...", "team_slug": "..."}` to `user_input={"token": "..."}`.

- [ ] **Step 5: Update step-2 tests for new entry shape and title**

Replace `test_step_meters_creates_entry`:

```python
async def test_step_meters_creates_entry(hass: HomeAssistant, mock_api) -> None:
    """Selecting meters should create the config entry with a fixed title."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"token": "token"},
    )
    with patch(
        "custom_components.energiedaten.async_setup_entry",
        return_value=True,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"meters": ["meter-1"]},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "energiedaten.at"
    assert result["data"]["token"] == "token"
    assert "team_slug" not in result["data"]
    assert len(result["data"]["meters"]) == 1
    assert result["data"]["meters"][0]["uuid"] == "meter-1"
    assert result["data"]["meters"][0]["energy_direction"] == "consumption"
```

- [ ] **Step 6: Update `test_step_meters_only_shows_connected` and `test_step_meters_multiple_selection`**

In both, change `user_input={"token": "...", "team_slug": "..."}` to `user_input={"token": "..."}`.

- [ ] **Step 7: Update reauth tests**

Replace `test_reauth_flow` and `test_reauth_invalid_token`:

```python
async def test_reauth_flow(hass: HomeAssistant, mock_api) -> None:
    """Reauth flow should update token and reload."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=3,
        data={"token": "old-token", "meters": []},
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with patch(
        "custom_components.energiedaten.async_setup_entry",
        return_value=True,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"token": "new-token"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"


async def test_reauth_invalid_token(hass: HomeAssistant, mock_api) -> None:
    """Reauth with bad token should show error."""
    mock_api.async_validate.side_effect = AuthenticationError
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=3,
        data={"token": "old", "meters": []},
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"token": "still-bad"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
```

- [ ] **Step 8: Add a `VERSION` regression test**

Append to `tests/test_config_flow.py`:

```python
async def test_config_flow_version_is_3() -> None:
    """Lock the schema version so accidental downgrades fail loudly."""
    from custom_components.energiedaten.config_flow import EnergiedatenConfigFlow
    assert EnergiedatenConfigFlow.VERSION == 3
```

- [ ] **Step 9: Run the config-flow tests and confirm they FAIL**

Run: `pytest tests/test_config_flow.py -v`
Expected: many failures — current flow still requires `team_slug` and still imports `TeamNotFoundError` (which we deleted in Task 2).

- [ ] **Step 10: Rewrite `config_flow.py`**

Replace the entire contents of `custom_components/energiedaten/config_flow.py` with:

```python
"""Config flow for energiedaten.at."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import AuthenticationError, EnergiedatenApiClient
from .const import CONF_METERS, CONF_TOKEN, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Team is derived from the API key server-side; we don't know the team
# name without an extra call, so we use a fixed title. (See open question
# in the spec about adding /api/v1/user lookup for a friendlier label.)
_ENTRY_TITLE = "energiedaten.at"

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


class EnergiedatenConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for energiedaten.at integration."""

    VERSION = 3

    def __init__(self) -> None:
        """Initialize flow state."""
        self._token: str = ""
        self._meters: list[dict[str, Any]] = []

    def _create_client(self, token: str) -> EnergiedatenApiClient:
        """Create an API client using HA's shared aiohttp session."""
        session = async_get_clientsession(self.hass)
        return EnergiedatenApiClient(session, token)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: Collect API key."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._token = user_input[CONF_TOKEN]
            client = self._create_client(self._token)

            try:
                await client.async_validate()
                self._meters = await client.async_get_meters()
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during validation")
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_meters()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    async def async_step_meters(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: Select which meters to import."""
        if user_input is not None:
            selected_uuids = user_input[CONF_METERS]
            selected_meters = [
                {
                    "uuid": m["id"],
                    "metering_point": m["metering_point"],
                    "energy_direction": m["energy_direction"],
                    "label": m.get("label"),
                }
                for m in self._meters
                if m["id"] in selected_uuids
            ]
            return self.async_create_entry(
                title=_ENTRY_TITLE,
                data={
                    CONF_TOKEN: self._token,
                    CONF_METERS: selected_meters,
                },
            )

        connected = [m for m in self._meters if m["status"] == "connected"]
        options = [
            SelectOptionDict(
                value=m["id"],
                label=self._meter_display_name(m),
            )
            for m in connected
        ]
        all_uuids = [m["id"] for m in connected]

        return self.async_show_form(
            step_id="meters",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_METERS, default=all_uuids): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    @staticmethod
    def _meter_display_name(meter: dict[str, Any]) -> str:
        """Format meter display name for the selection list."""
        label = meter.get("label") or meter["metering_point"][-6:]
        direction = (
            "Consumption" if meter["energy_direction"] == "consumption" else "Feed-in"
        )
        return f"{label} ({direction})"

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication trigger."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step: Re-enter API key."""
        errors: dict[str, str] = {}

        if user_input is not None:
            client = self._create_client(user_input[CONF_TOKEN])
            try:
                await client.async_validate()
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={CONF_TOKEN: user_input[CONF_TOKEN]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
        )
```

- [ ] **Step 11: Run the config-flow tests and confirm they PASS**

Run: `pytest tests/test_config_flow.py -v`
Expected: all pass.

- [ ] **Step 12: Run the entire suite as a regression check**

Run: `pytest -v`
Expected: all pass. If `tests/test_coordinator.py`, `tests/test_button.py`, or `tests/test_sensor.py` fail because they construct entries with `team_slug` or pass `team_slug=` to the client, fix the offending fixtures (just delete the `team_slug` field / kwarg).

- [ ] **Step 13: Commit the canonical-migration code change**

```bash
git add custom_components/energiedaten/const.py \
        custom_components/energiedaten/config_flow.py \
        custom_components/energiedaten/__init__.py \
        tests/test_config_flow.py \
        tests/test_init.py \
        tests/test_coordinator.py tests/test_button.py tests/test_sensor.py
git commit -m "feat!: drop team_slug from config flow and config entry

The team is derived from the API key server-side, so the slug field
is gone from setup, reauth, and stored entry data. Adds a v2→v3
migration that strips team_slug from existing entries while keeping
watermarks intact (the new updated_since pagination uses identical
semantics). Entry title is fixed at \"energiedaten.at\"."
```

(Only stage the test files that actually changed; trim the `git add` list accordingly.)

---

## Task 6: UI strings and translations

**Files:**
- Modify: `custom_components/energiedaten/strings.json`
- Modify: `custom_components/energiedaten/translations/en.json`
- Modify: `custom_components/energiedaten/translations/de.json`

- [ ] **Step 1: Rewrite `strings.json`**

Replace the file with:

```json
{
  "config": {
    "step": {
      "user": {
        "title": "Connect to energiedaten.at",
        "data": {
          "token": "API Key"
        },
        "data_description": {
          "token": "Create an API key at energiedaten.at → Settings → API Keys (scopes: meters:read, data:read). Keys expire — Community plan caps at 365 days; re-authenticate before then."
        }
      },
      "meters": {
        "title": "Select Meters",
        "data": {
          "meters": "Meters to import"
        }
      },
      "reauth_confirm": {
        "title": "Re-authenticate",
        "data": {
          "token": "API Key"
        }
      }
    },
    "error": {
      "invalid_auth": "Invalid API key",
      "cannot_connect": "Cannot connect to energiedaten.at"
    },
    "abort": {
      "reauth_successful": "Re-authentication successful"
    }
  },
  "services": {
    "reimport": {
      "name": "Re-import data",
      "description": "Clear sync watermarks and re-fetch all historical meter data from energiedaten.at. Existing statistics are overwritten, not duplicated."
    }
  }
}
```

- [ ] **Step 2: Rewrite `translations/en.json`**

Identical content to `strings.json` above. Copy/paste verbatim.

- [ ] **Step 3: Rewrite `translations/de.json`**

```json
{
  "config": {
    "step": {
      "user": {
        "title": "Mit energiedaten.at verbinden",
        "data": {
          "token": "API-Key"
        },
        "data_description": {
          "token": "API-Key erstellen unter energiedaten.at → Einstellungen → API-Keys (Berechtigungen: meters:read, data:read). Keys laufen ab — Community-Plan maximal 365 Tage; vor Ablauf erneut authentifizieren."
        }
      },
      "meters": {
        "title": "Zähler auswählen",
        "data": {
          "meters": "Zu importierende Zähler"
        }
      },
      "reauth_confirm": {
        "title": "Erneut authentifizieren",
        "data": {
          "token": "API-Key"
        }
      }
    },
    "error": {
      "invalid_auth": "Ungültiger API-Key",
      "cannot_connect": "Verbindung zu energiedaten.at nicht möglich"
    },
    "abort": {
      "reauth_successful": "Erneute Authentifizierung erfolgreich"
    }
  },
  "services": {
    "reimport": {
      "name": "Daten neu importieren",
      "description": "Synchronisierungs-Wasserzeichen löschen und alle historischen Zählerdaten von energiedaten.at neu abrufen. Bestehende Statistiken werden überschrieben, nicht dupliziert."
    }
  }
}
```

- [ ] **Step 4: Validate JSON**

Run: `python -m json.tool custom_components/energiedaten/strings.json > /dev/null && python -m json.tool custom_components/energiedaten/translations/en.json > /dev/null && python -m json.tool custom_components/energiedaten/translations/de.json > /dev/null && echo OK`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add custom_components/energiedaten/strings.json custom_components/energiedaten/translations/en.json custom_components/energiedaten/translations/de.json
git commit -m "i18n: drop team_slug field and team_not_found error; rename token→key

Mirrors the upstream rename (ApiToken → ApiKey) and adds a note
about Community-plan 365-day key expiry to the setup blurb."
```

---

## Task 7: README — drop team-slug instructions, mention key expiry

**Files:**
- Modify: `README.md:42-66, 130-140`

- [ ] **Step 1: Update the Requirements table**

Replace the API token row in `README.md` (around line 46) with:

```markdown
| API key | With `meters:read` and `data:read` scopes ([create one here](https://energiedaten.at/settings/api-keys)). Keys expire — Community plan caps at 365 days. |
```

- [ ] **Step 2: Replace the Configuration steps**

Replace lines 62–67 of `README.md`:

```markdown
## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **energiedaten.at**
3. Enter your **API Key**
4. Select which meters to import (all connected meters are pre-selected)
```

- [ ] **Step 3: Update the Re-authentication troubleshooting line**

Replace lines 138–139 (the `**Re-authentication required?**` block):

```markdown
**Re-authentication required?**
- Your API key may have expired (Community plan caps at 365 days). Create a new one at [energiedaten.at → Settings → API Keys](https://energiedaten.at/settings/api-keys).
```

- [ ] **Step 4: Add a 0.4.0 upgrade note in Troubleshooting**

Insert this paragraph just after the existing `**Upgraded from 0.2.x?**` block:

```markdown
**Upgraded to 0.4.x from 0.3.x?**
- The team-slug field is gone — the team is now derived from your API key. Existing setups are migrated automatically on first load; no user action required.
```

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs(readme): drop team-slug setup step, document API-key expiry"
```

---

## Task 8: Spec doc — annotate superseded sections

**Files:**
- Modify: `docs/superpowers/specs/2026-03-16-ha-energiedaten-hacs-integration.md`

- [ ] **Step 1: Add a banner at the top**

Insert immediately after the `**Support level:**` line:

```markdown
> **Superseded sections (2026-05-18):** the API client section, config-flow Team Slug field, and cursor-pagination description are out of date. See `docs/superpowers/plans/2026-05-18-api-keys-and-no-team-slug.md` and its companion implementation plan for the current contract. The rest of this spec still applies.
```

- [ ] **Step 2: Annotate the `EnergiedatenApiClient` constructor signature**

Replace lines 104–108 (the `__init__` signature + base URL) with:

```markdown
```
__init__(session: aiohttp.ClientSession, token: str)
```

Base URL: `https://energiedaten.at/api/v1`

> Updated 2026-05-18: dropped `team_slug` parameter; team is derived from the API key.
```

- [ ] **Step 3: Annotate the meter-data method**

Replace lines 122–128 (the `async_get_meter_data` description) with:

```markdown
**`async_get_meter_data(meter_uuid: str, from_dt: datetime, to_dt: datetime, updated_since: str | None = None) -> MeterDataResult`**
- `GET /meters/{meter_uuid}/data?from={from_dt}&to={to_dt}&order=asc` (and `&updated_since=…` when paging)
- Response envelope: `{ "object": "data_window", "data": [...], "is_truncated": bool, "max_updated_at": "…" }`. Server cap is 50 000 records; when `is_truncated=true`, re-request with `updated_since=<max_updated_at>` until it flips to `false`.
- `limit` and `cursor` are no longer accepted by the server (FailOnUnknownFields → 422).
- Returns a `MeterDataResult` with the flat reading list and the final `max_updated_at` watermark.

> Updated 2026-05-18: replaced cursor pagination with `updated_since` walk per API.md §7.1.
```

- [ ] **Step 4: Annotate the config-flow Credentials table**

Replace lines 148–153 (the "Step 1: Credentials" table) with:

```markdown
| Field | Type | Required | Notes |
|---|---|---|---|
| API Key | password input | yes | Sanctum personal access key with `meters:read` and `data:read` scopes |

> Updated 2026-05-18: removed Team Slug field — team is derived from the API key.
```

- [ ] **Step 5: Annotate the Config Entry Data example**

Replace lines 170–179 with:

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

- [ ] **Step 6: Annotate the strings.json excerpt**

In the `strings.json` excerpt block (lines ~316–345), strike through (or note) the `team_slug` data field and `team_not_found` error — both removed in 0.4.0. The simplest edit is to add a one-line note after the closing fence:

```markdown
> Updated 2026-05-18: `team_slug` field and `team_not_found` error key removed from strings.
```

- [ ] **Step 7: Commit**

```bash
git add docs/superpowers/specs/2026-03-16-ha-energiedaten-hacs-integration.md
git commit -m "docs(spec): annotate sections superseded by the 0.4.0 API migration"
```

---

## Task 9: Release — bump manifest version

**Files:**
- Modify: `custom_components/energiedaten/manifest.json:12`

- [ ] **Step 1: Bump the version**

Change line 12 of `custom_components/energiedaten/manifest.json` from `"version": "0.3.0"` to `"version": "0.4.0"`.

- [ ] **Step 2: Validate manifest**

Run: `python -m json.tool custom_components/energiedaten/manifest.json > /dev/null && echo OK`
Expected: `OK`

- [ ] **Step 3: Final regression run**

Run: `pytest -v`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add custom_components/energiedaten/manifest.json
git commit -m "chore(release): bump to 0.4.0

Breaking config-entry change: team_slug is no longer collected.
Existing v2 entries are migrated to v3 automatically on first load.
Requires re-creating any API token that was issued before the
ApiToken→ApiKey rename if it lacked an expires_at (Community plan
caps at 365 days)."
```

---

## Coverage check (self-review)

- Spec §"P0 — Minimum fix": Task 1 ✓
- Spec §"What changed upstream" → `data_window` envelope: Task 1 ✓
- Spec §"What changed upstream" → no team segment: Task 2 ✓
- Spec §"Files to change" → `api.py`: Tasks 1 & 2 ✓
- Spec §"Files to change" → `const.py`: Task 3 ✓
- Spec §"Files to change" → `config_flow.py`: Task 5 ✓
- Spec §"Files to change" → `__init__.py`: Task 4 ✓
- Spec §"Files to change" → `strings.json` + translations: Task 6 ✓
- Spec §"Files to change" → `README.md`: Task 7 ✓
- Spec §"Files to change" → spec doc: Task 8 ✓
- Spec §"Files to change" → `tests/`: covered across Tasks 1, 2, 4, 5 ✓
- Spec §"Migration order" step 6 (release): Task 9 ✓
- Spec open question on first-run truncation: explicitly addressed by the pagination loop in Task 1 step 7 (loops on `is_truncated`, advances `updated_since` to the last `max_updated_at`).
- Spec open question on entry title: resolved by using fixed `"energiedaten.at"` constant; documented in code comment in Task 5 step 10.
- Spec open question on existing legacy-URL users: resolved by v2→v3 migration in Task 4 step 6.
