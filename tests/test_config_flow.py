"""Tests for the energiedaten.at config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energiedaten.api import AuthenticationError
from custom_components.energiedaten.const import DOMAIN

MOCK_METERS = [
    {
        "id": "meter-1",
        "metering_point_number": "AT0030000000000000000000000054321",
        "label": "Wohnung",
        "display_name": "Wohnung",
        "energy_direction": "consumption",
        "granularity": "quarter_hour",
        "status": "connected",
        "latest_data_at": "2026-03-15T23:45:00Z",
    },
    {
        "id": "meter-2",
        "metering_point_number": "AT0030000000000000000000000054322",
        "label": "PV Anlage",
        "display_name": "PV Anlage",
        "energy_direction": "feed_in",
        "granularity": "quarter_hour",
        "status": "connected",
        "latest_data_at": "2026-03-15T23:45:00Z",
    },
    {
        "id": "meter-3",
        "metering_point_number": "AT0030000000000000000000000099999",
        "label": "Pending Meter",
        "display_name": "Pending Meter",
        "energy_direction": "consumption",
        "granularity": "quarter_hour",
        "status": "pending",
        "latest_data_at": None,
    },
]


@pytest.fixture
def mock_api():
    """Mock the API client used by the config flow."""
    with patch(
        "custom_components.energiedaten.config_flow.EnergiedatenApiClient",
    ) as mock_cls:
        client = mock_cls.return_value
        client.async_validate = AsyncMock(return_value=True)
        client.async_get_meters = AsyncMock(return_value=MOCK_METERS)
        yield client


# --- Step 1: Credentials ---


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


async def test_step_user_invalid_auth(hass: HomeAssistant, mock_api) -> None:
    """Invalid token should show error on step 1."""
    mock_api.async_validate.side_effect = AuthenticationError
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"token": "bad-token"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_step_user_cannot_connect(hass: HomeAssistant, mock_api) -> None:
    """Network error should show cannot_connect."""
    mock_api.async_validate.side_effect = OSError("Connection refused")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"token": "token"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


# --- Step 2: Meter Selection ---


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
    # New entries start at the current schema so no migration runs on them
    assert result["result"].version == 4


async def test_step_meters_only_shows_connected(hass: HomeAssistant, mock_api) -> None:
    """Step 2 should only list meters with status=connected, not pending."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"token": "token"},
    )
    # Default should be all connected meter UUIDs (2, not 3)
    schema = result["data_schema"].schema
    meters_key = next(k for k in schema if str(k) == "meters")
    default_uuids = meters_key.default()
    assert "meter-1" in default_uuids
    assert "meter-2" in default_uuids
    assert "meter-3" not in default_uuids  # pending meter excluded
    assert len(default_uuids) == 2


async def test_step_meters_handles_live_api_field_name(
    hass: HomeAssistant, mock_api
) -> None:
    """The /smart-meters response uses metering_point_number, not metering_point.

    Regression test for the v0.5.0 setup failure: when the live API returns
    `metering_point_number`, submitting the meter-selection step previously
    raised KeyError('metering_point') and surfaced as 'Unknown error occurred'.
    """
    live_shape_meters = [
        {
            "object": "smart_meter",
            "id": "meter-live-1",
            "metering_point_number": "AT0010000000000000001000099999999",
            "label": "Bezug",
            "display_name": "Bezug",
            "energy_direction": "consumption",
            "granularity": "quarter_hour",
            "status": "connected",
        },
    ]
    mock_api.async_get_meters.return_value = live_shape_meters

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
            user_input={"meters": ["meter-live-1"]},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    stored = result["data"]["meters"][0]
    assert stored["uuid"] == "meter-live-1"
    assert stored["metering_point"] == "AT0010000000000000001000099999999"
    assert stored["energy_direction"] == "consumption"


# --- Reconfigure ---


def _make_reconfigure_entry() -> MockConfigEntry:
    """Build a v3 config entry as it would look after the original setup."""
    return MockConfigEntry(
        domain=DOMAIN,
        version=3,
        title="energiedaten.at",
        data={
            "token": "old-token",
            "meters": [
                {
                    "uuid": "meter-1",
                    "metering_point": "AT0030000000000000000000000054321",
                    "energy_direction": "consumption",
                    "label": "Wohnung",
                },
            ],
            "cursors": {"meter-1": "Y3Vyc29yLTE"},
        },
    )


async def test_reconfigure_updates_token_and_meters(
    hass: HomeAssistant, mock_api
) -> None:
    """Submitting both steps should update the entry in place (not create)."""
    entry = _make_reconfigure_entry()
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"token": "new-token"},
    )
    assert result["step_id"] == "meters"

    with patch(
        "custom_components.energiedaten.async_setup_entry",
        return_value=True,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"meters": ["meter-1", "meter-2"]},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["token"] == "new-token"
    uuids = {m["uuid"] for m in entry.data["meters"]}
    assert uuids == {"meter-1", "meter-2"}


async def test_reconfigure_preserves_cursors(
    hass: HomeAssistant, mock_api
) -> None:
    """Reconfigure must not blow away the sync cursors."""
    entry = _make_reconfigure_entry()
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"token": "new-token"},
    )
    with patch(
        "custom_components.energiedaten.async_setup_entry",
        return_value=True,
    ):
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"meters": ["meter-1"]},
        )
        await hass.async_block_till_done()

    assert entry.data["cursors"] == {"meter-1": "Y3Vyc29yLTE"}


async def test_reconfigure_invalid_auth_shows_error(
    hass: HomeAssistant, mock_api
) -> None:
    """Bad key during reconfigure should surface invalid_auth, not crash."""
    mock_api.async_validate.side_effect = AuthenticationError
    entry = _make_reconfigure_entry()
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"token": "still-bad"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_reconfigure_meter_step_defaults_to_current_selection(
    hass: HomeAssistant, mock_api
) -> None:
    """The meter form should pre-check whichever meters are already imported."""
    entry = _make_reconfigure_entry()
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"token": "new-token"},
    )

    schema = result["data_schema"].schema
    meters_key = next(k for k in schema if str(k) == "meters")
    assert meters_key.default() == ["meter-1"]


# --- Reauth ---


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
