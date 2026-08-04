"""API client for energiedaten.at."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """Raised on 401/403 responses."""


class MeterNotFoundError(Exception):
    """Raised on 404 for meter endpoints."""


class RateLimitError(Exception):
    """Raised on 429 responses."""


class InvalidRequestError(Exception):
    """Raised on 400/422 — the server rejected the request parameters.

    Most often a stale or malformed sync cursor. Callers recover by dropping
    the cursor and falling back to a window read.
    """


@dataclass
class MeterDataResult:
    """Result of a meter data fetch including the sync resume cursor."""

    readings: list[dict[str, Any]] = field(default_factory=list)
    next_cursor: str | None = None


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
            if "/smart-meters/" in path:
                raise MeterNotFoundError(f"Meter not found: {path}")
            # No team-scoped routes anymore; 404 on /smart-meters means the key
            # doesn't resolve to a team.
            raise AuthenticationError("Key did not resolve to a team")
        if resp.status == 429:
            raise RateLimitError("Rate limit exceeded")
        if resp.status in (400, 422):
            raise InvalidRequestError(
                f"Server rejected request to {path}: {resp.status}"
            )

        resp.raise_for_status()
        return await resp.json()

    async def async_validate(self) -> bool:
        """Validate credentials by calling GET /smart-meters."""
        await self._get("/smart-meters")
        return True

    async def async_get_meters(self) -> list[dict[str, Any]]:
        """Get all meters the API key has access to."""
        data = await self._get("/smart-meters")
        return data["data"]

    async def async_get_meter_data(
        self,
        meter_uuid: str,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        cursor: str | None = None,
    ) -> MeterDataResult:
        """Get meter readings as either a window read or a cursor sync read.

        Pass `from_dt`/`to_dt` for a window read (records in `timestamp`
        order, clamped to the plan's retention floor), or `cursor` to resume
        the change feed (records in `updated_at` order, strictly newer than
        the cursor's anchor, with no retention clamp — this is what delivers
        grid-operator revisions to readings older than the window).

        The two are mutually exclusive: `from`/`to` still filter by
        `timestamp` in cursor mode, which would clip out exactly the late
        revisions the change feed exists to deliver.

        The server caps each response at 50 000 records. When `is_truncated`
        is true, resume with the response's `next_cursor` until the server
        reports `is_truncated=false`. The caller persists the final
        `next_cursor` to resume the next sync.
        """
        readings: list[dict[str, Any]] = []
        if cursor is not None:
            params: dict[str, Any] = {"cursor": cursor}
        else:
            params = {
                "from": from_dt.isoformat(),
                "to": to_dt.isoformat(),
                "order": "asc",
            }

        next_cursor: str | None = None
        while True:
            data = await self._get(f"/smart-meters/{meter_uuid}/data", params=params)
            readings.extend(data["data"])
            next_cursor = data.get("next_cursor") or next_cursor

            if not data.get("is_truncated"):
                break

            if next_cursor is None:
                # Truncated with nothing to resume from — bail rather than
                # re-request the same page forever.
                _LOGGER.warning(
                    "Truncated response for meter %s carried no next_cursor; "
                    "returning partial data",
                    meter_uuid,
                )
                break

            # Record-cap pagination: resume the change feed past this page.
            params = {"cursor": next_cursor}

        return MeterDataResult(readings=readings, next_cursor=next_cursor)
