"""Global test fixtures for energiedaten.at."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energiedaten.const import DOMAIN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations in all tests."""
    yield


@pytest.fixture
def mock_recorder_before_hass():
    """Mock the recorder component so the recorder dependency is satisfied in tests.

    The energiedaten integration declares recorder as a dependency so that
    homeassistant-historical-sensor can write long-term statistics.  The real
    recorder requires a live database connection, which is unavailable in the
    unit-test environment.  We patch its async_setup to merely initialise the
    recorder data-structures (so hass.data[DATA_RECORDER] exists) and return
    True — enough to satisfy HA's dependency check without spinning up SQLite.
    """

    async def _mock_recorder_setup(hass, config):
        from homeassistant.helpers.recorder import async_initialize_recorder

        async_initialize_recorder(hass)
        return True

    with patch(
        "homeassistant.components.recorder.async_setup",
        side_effect=_mock_recorder_setup,
    ):
        yield


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="mein-haushalt",
        data={
            "token": "test-token",
            "team_slug": "mein-haushalt",
            "meters": [
                {
                    "uuid": "meter-1",
                    "metering_point": "AT0030000000000000000000000054321",
                    "energy_direction": "consumption",
                    "label": "Wohnung",
                },
            ],
        },
    )
