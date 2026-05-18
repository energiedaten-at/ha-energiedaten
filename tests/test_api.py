"""Tests for the energiedaten.at API client."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.energiedaten.api import (
    AuthenticationError,
    EnergiedatenApiClient,
    MeterDataResult,
    RateLimitError,
)


def _mock_response(status: int = 200, json_data: dict | None = None) -> AsyncMock:
    """Create a mock aiohttp response."""
    resp = AsyncMock(spec=aiohttp.ClientResponse)
    resp.status = status
    resp.json = AsyncMock(return_value=json_data or {})
    resp.raise_for_status = MagicMock()
    if status >= 400:
        resp.raise_for_status.side_effect = aiohttp.ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=status,
        )
    return resp


@pytest.fixture
def mock_session() -> AsyncMock:
    """Create a mock aiohttp session."""
    session = AsyncMock(spec=aiohttp.ClientSession)
    session.get = AsyncMock()
    return session


@pytest.fixture
def client(mock_session: AsyncMock) -> EnergiedatenApiClient:
    """Create an API client with mocked session."""
    return EnergiedatenApiClient(mock_session, "test-token")


# --- async_validate ---


async def test_validate_success(client, mock_session):
    mock_session.get.return_value = _mock_response(200, {"data": []})
    assert await client.async_validate() is True


async def test_validate_auth_error(client, mock_session):
    mock_session.get.return_value = _mock_response(401)
    with pytest.raises(AuthenticationError):
        await client.async_validate()


async def test_validate_forbidden(client, mock_session):
    mock_session.get.return_value = _mock_response(403)
    with pytest.raises(AuthenticationError):
        await client.async_validate()


async def test_validate_404_is_auth_error(client, mock_session):
    """A 404 on /meters has no team-route meaning anymore — treat as auth."""
    mock_session.get.return_value = _mock_response(404)
    with pytest.raises(AuthenticationError):
        await client.async_validate()


# --- async_get_meters ---


async def test_get_meters_returns_list(client, mock_session):
    meters = [
        {"id": "m1", "metering_point": "AT003...", "status": "connected"},
        {"id": "m2", "metering_point": "AT003...", "status": "pending"},
    ]
    mock_session.get.return_value = _mock_response(200, {"data": meters})
    result = await client.async_get_meters()
    assert result == meters
    assert len(result) == 2


# --- async_get_meter_data ---


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


async def test_get_meter_data_rate_limited(client, mock_session):
    mock_session.get.return_value = _mock_response(429)
    with pytest.raises(RateLimitError):
        await client.async_get_meter_data(
            "m1",
            datetime(2026, 3, 15, tzinfo=timezone.utc),
            datetime(2026, 3, 16, tzinfo=timezone.utc),
        )


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


async def test_validate_sends_auth_header(client, mock_session):
    mock_session.get.return_value = _mock_response(200, {"data": []})
    await client.async_validate()
    call_kwargs = mock_session.get.call_args
    headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers")
    assert headers["Authorization"] == "Bearer test-token"


async def test_client_uses_canonical_base_url(client, mock_session):
    """Base URL must be /api/v1 with no /teams/{slug} segment."""
    mock_session.get.return_value = _mock_response(200, {"data": []})
    await client.async_validate()
    called_url = mock_session.get.call_args.args[0]
    assert called_url == "https://energiedaten.at/api/v1/meters"
