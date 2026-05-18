# API migration — drop team slug, adopt key-scoped endpoints + `data_window` pagination

> **Source of changes:** `../energiedaten` commits between `1b75a4bc` (ApiToken → ApiKey rename) and `8e849e09` (drop `?token=` query auth). See `docs/technical/API.md` in that repo for the authoritative spec.

**Goal:** Update the HACS integration to the canonical V1 API contract: team is derived from the API key, paths drop the `/teams/{slug}` segment, and `/meters/{id}/data` uses the new `data_window` envelope with record-cap pagination instead of cursors.

**Why now:** Data sync is currently broken in production. The legacy `/v1/teams/{slug}/...` routes still 308-redirect, so meter listing and validation succeed — but every `/meters/{id}/data` call returns `422 validation_error` because we send `limit=10000` and `MeterDataRequest` (with `#[FailOnUnknownFields]`) explicitly unsets `limit` from its rules. `raise_for_status()` then raises, the coordinator surfaces `UpdateFailed`, and entities go unavailable until the next 6-hour cycle, which fails again.

---

## P0 — Minimum fix to restore data sync

These three changes in `api.py` are sufficient to unblock existing users on the legacy 308-redirect path. Ship this first, separately from the team-slug cleanup if needed.

1. **Drop `params["limit"] = 10000`** in `async_get_meter_data` — `MeterDataRequest` rejects it.
2. **Read pagination markers from the top level** of the response, not `meta`:
   - `max_updated_at = data.get("max_updated_at", max_updated_at)` (was `data.get("meta", {}).get("max_updated_at", ...)`)
   - Loop condition becomes `if not data.get("is_truncated"): break` (was `if not data.get("meta", {}).get("next_cursor"): break`)
3. **Page forward with `updated_since`, not `cursor`** — when `is_truncated` is `true`, set `params["updated_since"] = max_updated_at` for the next iteration. Drop the `params["cursor"] = next_cursor` line.

Optional polish in the same patch: omit `params["order"]` when `updated_since` is set (server forces `updated_at ASC` and ignores `order` anyway). After this patch, steady-state syncs work; the broader migration below is cleanup.

---

## What changed upstream

| Change | Commit / Doc | Impact on us |
|---|---|---|
| `?token=` query auth dropped, Bearer-only | `8e849e09` | None — we already use Bearer header |
| `/api/v1/tokens` → `/api/v1/keys`; `ApiToken` → `ApiKey` | `b84fb80a`, `1b75a4bc` | Terminology only — we don't call these endpoints |
| Team derived from `api_keys.team_id`; no team segment in paths | `83347166`, API.md §3.3 | **Drop `CONF_TEAM_SLUG` everywhere** |
| Legacy `/v1/teams/{slug}/...` paths kept as 308-redirect transitional shim | `LegacyTeamUrlHandler.php` | Works today; treat as deprecated — migrate off |
| `/meters/{id}/data` envelope: `meta.next_cursor`/`meta.max_updated_at` → top-level `is_truncated`/`max_updated_at` on `"object": "data_window"` | API.md §7.1, `DataWindowTest.php` | **Rewrite pagination loop** |
| `/meters/{id}/data` rejects `limit` and `cursor` (FailOnUnknownFields → 422) | `MeterDataRequest.php` | **Stop sending `limit`; switch to `updated_since` paging** |
| Record-cap pagination: when `is_truncated=true`, re-request with `updated_since=<max_updated_at>` (default cap 50 000) | API.md §7.1 | **New pagination strategy** |
| Keys carry mandatory `expires_at` (Community: 365 days max) | API.md §3.1 | Surface this in docs; reauth flow already handles 401 |

---

## Files to change

### `custom_components/energiedaten/const.py`
- Remove `CONF_TEAM_SLUG`.

### `custom_components/energiedaten/api.py`
- `EnergiedatenApiClient.__init__` — drop `team_slug` parameter; base URL becomes `https://energiedaten.at/api/v1` (no team segment).
- `async_get_meter_data` — rewrite the pagination loop:
  - Drop `params["limit"]` and the `cursor` recursion.
  - Read `max_updated_at` and `is_truncated` from the top level (not `meta`).
  - When `is_truncated` is `true`, set `params["updated_since"] = max_updated_at` and keep looping. Stop when `is_truncated` is `false`.
  - When `updated_since` is set, omit `order` (the server forces `updated_at ASC` and ignores `order` anyway, but cleaner to omit).
  - Note: if `updated_since` is supplied on entry, `from`/`to` must still be valid — the server allows the combination per §7.1, but worth confirming behaviour on the very first sync where `updated_since` is unset.
- Error mapping: `TeamNotFoundError` becomes obsolete (no team route exists). Treat 404 on `/meters` as `AuthenticationError` or a generic `UpdateFailed`; remove the `TeamNotFoundError` class and its import from `config_flow.py`.

### `custom_components/energiedaten/config_flow.py`
- Step `user`: remove `CONF_TEAM_SLUG` from the schema; collect only the API key.
- After validation, derive a sensible entry title — call `GET /api/v1/user` (returns `{ object, id, team_id, abilities }`) or fall back to a fixed string like `"energiedaten.at"`. Don't store the slug.
- Drop `team_not_found` error path; remove `TeamNotFoundError` import.
- `async_step_reauth` / `async_step_reauth_confirm`: drop `self._team_slug` plumbing.
- Bump `VERSION` to `3`.

### `custom_components/energiedaten/__init__.py`
- `async_migrate_entry`: add a v2 → v3 step that strips `CONF_TEAM_SLUG` from `entry.data`. Watermarks stay (the new pagination uses the same `updated_since` semantics — old watermarks are still valid).
- `async_setup_entry`: drop the `team_slug=` kwarg when constructing `EnergiedatenApiClient`.

### `custom_components/energiedaten/strings.json` + `translations/*.json`
- Remove the `team_slug` field label/description from the `user` and `reauth_confirm` steps.
- Remove the `team_not_found` error message key.
- Update the description text to mention: "Create an API key in your energiedaten.at account. Keys expire — Community plan caps at 365 days; re-auth before expiry."

### `README.md`
- Update setup instructions: no team slug field; new key-creation flow.
- Mention key expiry caps per plan.

### `tests/`
- Update fixtures: API client constructor signature, base URL, response envelope shape (`data_window`, `is_truncated`, top-level `max_updated_at`).
- Add a test that exercises `is_truncated=true` → loop with `updated_since` watermark.
- Add a config-entry migration test for v2 → v3 (drops `team_slug`, preserves watermarks).
- Drop tests covering `team_not_found` errors.

### `docs/superpowers/specs/2026-03-16-ha-energiedaten-hacs-integration.md`
- Audit for `team_slug` and cursor-pagination references; update or annotate as superseded.

---

## Migration order (suggested)

1. **P0 patch (api.py only)** — drop `limit`, read `is_truncated`/`max_updated_at` from top level, page with `updated_since`. Ship as a patch release to unblock existing users. Adds the new envelope shape to tests.
2. **api.py constructor** — drop `team_slug` parameter; base URL becomes `/api/v1`.
3. **const.py + config_flow.py + strings/translations** — drop `team_slug` from the UI.
4. **__init__.py** — v2 → v3 migration that strips `team_slug` from existing entries.
5. **README + spec doc** — text updates.
6. **Release** — minor version bump, changelog entry noting the breaking config-entry change and the new key requirement.

---

## Open questions

- **First-run window vs retention:** the coordinator still passes `_HISTORY_START = 2020-01-01` on every call. With the 50 000-record cap, a fresh sync of a QH meter going back 5+ years (~175k records) **will** be truncated and the loop must handle that — verify the new pagination handles this correctly end-to-end against a real key.
- **Entry title without slug:** decide whether to call `/api/v1/user` for a friendly title (`team_id` is a UUID, not great UX) or use a fixed `"energiedaten.at"` string. A real team name would require a new endpoint or expanding `/api/v1/user`.
- **Existing entries on legacy URL:** users already configured against `/v1/teams/{slug}/...` will keep working via 308 redirects until the migration ships. The v2 → v3 migration silently drops the slug and switches to canonical paths in one step — no user action required, only a reload.
