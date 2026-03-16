"""API client for energiedaten.at."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """Raised on 401/403 responses."""


class TeamNotFoundError(Exception):
    """Raised on 404 for team endpoints."""


class MeterNotFoundError(Exception):
    """Raised on 404 for meter endpoints."""


class RateLimitError(Exception):
    """Raised on 429 responses."""


class EnergiedatenApiClient:
    """Async client for the energiedaten.at REST API v1."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token: str,
        team_slug: str,
    ) -> None:
        self._session = session
        self._token = token
        self._base_url = f"https://energiedaten.at/api/v1/teams/{team_slug}"

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
            raise TeamNotFoundError("Team not found")
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
    ) -> list[dict[str, Any]]:
        """Get meter readings, handling cursor pagination internally."""
        readings: list[dict[str, Any]] = []
        params: dict[str, Any] = {
            "from": from_dt.isoformat(),
            "to": to_dt.isoformat(),
            "limit": 10000,
            "order": "asc",
        }

        while True:
            data = await self._get(f"/meters/{meter_uuid}/data", params=params)
            readings.extend(data["data"])

            next_cursor = data.get("meta", {}).get("next_cursor")
            if not next_cursor:
                break
            params["cursor"] = next_cursor

        return readings
