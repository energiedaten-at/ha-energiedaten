"""Tests for the energiedaten.at refresh button."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.energiedaten.button import EnergiedatenRefreshButton
from custom_components.energiedaten.const import DOMAIN


def test_button_belongs_to_the_account_device():
    """Without a device the button lands in the global namespace as button.refresh.

    Attaching it to the account device is what gives it an entity_id scoped to
    the integration, and what stops it showing up as an orphan in the UI.
    """
    entry = MagicMock()
    entry.entry_id = "test-entry-id"

    button = EnergiedatenRefreshButton(MagicMock(), entry)

    assert (DOMAIN, "test-entry-id") in button.device_info["identifiers"]


async def test_press_triggers_coordinator_refresh():
    """Pressing the button should call coordinator.async_request_refresh."""
    coordinator = MagicMock()
    coordinator.async_request_refresh = AsyncMock()

    entry = MagicMock()
    entry.entry_id = "test-entry-id"

    button = EnergiedatenRefreshButton(coordinator, entry)
    await button.async_press()

    coordinator.async_request_refresh.assert_called_once()
