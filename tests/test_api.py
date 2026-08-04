"""Tests for the energiedaten.at API client."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.energiedaten.api import (
    AuthenticationError,
    EnergiedatenApiClient,
    InvalidRequestError,
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


async def test_validate_sends_authenticated_request_to_canonical_url(
    client, mock_session
):
    """Bearer key against /api/v1 with no /teams/{slug} segment."""
    mock_session.get.return_value = _mock_response(200, {"data": []})

    assert await client.async_validate() is True

    call = mock_session.get.call_args
    assert call.args[0] == "https://energiedaten.at/api/v1/smart-meters"
    assert call.kwargs["headers"]["Authorization"] == "Bearer test-token"


@pytest.mark.parametrize("status", [401, 403, 404])
async def test_validate_rejects_bad_key(client, mock_session, status):
    """A 404 on /smart-meters has no team-route meaning anymore — treat as auth."""
    mock_session.get.return_value = _mock_response(status)
    with pytest.raises(AuthenticationError):
        await client.async_validate()


# --- async_get_meters ---


async def test_get_meters_unwraps_data_envelope(client, mock_session):
    meters = [
        {"id": "m1", "metering_point_number": "AT003...", "status": "connected"},
        {"id": "m2", "metering_point_number": "AT003...", "status": "pending"},
    ]
    mock_session.get.return_value = _mock_response(200, {"data": meters})
    assert await client.async_get_meters() == meters


# --- async_get_meter_data ---


async def test_window_read_sends_window_params_and_returns_cursor(
    client, mock_session
):
    """A first sync reads a `from`/`to` window and keeps the resume cursor."""
    readings = [
        {"timestamp": "2026-03-15T14:00:00Z", "timestamp_end": "2026-03-15T14:15:00Z", "value": 0.3},
    ]
    mock_session.get.return_value = _mock_response(
        200,
        {
            "object": "data_window",
            "data": readings,
            "is_truncated": False,
            "next_cursor": "Y3Vyc29yLTE",
        },
    )
    from_dt = datetime(2026, 3, 15, tzinfo=timezone.utc)
    to_dt = datetime(2026, 3, 16, tzinfo=timezone.utc)

    result = await client.async_get_meter_data("m1", from_dt, to_dt)

    assert mock_session.get.call_args.kwargs["params"] == {
        "from": from_dt.isoformat(),
        "to": to_dt.isoformat(),
        "order": "asc",
    }
    assert result.readings == readings
    assert result.next_cursor == "Y3Vyc29yLTE"


async def test_truncated_window_read_resumes_with_next_cursor(client, mock_session):
    """is_truncated=true must resume via next_cursor, dropping the window params.

    This is the documented "backfill then tail" bridge: the window read pins a
    low watermark, and the cursor follow-up drains the rest.
    """
    page1 = _mock_response(
        200,
        {
            "object": "data_window",
            "data": [{"timestamp": "2026-03-15T14:00:00Z", "value": 0.3}],
            "is_truncated": True,
            "next_cursor": "Y3Vyc29yLTE",
        },
    )
    page2 = _mock_response(
        200,
        {
            "object": "data_window",
            "data": [{"timestamp": "2026-03-15T14:15:00Z", "value": 0.4}],
            "is_truncated": False,
            "next_cursor": "Y3Vyc29yLTI",
        },
    )
    mock_session.get.side_effect = [page1, page2]

    result = await client.async_get_meter_data(
        "m1",
        datetime(2026, 3, 15, tzinfo=timezone.utc),
        datetime(2026, 3, 16, tzinfo=timezone.utc),
    )

    assert len(result.readings) == 2
    assert result.next_cursor == "Y3Vyc29yLTI"
    assert mock_session.get.call_count == 2
    assert mock_session.get.call_args_list[1].kwargs["params"] == {
        "cursor": "Y3Vyc29yLTE"
    }


@pytest.mark.parametrize("status", [400, 422])
async def test_rejected_request_raises_invalid_request(client, mock_session, status):
    """400/422 mean the server rejected our params — surface it as its own type.

    A malformed or stale cursor lands here, and the caller needs to tell it
    apart from a transport error to recover by dropping the cursor.
    """
    mock_session.get.return_value = _mock_response(status)
    with pytest.raises(InvalidRequestError):
        await client.async_get_meter_data("m1", cursor="stale")


async def test_get_meter_data_rate_limited(client, mock_session):
    mock_session.get.return_value = _mock_response(429)
    with pytest.raises(RateLimitError):
        await client.async_get_meter_data(
            "m1",
            datetime(2026, 3, 15, tzinfo=timezone.utc),
            datetime(2026, 3, 16, tzinfo=timezone.utc),
        )


async def test_sync_read_sends_cursor_alone(client, mock_session):
    """A cursor resume must send `cursor` and nothing else.

    `from`/`to` filter by `timestamp` even in cursor mode, which would clip out
    the late grid-operator revisions the change feed exists to deliver.
    """
    mock_session.get.return_value = _mock_response(
        200,
        {"object": "data_window", "data": [], "is_truncated": False},
    )
    await client.async_get_meter_data("m1", cursor="eyJ1IjoiMjAyNi0w")

    params = mock_session.get.call_args.kwargs["params"]
    assert params == {"cursor": "eyJ1IjoiMjAyNi0w"}


async def test_empty_response_yields_no_cursor(client, mock_session):
    """The server omits next_cursor when data is empty — callers keep their own."""
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
    assert result.next_cursor is None
