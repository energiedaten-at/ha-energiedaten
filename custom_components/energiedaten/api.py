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


@dataclass
class MeterDataResult:
    """Result of a meter data fetch including delta-sync watermark."""

    readings: list[dict[str, Any]] = field(default_factory=list)
    max_updated_at: str | None = None


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

    async def async_validate(self) -> bool:
        """Validate credentials by calling GET /meters."""
        await self._get("/meters")
        return True

    async def async_get_meters(self) -> list[dict[str, Any]]:
        """Get all meters for the team."""
        data = await self._get("/meters")
        return data["data"]

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
